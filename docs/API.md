# 資料接口（板子 ↔ 平台 ↔ 網頁）

這份是三方對接的唯一依據。板子端（`rider/`）照第 1 節發訊息，網頁照第 2 節讀資料，兩邊就能各自開發、最後直接接上。

```
板子 (i.MX93)                        筆電（平台端 server/）                 瀏覽器 (web/)
  Stage A ──MQTT 1883──▶ sources/mqtt_source ─┐
  （備援）──HTTP POST──▶ api /api/ingest ──────┼─▶ core/store ─▶ api REST + SSE ─▶ js/api/client.js
  模擬騎手 ────────────▶ sources/simulator ───┘
  stream_server :8080 ◀──────── api/board_proxy ◀──────────────── <img> /api/board/stream.mjpg
  模擬訂單 ────────────▶ sources/order_simulator ─▶ core/orders ─▶ api /api/app/* ─▶ 手機 (app/)，見第 4 節
```

## 1. 板子要送什麼

Topic 格式 `riders/{rider_id}/{kind}`，payload 都是 JSON，QoS 0。第一塊板子的 id 是 `rider-01`。

**多塊板子**：每塊板子用自己的 id（`rider-06`、`rider-07`…，限英數、`-`、`_`，最長 32 字）發同樣的三種訊息即可，**平台端不用改設定**——第一次收到新 id 就會自動加進儀表板並標成「真實裝置」，事件紀錄會記一筆「新裝置上線」。id 若和模擬騎手相同（例如 `rider-02`），啟動時加 `--real rider-01,rider-02` 讓模擬器讓出那個位置。上限 24 位（`max_riders`）；不想自動加入就用 `--no-auto-register`。

| kind | 何時送 | payload |
|---|---|---|
| `fatigue_score` | **固定節奏持續送（建議 1 Hz）**，不是超標才送 | `{"timestamp": 1789746935.9, "score": 12.4}` |
| `health` | 持續送（建議 1 Hz），**感知失效時也要送** | `{"timestamp": ..., "perception": "ok"}` |
| `demo_state` | **只在展示模式送**；一般模式完全不送 | 見下 |
| `vitals` | **只在展示模式送**（生理數據比分數更敏感，跟影像用同一個開關）；每秒一次 | 見下 |

- `timestamp`：板子時鐘，epoch 秒（浮點數）。
- `score`：Stage A 累積分數。平台用 `pause_threshold`（15）／`resume_threshold`（8）判斷暫停與恢復。
- `perception`：`ok`｜`no_face`｜`camera_error`。
  **偵測不到臉或攝影機壞掉時，停止送 `fatigue_score`，只送 `health`**——平台會在 5 秒後把該騎手標成「未知」，而不是讓舊的綠燈繼續亮著（plan.md §4.4）。
- `demo_state` 欄位全部選填，缺的填 `null` 或省略：

```json
{"timestamp": 1789746935.9, "ear": 0.19, "mar": 0.08, "perclos": 0.31,
 "head_pitch_deg": 9.5, "eyes_closed": true, "yawning": false,
 "inference_fps": 12.1, "reasons": ["perclos", "yawn"]}
```

`vitals`（MAX30102 PPG 心率，`rider/ppg_reader.py`）：

```json
{"timestamp": 1789746935.9, "quality": "good", "heart_rate_bpm": 72.4, "rmssd_ms": 38.0,
 "perfusion_index": 0.8, "waveform": [0.02, 0.10, 0.45, ...]}
```

- `quality`：`no_contact`（IR 低於 50000，沒貼到皮膚）｜`settling`（剛接觸，穩定訊號還不到 6 秒）｜`weak`（有訊號但還不能採信）｜`good`（已鎖定）｜`holding`（剛才鎖定過，訊號短暫不穩，沿用追蹤值，`held_sec` 是該數值的年齡，最多 10 秒）。
- **`heart_rate_bpm` 只有在 `good`／`holding` 才有值**，其餘一律 `null`。判定方式（`rider/ppg_dsp.py`，門檻用這顆感測器的真實錄音調過）：每秒的單一視窗只產生「候選值」，需同時通過灌流指數 0.15–6%、自相關與逐拍計時一致（±6 bpm）、心跳間隔規律、頻譜能量集中在該心率的諧波上（≥0.55）；**連續 5 個候選值落在 5 bpm 內才鎖定**，鎖定後接受追蹤值 ±12 bpm 內的候選值。失去接觸立即解除鎖定。
- 診斷欄位：`ir_dc`、`perfusion_index`、`autocorr`、`spectral_peak`、`sample_hz`（應為 50）、`fifo_overflows`。
- `rmssd_ms`：合併最近 60 秒內「相鄰心跳間隔的差」，滿 30 組才有值；**不需要不中斷的訊號**（中斷處不取差值、晃動前後 2.5 秒排除）。`rmssd_pairs` 是目前合併了幾組。絕對值系統性偏低約 20–25%，只適合和同一人自己的基準比較（50 Hz 取樣、手指／耳垂量測，不是醫療級 HRV）。
- `waveform`：最近 6 秒帶通後的脈搏波，150 點、範圍 −1～1，純顯示用。最多 400 點。
- 平台端 5 秒沒收到就清掉，不會留著舊的心率。
- **心率參與疲勞評分**（`rider/ppg_fatigue.py`，設計與文獻依據見 `docs/PPG_FATIGUE.md`）：板子發布的 `score` ＝ min(30, 影像 Stage A 分數 ＋ 心率加分)，心率加分 0–6、刻意低於 App 提醒線。`--no-score-ppg` 可關閉。`vitals.fatigue` 帶出指標的狀態與所有判斷依據（選填，舊板子不送）：

```json
"fatigue": {"state": "pattern",              // no_signal | learning | normal | pattern | elevated
            "baseline_progress": 1.0, "baseline_hr_bpm": 80.0, "baseline_rmssd_ms": 30.0,
            "recent_hr_bpm": 72.0, "recent_rmssd_ms": 42.0, "hr_change_pct": -10.0, "rmssd_change_pct": 40.0,
            "hr_drop_pct": 8.0, "rmssd_rise_pct": 25.0, "pattern_sec": 65, "sustain_sec": 120.0,
            "bonus": 0.0, "bonus_cap": 6.0,
            "baseline_age_sec": 1260, "baseline_restored": 1}   // 基準多久前建立；1＝沿用重啟前存檔的基準
```

  除 `state` 外都是數字或 `null`；格式不對整筆 `vitals` 會被拒絕。`demo_state` 另外多兩個選填欄位 `score_visual`（影像分數）與 `ppg_bonus`（心率加分），儀表板用來顯示分數組成；`reasons` 多一個 `ppg`。

`reasons` 目前網頁認得 `perclos`、`yawn`、`head_down`、`audio`（對照表在 `web/js/utils/format.js`），其他字串會原樣顯示。

**沒有 MQTT 時的備援**：同樣的 payload 可以直接 POST，行為完全相同（兩條路走同一個 `ingest_message()`）：

```sh
curl -X POST http://<筆電>:8000/api/ingest/rider-01/fatigue_score \
     -H 'Content-Type: application/json' -d '{"timestamp": 1789746935.9, "score": 12.4}'
```

格式錯誤回 400 並說明原因；MQTT 來的壞訊息會被丟棄並計數，可在 `GET /api/sources` 看到。

## 2. 網頁讀什麼

| 方法 | 路徑 | 用途 |
|---|---|---|
| GET | `/api/config` | 門檻（含騎手 App 的 `warn_threshold`）、逾時秒數、圖表上限 |
| GET | `/api/state?history=600` | 完整快照：所有騎手＋歷史＋事件。連線／重連時抓一次 |
| GET | `/api/events` | **SSE 即時推送**，事件名 `rider`（單一騎手最新狀態）與 `event`（派單事件） |
| GET | `/api/riders/{id}/history?seconds=600` | 單一騎手歷史 `{"points": [[t, score], ...]}` |
| GET | `/api/sources` | 資料來源健康狀態（MQTT 有沒有連上、收了幾筆、拒絕幾筆） |
| GET | `/api/board/{id}/status` | 該騎手板子的影像服務狀態；連不上回 `{"reachable": false, "configured": true}`，沒設定影像位址回 `"configured": false`，**不會回 5xx** |
| POST | `/api/board/{id}/mode` | `{"demo": true\|false}`，轉發給那塊板子切換展示模式 |
| POST | `/api/board/{id}/reset` | 展示用「重設疲勞值」：轉發給板子（`POST /reset`）把 Stage A 分數、PERCLOS 視窗、心率加分歸零；板子回 200 後平台同時解除該騎手的暫停（不必等最短休息時間），事件紀錄一筆 `score_reset`。板子連不上回 502、沒設定影像位址回 404，平台狀態都不變 |
| GET | `/api/board/{id}/stream.mjpg` | 代理那塊板子的 MJPEG；一般模式回 403 |

騎手物件（`/api/state` 的 `riders[]` 與 SSE `rider` 事件相同）：

```json
{
  "id": "rider-01", "name": "騎手 01",
  "source": "real",            // real | simulated —— 網頁一定會標示出來
  "primary": true,             // 預設放大的那一位（網頁上可點其他人切換）
  "has_board": true,           // 這位騎手有設定攝影機串流位址
  "score": 12.4, "timestamp": 1789746935.9,
  "received_at": 1789746935.95, "age_sec": 0.4,
  "link": "online",            // waiting | online | stale(>5s) | offline(>15s)
  "perception": "ok",          // ok | no_face | camera_error | unknown
  "dispatch": "normal",        // normal | paused —— 熔斷器狀態，訊號中斷時凍結
  "rest_remaining_sec": null,  // 只有 paused 時有值：最短休息時間還剩幾秒（0＝時間已滿，只差分數）
  "status": "normal",          // normal | paused | unknown —— 網頁顯示用
  "detail": null,              // demo_state 的內容；5 秒沒更新自動清掉
  "vitals": null,              // vitals 的內容（心率）；同樣 5 秒沒更新自動清掉
  "history": [[1789746930.1, 11.8], ...]   // 只有 /api/state 帶
}
```

`status` 規則：`link` 不是 `online`，或 `perception` 回報故障 → `unknown`；否則跟著 `dispatch`。

派單事件：`{"seq": 7, "time": ..., "rider_id": "...", "rider_name": "...", "kind": "paused", "score": 15.2}`
`kind`：`registered`（新板子上線）、`paused`、`resumed`、`link_stale`、`link_offline`、`link_restored`、`perception_no_face`、`perception_camera_error`；來自騎手 App 的有 `duty_on`、`duty_off`、`offer_withdrawn`（疲勞暫停時撤回待回覆的訂單）、`alert_ack_warning`、`alert_ack_paused`（騎手按了「我知道了」）。

## 3. 板子影像服務（已完成，在板子上的 `rider/stream_server.py`）

| 路徑 | 說明 |
|---|---|
| `GET /status` | `{"mode": "normal"\|"demo", "viewers", "stream_fps", "quality": {"tier", "width", "jpeg_quality", "adaptive"}, "camera": {"ok", "capture_fps", ...}}` |
| `GET /stream.mjpg` | 展示模式才有，一般模式 403 |
| `POST /mode` | `{"demo": bool}`，有設 token 時要帶 `X-Token` |

平台端用 `--board rider-01=http://<板子>:8080` 指到它（每塊板子一個，可重複），token 用 `--board-token`（只存在筆電後端，不會送到瀏覽器）。沒設定影像位址的騎手照樣有分數，只是沒有畫面。

**連線慢時的行為**：板子端會自動調整畫質——觀看端收不到六成以上的畫面就降一級（640→480→320→240 寬，壓縮率同步提高），連線穩定約 12 秒後再升回去；`--no-adaptive` 可關閉。平台端不論幾個瀏覽器在看，都只向板子拉**一路**影像再分送，所以多開分頁不會互相搶頻寬。

## 4. 騎手 App 讀寫什麼（`app/`，架構與部署見 `docs/APP.md`）

App 不用 SSE，**每秒輪詢一次狀態**（原因見 `docs/APP.md`）。這組路由有開 CORS（`Access-Control-Allow-Origin: *`），其餘路由維持同源。目前沒有登入機制。

| 方法 | 路徑 | body | 用途 |
|---|---|---|---|
| GET | `/api/app/riders` | — | 登入頁的騎手名單 `{"riders": [{"id", "name", "source", "link"}]}` |
| GET | `/api/app/{id}/state` | — | 這支手機需要的全部狀態；也當作「App 還活著」的心跳 |
| GET | `/api/app/{id}/route?from=緯度,經度` | — | 畫面上那張訂單的道路路線；`from`（騎手位置）選填。見下 |
| POST | `/api/app/{id}/duty` | `{"on": bool}` | 上線／下線。下線時撤回待回覆的訂單 |
| POST | `/api/app/{id}/offer` | `{"order_id": "o-7", "accept": bool}` | 接單／略過 |
| POST | `/api/app/{id}/advance` | `{"order_id": "o-7"}` | `accepted` → `picked_up` → `delivered` |
| POST | `/api/app/{id}/ack` | `{"level": "warning"\|"paused"}` | 騎手確認了疲勞提醒，寫進事件紀錄 |

每個 POST 成功都回**新的狀態**（和 GET state 同形狀）。格式錯誤 400；騎手不存在 404；動作和目前狀態對不上（訂單已逾時、已被撤回）回 **409**，body 為 `{"error": "...", "state": {...}}`，App 直接拿裡面的 state 更新畫面。

```json
{
  "server_time": 1789746935.9,
  "rider": { ... },                 // 和第 2 節的騎手物件完全相同
  "on_duty": true,
  "dispatch_blocked": null,         // null | "fatigue"（熔斷器暫停中）| "no_signal"（沒有可信的偵測訊號）—— 都不會派新單
  "offer": {                        // 等待回覆的訂單，沒有就是 null
    "id": "o-7", "state": "offered", "restaurant": "竹風鍋貼", "pickup": "...", "dropoff": "...",
    "items": "鍋貼 ×15、酸辣湯", "distance_km": 2.4, "fee": 59, "offered_at": 1789746930.1, "expires_in": 24.2,
    "pickup_pos": [24.7968, 120.9935], "dropoff_pos": [24.7957, 120.9920]   // [緯度, 經度]；訂單來源沒給座標就是 null，App 不顯示地圖
  },
  "order": null,                    // 進行中的訂單（state: accepted | picked_up），形狀同上，expires_in 為 null
  "last_closed": null,              // 最近一張沒接成的訂單（state: declined | expired | withdrawn），讓 App 能說明原因
  "stats": {"delivered": 3, "earnings": 187}
}
```

派單規則（`server/core/orders.py`）：

- 只派給**已上線、App 在 20 秒內輪詢過、手上沒有訂單也沒有待回覆訂單、熔斷器不是暫停、而且有可信偵測訊號（騎手 `status` 不是 `unknown`）**的騎手。
- **恢復派單要兩個條件都成立**：暫停已滿 `min_rest_sec`（預設 60 秒，`--min-rest`，在 `/api/config`），且收到一筆 ≤ `resume_threshold` 的即時分數。騎手物件的 `rest_remaining_sec` 是時間那一半的進度。
- 熔斷器暫停時：待回覆的訂單立刻撤回（`withdrawn`，事件 `offer_withdrawn`）；**已接的訂單保留**，騎手送完再休息。
- 待回覆的訂單 30 秒沒回應就逾時（`expired`）。
- 訊號中斷（`status: unknown`：連線 `waiting`／`stale`／`offline`，或 `no_face`／`camera_error`）時**不派新單**（`no_signal`），但已在畫面上待回覆的訂單保留、可以接。熔斷器本身凍結：原本暫停的仍是 `fatigue`（優先於 `no_signal`）。
- App 上的「注意疲勞」（分數 ≥ `warn_threshold`，降到 `resume_threshold` 以下才解除）是 **App 端**依 `/api/config` 算的，平台端沒有這個狀態。

### 路線 `GET /api/app/{id}/route`

對象是騎手畫面上的訂單（進行中的優先，否則是待回覆的）。途經點：`from`（有給的話）→ 取餐點 → 送達點；**已取餐且有 `from`** 時改成 `from` → 送達點。沒有帶座標的訂單回 404；`from` 格式錯誤回 400。

```json
{
  "order_id": "o-7", "order_state": "accepted",   // App 用來丟掉過期的回應
  "source": "osrm",                                // osrm | estimate（路線服務連不上：直線 ×1.3、時速 25 公里估計）
  "distance_m": 3620, "duration_s": 742,
  "legs": [{"to": "pickup", "distance_m": 1180, "duration_s": 251}, {"to": "dropoff", "distance_m": 2440, "duration_s": 491}],
  "geometry": [[24.7901, 120.9968], ...],          // [緯度, 經度]，可直接丟給 Leaflet
  "from_rider": true,                              // 路線從騎手位置出發（有帶 from）；App 只在這種路線上做導航
  "steps": [                                       // OSRM 的轉彎步驟，不含文字（App 端決定怎麼說）
    {"leg": 0, "type": "turn", "modifier": "left", "name": "光復路二段", "exit": null,
     "distance_m": 887, "location": [24.7909, 121.0045]}, ...
  ]
}
```

`steps[].type` 是 OSRM 的 maneuver type（`depart`、`turn`、`new name`、`continue`、`end of road`、`roundabout`、`arrive`…），`modifier` 是方向（`left`、`slight right`、`uturn`…），`distance_m` 是**這個動作之後**那段路的長度，`leg` 對應 `legs[]` 的索引。估計路線（`source: estimate`）每段只有 `depart`／`arrive` 兩步。

路線由 `server/sources/routing.py` 向 OSRM 查（`--routing-url`，預設公開示範伺服器；`--no-routing` 完全不上網）。座標四捨五入到小數三位（約 100 公尺）當快取鍵；查詢失敗後 30 秒內直接回估計值，不再重試。健康狀態在 `GET /api/sources` 的 `routing` 項目（查了幾次、失敗幾次）。騎手位置只用來查路線，不儲存。
