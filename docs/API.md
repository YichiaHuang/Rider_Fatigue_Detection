# 資料接口（板子 ↔ 平台 ↔ 網頁）

這份是三方對接的唯一依據。板子端（`rider/`）照第 1 節發訊息，網頁照第 2 節讀資料，兩邊就能各自開發、最後直接接上。

```
板子 (i.MX93)                        筆電（平台端 server/）                 瀏覽器 (web/)
  Stage A ──MQTT 1883──▶ sources/mqtt_source ─┐
  （備援）──HTTP POST──▶ api /api/ingest ──────┼─▶ core/store ─▶ api REST + SSE ─▶ js/api/client.js
  模擬騎手 ────────────▶ sources/simulator ───┘
  stream_server :8080 ◀──────── api/board_proxy ◀──────────────── <img> /api/board/stream.mjpg
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
 "head_pitch_deg": 9.5, "inference_fps": 12.1, "reasons": ["perclos", "yawn"]}
```

`vitals`（MAX30102 PPG 心率，`rider/ppg_reader.py`）：

```json
{"timestamp": 1789746935.9, "quality": "good", "heart_rate_bpm": 72.4, "rmssd_ms": 38.0,
 "perfusion_index": 0.8, "waveform": [0.02, 0.10, 0.45, ...]}
```

- `quality`：`no_contact`（IR 低於 50000，沒貼到皮膚）｜`settling`（剛接觸，還不到 8 秒）｜`weak`（有訊號但兩種估計不一致或週期性太差）｜`good`。
- **`heart_rate_bpm` 只有在 `good` 時才有值**，其餘一律 `null`；平台端也會把非 `good` 的心率丟掉。寧可沒有數字，不顯示猜的數字。
- `rmssd_ms`：約 40 秒穩定訊號後才有，僅供參考（50 Hz 取樣、手指／耳垂量測，不是醫療級 HRV）。
- `waveform`：最近 6 秒帶通後的脈搏波，150 點、範圍 −1～1，純顯示用。最多 400 點。
- 平台端 5 秒沒收到就清掉，不會留著舊的心率。**目前心率只顯示、不參與疲勞評分。**

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
| GET | `/api/config` | 門檻、逾時秒數、圖表上限 |
| GET | `/api/state?history=600` | 完整快照：所有騎手＋歷史＋事件。連線／重連時抓一次 |
| GET | `/api/events` | **SSE 即時推送**，事件名 `rider`（單一騎手最新狀態）與 `event`（派單事件） |
| GET | `/api/riders/{id}/history?seconds=600` | 單一騎手歷史 `{"points": [[t, score], ...]}` |
| GET | `/api/sources` | 資料來源健康狀態（MQTT 有沒有連上、收了幾筆、拒絕幾筆） |
| GET | `/api/board/{id}/status` | 該騎手板子的影像服務狀態；連不上回 `{"reachable": false, "configured": true}`，沒設定影像位址回 `"configured": false`，**不會回 5xx** |
| POST | `/api/board/{id}/mode` | `{"demo": true\|false}`，轉發給那塊板子切換展示模式 |
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
  "status": "normal",          // normal | paused | unknown —— 網頁顯示用
  "detail": null,              // demo_state 的內容；5 秒沒更新自動清掉
  "vitals": null,              // vitals 的內容（心率）；同樣 5 秒沒更新自動清掉
  "history": [[1789746930.1, 11.8], ...]   // 只有 /api/state 帶
}
```

`status` 規則：`link` 不是 `online`，或 `perception` 回報故障 → `unknown`；否則跟著 `dispatch`。

派單事件：`{"seq": 7, "time": ..., "rider_id": "...", "rider_name": "...", "kind": "paused", "score": 15.2}`
`kind`：`registered`（新板子上線）、`paused`、`resumed`、`link_stale`、`link_offline`、`link_restored`、`perception_no_face`、`perception_camera_error`。

## 3. 板子影像服務（已完成，在板子上的 `rider/stream_server.py`）

| 路徑 | 說明 |
|---|---|
| `GET /status` | `{"mode": "normal"\|"demo", "viewers", "stream_fps", "quality": {"tier", "width", "jpeg_quality", "adaptive"}, "camera": {"ok", "capture_fps", ...}}` |
| `GET /stream.mjpg` | 展示模式才有，一般模式 403 |
| `POST /mode` | `{"demo": bool}`，有設 token 時要帶 `X-Token` |

平台端用 `--board rider-01=http://<板子>:8080` 指到它（每塊板子一個，可重複），token 用 `--board-token`（只存在筆電後端，不會送到瀏覽器）。沒設定影像位址的騎手照樣有分數，只是沒有畫面。

**連線慢時的行為**：板子端會自動調整畫質——觀看端收不到六成以上的畫面就降一級（640→480→320→240 寬，壓縮率同步提高），連線穩定約 12 秒後再升回去；`--no-adaptive` 可關閉。平台端不論幾個瀏覽器在看，都只向板子拉**一路**影像再分送，所以多開分頁不會互相搶頻寬。
