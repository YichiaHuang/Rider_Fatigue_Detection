# Rider_Fatigue_Detection — 平台端（筆電）

外送騎手疲勞風險偵測的**平台端**：接收板子送來的疲勞分數 → 派單熔斷（暫停／恢復）→ 網頁儀表板（1 位真實騎手＋4 位模擬騎手＋即時攝影機畫面）＋**騎手手機 App**（接單＋疲勞提醒）。整體計畫見 [`plan.md`](plan.md)，資料格式見 [`docs/API.md`](docs/API.md)，App 的架構與部署見 [`docs/APP.md`](docs/APP.md)，整個系統的總覽（訊號鏈、串流設計、板子輸入輸出；簡報素材）見 [`docs/SYSTEM_OVERVIEW.md`](docs/SYSTEM_OVERVIEW.md)。

板子端程式（`rider/`、`scripts/`）已併入這個資料夾，部署位置是板子的 `/home/Rider_Fatigue_Detection/`；隊友 H1 的 NXP DMS 推論程式放在 `guardian_helmet_dms/`（板子上的位置是 `/root/guardian_helmet/dms`）。

## 執行

只需要 Python 3.10+，**不用安裝任何套件**（後端只用標準函式庫，前端不用編譯）。

```sh
python3 -m server --simulate-real        # 板子沒開：連真實騎手也用模擬資料（頁面會標「模擬資料」）
python3 -m server --mqtt-host <broker>   # 正式：訂閱 MQTT，rider-01 等真實資料
python3 -m server --board rider-01=http://172.20.10.3:8080   # 指定某位騎手的影像服務位址
```

**多塊板子**：每塊板子用不同的 rider id 發 MQTT 就會自動出現在儀表板（標「真實裝置」），不用改這邊的設定；要看它的攝影機就多給一個 `--board rider-06=http://...`。頁面上點任一位騎手的卡片可以切到大畫面。

打開 <http://localhost:8000/>（儀表板）；騎手 App 在 <http://localhost:8000/app/>，手機怎麼連、怎麼裝到主畫面見 [`docs/APP.md`](docs/APP.md)。在 WSL 裡跑、用 Windows 瀏覽器看的話，`localhost` 可能不通，改用 `hostname -I` 查到的 WSL IP。

### 接真實板子（目前可用的完整流程）

```sh
# 1. 板子上（SSH 進去）：攝影機 → DMS 推論 → Stage A → MQTT，同時提供展示影像
sh /home/Rider_Fatigue_Detection/tools/start_board.sh            # 加 --demo 則一啟動就開串流

# 2. 筆電上：開 MQTT 通道 + 儀表板（一個指令）
tools/connect_board.sh                                     # 走 Tailscale
BOARD=10.199.29.167 tools/connect_board.sh                 # 板子和筆電在同一個 Wi-Fi 時，影像順很多
```

板子的 Mosquitto 只接受本機連線，所以筆電是透過 SSH 通道去訂閱的，不用動板子的系統設定。

**畫面操作**：攝影機面板右上角的「放大畫面」會把影像放到最大、其餘面板縮到右側（Esc 還原；投影用可在網址加 `?focus=1&theme=dark`）。展示模式的影像會疊上 DMS 的人臉框、468 點網格、眼睛輪廓和 Yawning／Eye／Face 狀態，沒抓到臉時顯示紅色 NO FACE，方便調鏡頭角度；不想疊圖就在板子端加 `--no-overlay`。真實騎手讀數右上角的「**重設疲勞值**」會把板子上的 Stage A 分數、PERCLOS 視窗與心率加分歸零，平台同時解除暫停（不用等最短休息時間）；展示時分數衝高後靠它回到 0，事件紀錄會留一筆。

### 目前的評分設定（板內 NXP DMS 定義 + Stage A 時間累積）

| 規則 | 條件 | 加分 | 啟動參數 |
|---|---|---|---|
| 嘴巴張開 | DMS 的 MAR 由下往上超過 **0.3**；兩次事件至少隔 **1 秒**；持續張著只算一次 | **+3／次**（2026-09-19 晚上試過 +7〔NXP GoPoint 版的哈欠權重〕與 +5，都太重，改回 +3） | `--mar-threshold` `--yawn-cooldown` |
| 閉眼比例 | DMS 判定左右眼比例**都低於 0.2** 才計為閉眼；過去 30 秒 PERCLOS 超過 **10%** | +2／秒 | `--perclos-threshold` `--perclos-window` |
| 持續低頭 | 目前只顯示、不計分（鏡頭架得低時俯角長時間偏高，實測中位數 34°，會單獨把分數推上去）；攝影機角度校準後可啟用 | 啟用後 +3／2 秒 | `--score-head-down` |
| 衰減 | 沒有規則觸發時 | **−0.3／秒** | `--decay-per-sec` |

分數上限 30；平台端 10 分 App 提醒、15 暫停新單（哈欠 +3 只是把分數往上推一點，真正跨線靠的是持續閉眼；DMS 的嘴巴線 0.3 下大笑也會算成哈欠）；**恢復要同時滿足「已休息至少 60 秒」與「分數降到 8 以下」**（`server/config.py`，`--pause` `--resume` `--min-rest`）。沒有可信偵測訊號（裝置未連線、訊號中斷、抓不到臉、攝影機故障）的騎手，App 上也不會收到新訂單。臉轉向側面超過 25°、或偵測不到臉時，分數凍結（不加也不減）。
嘴部與眼部二元判定預設採板上 `/root/guardian_helmet/dms/main.py` 的原始門檻（`--criteria dms`）。模型本身輸出特徵，疲勞分數仍由 Stage A 的持續時間和加減分規則計算。`--criteria tuned` 可換回本專案調過的判定線（兩眼平均 < 0.21、MAR > 0.4；MAR 曾試過 0.1，實測靜止時中位數 0.05、第 75 百分位 0.14，連講話都會觸發）。

```sh
sh tools/start_board.sh --demo                                        # 預設：NXP DMS 的閉眼／哈欠判定線
sh tools/start_board.sh --demo --criteria tuned                       # 本專案調過的判定線
sh tools/start_board.sh --demo --decay-per-sec 0.5                    # 範例：現場微調不用改程式
```

### 騎手 App（`app/`）

可安裝到手機主畫面的 PWA，同一個伺服器提供，不用編譯。騎手上線後會收到模擬訂單；疲勞分數 ≥ 10 先提醒（`--warn`），≥ 15 平台暫停派單時，App 以全螢幕畫面＋提示音＋中文語音＋震動＋系統通知提醒，**待回覆的訂單會被撤回、已接的單保留到送完**；騎手按「我知道了」會記進儀表板的事件紀錄。沒有板子時登入選「騎手 03」就能看到完整的 注意 → 暫停 → 恢復 循環。`--no-orders` 可關掉模擬訂單。訂單卡內有地圖與道路路線（Leaflet＋OpenStreetMap＋OSRM，免金鑰），接單後可在 **App 內逐向導航**（轉彎指示＋中文語音；疲勞提醒會直接蓋在導航上，不會因為跳去別的地圖 App 而漏掉）。會場內展示用設定裡的「模擬騎乘」。會場沒網路時加 `--no-routing`，路線改畫直線估計。

### 心率（PPG，MAX30102）

接在板子的 `/dev/i2c-0`（0x57），需要 `vexp-3v3` 服務供電；live runner 會自動偵測，沒有感測器也照常運作。心率**只在展示模式**送到平台。**心率會參與評分（多模態）**：近期心率比個人基準低 8% 以上、且 RMSSD 高 25% 以上，持續 2 分鐘後開始慢慢加分，上限 6 分（刻意低於 App 提醒線 10，單靠心率不會提醒或暫停派單）；個人基準會存檔、12 小時內重啟沿用（**換人戴要加 `--ppg-new-baseline`**）；`--no-score-ppg` 關閉、`--ppg-demo` 用短時間常數展示。設計、文獻依據與「哪些數字不是來自文獻」見 [`docs/PPG_FATIGUE.md`](docs/PPG_FATIGUE.md)。判定方式與診斷欄位見 `docs/API.md`；戴上後約 10–15 秒鎖定，短暫晃動時沿用剛才的數值最多 10 秒。**RMSSD 不需要連續不中斷的訊號**：每個通過檢查的 12 秒視窗交出「相鄰兩個心跳間隔的差」，合併最近 60 秒、滿 30 組就出值，差值絕不跨越中斷處；晃動前後 2.5 秒與超過心跳間隔 20% 的差值一律排除（否則晃動會讓 RMSSD 假性上升，正好是疲勞指標在看的方向）。live runner 執行時不要同時跑 `/root/sensors/sensor_test`（兩邊會搶感測器資料）。

沒有板子時想手動測真實資料路徑：

```sh
python3 tools/fake_rider.py --http http://localhost:8000          # 走 HTTP 備援接口
python3 tools/fake_rider.py --mqtt 127.0.0.1                      # 走 MQTT（需要 broker）
python3 tools/fake_rider.py --http http://localhost:8000 --rider rider-06 --wave   # 假裝第二塊板子，分數自己起伏
```

測試：`python3 -m unittest discover -s tests -t .`

## 檔案結構

```
server/                 後端（分三層，層與層只透過 core/store 溝通）
  config.py             所有可調參數：port、門檻、逾時、騎手名單、板子位址
  __main__.py           進入點：組裝各層
  core/                 ── 領域邏輯，不碰網路
    models.py           三種訊息的資料型別與驗證
    circuit_breaker.py  暫停／恢復（15／8，有遲滯；暫停至少持續 60 秒）
    store.py            記憶體狀態：歷史、連線狀態、未知判定、事件紀錄
    orders.py           騎手 App 的訂單生命週期、上線狀態；派單前一律先問熔斷器
  sources/              ── 資料從哪來（要加新來源只動這層）
    base.py             Source 介面＋共用的 ingest_message()
    mqtt_client.py      純標準函式庫的 MQTT 3.1.1 客戶端
    mqtt_source.py      riders/{id}/{kind} → store
    simulator.py        模擬騎手（steady / rising / yawny / dropout / demo）
    order_simulator.py  模擬商家訂單（給騎手 App，含座標）
    routing.py          App 地圖的道路路線：問 OSRM、快取、連不上時退回直線估計
  api/                  ── 對瀏覽器（只讀 store，不知道資料從哪來）
    http_server.py      REST + SSE + 靜態檔（web/ 在 /，app/ 在 /app/）＋ /api/app/*
    board_proxy.py      同源代理板子的 MJPEG 與模式切換
web/                    前端（原生 ES modules）
  css/tokens.css        色彩與字體 token（唯一有色碼的檔案，含深色模式）
  css/layout.css        版面
  css/components.css    元件樣式
  js/api/client.js      資料層：唯一知道後端網址的檔案
  js/state/store.js     前端狀態
  js/components/        header / videoPanel / primaryRider / riderCard / lineChart / eventLog / statusBadge
  js/utils/format.js    顯示文字與格式化
  js/main.js            只做接線：api → store → components
app/                    騎手手機 App（PWA，原生 ES modules；詳細檔案說明見 docs/APP.md）
  manifest.webmanifest / sw.js   可安裝、離線開啟、系統通知
  js/api  js/state  js/alerts  js/screens   資料層／狀態／提醒（聲音、語音、震動、通知）／畫面（含訂單地圖）
  js/geo                 手機 GPS（只在上線或送單中開啟）；模擬騎乘（展示用的假定位）
  js/nav                 App 內導航：路線進度計算、轉彎語音
  vendor/leaflet/        地圖函式庫（BSD-2，隨 repo 附上，不依賴 CDN）
rider/                  板子端
  live_runner.py        進入點：單一攝影機 → DMS 推論 → Stage A → MQTT/HTTP，同程序內提供展示串流
  frame_source.py       唯一開攝影機的地方（推論與串流共用）
  stream_server.py      MJPEG 串流＋一般／展示模式（自動調整畫質）
  overlay.py            展示串流上的 DMS 疊圖（人臉框、網格、狀態文字）
  ppg_reader.py         MAX30102 心率感測器讀取（暫存器設定沿用 H2 的 sensor_test.c）
  ppg_dsp.py            心率／HRV 訊號處理（純 Python；訊號不可靠時不回報數字）
  ppg_fatigue.py        PPG 疲勞指標：對個人基準看「心率降＋RMSSD 升」是否持續，產生 0–6 的加分
  （推論預設 `--model-set hybrid`：人臉偵測跑 CPU、468 點網格與虹膜跑 Ethos-U65 NPU，實測約 21 FPS；`float` 為全 CPU 約 10 FPS。依據見 docs/nxp_dms_models.md）
  stage_a_scoring.py    規則式評分（按時間累積，與幀率無關）
  layer_b_features.py   PERCLOS（按時間加權、有暖機）等特徵
  guardian_helmet_bridge.py  接 H1 的 DMS 推論
scripts/                板子端的冒煙測試、校準錄製、離線分析；benchmark_npu.py 為 CPU／NPU 實測
guardian_helmet_dms/    H1 的 NXP DMS 推論程式（備份，模型檔不在 repo）
legacy/platform_streamlit/  舊版 Streamlit 平台端，已被 server/ + web/ 取代
tests/                  單元測試＋假 broker＋HTTP 端到端
tools/fake_rider.py     假裝成板子發訊息
tools/make_app_icons.py 產生 App 的 PNG 圖示（純標準函式庫）
tools/share_app.sh      給手機一個 HTTPS 網址（Cloudflare 臨時通道），壞了自動換一條
docs/API.md             資料接口
```

## 板子開機後要接的三件事

1. **分數**：板子的 live runner 每秒發 `riders/rider-01/fatigue_score`（與 `health`），這邊用 `--mqtt-host` 指到 broker 就會顯示，網頁不用改。
2. **展示細節**：展示模式時多發 `riders/rider-01/demo_state`，EAR／MAR／PERCLOS／加分原因的欄位就會亮起來。
3. **影像**：板子跑 `python3 rider/stream_server.py`，這邊 `--board-url` 指過去；頁面上的「開啟展示模式」按鈕會直接控制它。

## 已知待辦

- 板子 repo 已有 `platform/circuit_breaker.py`，和這裡的 `server/core/circuit_breaker.py` 是同一條規則的兩份；合併 repo 時留一份。
- 門檻 15／8 是 plan.md 的起始值，Stage A 時間尺度修正後要重新校準（改 `server/config.py` 或 `--pause/--resume`）。
- 模式切換沒有登入機制，只適合展示用的區網／Tailscale。騎手 App 同樣沒有登入；App 被關掉或鎖屏時收不到提醒（需要 Web Push／原生推播），細節見 `docs/APP.md` 第 4 節。
