# Rider_Fatigue_Detection — 平台端（筆電）

外送騎手疲勞風險偵測的**平台端**：接收板子送來的疲勞分數 → 派單熔斷（暫停／恢復）→ 網頁儀表板（1 位真實騎手＋4 位模擬騎手＋即時攝影機畫面）。整體計畫見 [`plan.md`](plan.md)，三方資料格式見 [`docs/API.md`](docs/API.md)。

板子端程式（`rider/`、`scripts/`）已併入這個資料夾，部署位置是板子的 `/home/fatigue-detection/`；隊友 H1 的 NXP DMS 推論程式放在 `guardian_helmet_dms/`（板子上的位置是 `/root/guardian_helmet/dms`）。

## 執行

只需要 Python 3.10+，**不用安裝任何套件**（後端只用標準函式庫，前端不用編譯）。

```sh
python3 -m server --simulate-real        # 板子沒開：連真實騎手也用模擬資料（頁面會標「模擬資料」）
python3 -m server --mqtt-host <broker>   # 正式：訂閱 MQTT，rider-01 等真實資料
python3 -m server --board rider-01=http://172.20.10.3:8080   # 指定某位騎手的影像服務位址
```

**多塊板子**：每塊板子用不同的 rider id 發 MQTT 就會自動出現在儀表板（標「真實裝置」），不用改這邊的設定；要看它的攝影機就多給一個 `--board rider-06=http://...`。頁面上點任一位騎手的卡片可以切到大畫面。

打開 <http://localhost:8000/>。在 WSL 裡跑、用 Windows 瀏覽器看的話，`localhost` 可能不通，改用 `hostname -I` 查到的 WSL IP。

### 接真實板子（目前可用的完整流程）

```sh
# 1. 板子上（SSH 進去）：攝影機 → DMS 推論 → Stage A → MQTT，同時提供展示影像
sh /home/fatigue-detection/tools/start_board.sh            # 加 --demo 則一啟動就開串流

# 2. 筆電上：開 MQTT 通道 + 儀表板（一個指令）
tools/connect_board.sh                                     # 走 Tailscale
BOARD=10.199.29.167 tools/connect_board.sh                 # 板子和筆電在同一個 Wi-Fi 時，影像順很多
```

板子的 Mosquitto 只接受本機連線，所以筆電是透過 SSH 通道去訂閱的，不用動板子的系統設定。

**畫面操作**：攝影機面板右上角的「放大畫面」會把影像放到最大、其餘面板縮到右側（Esc 還原；投影用可在網址加 `?focus=1&theme=dark`）。展示模式的影像會疊上 DMS 的人臉框、468 點網格、眼睛輪廓和 Yawning／Eye／Face 狀態，沒抓到臉時顯示紅色 NO FACE，方便調鏡頭角度；不想疊圖就在板子端加 `--no-overlay`。

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
    circuit_breaker.py  暫停／恢復（15／8，有遲滯）
    store.py            記憶體狀態：歷史、連線狀態、未知判定、事件紀錄
  sources/              ── 資料從哪來（要加新來源只動這層）
    base.py             Source 介面＋共用的 ingest_message()
    mqtt_client.py      純標準函式庫的 MQTT 3.1.1 客戶端
    mqtt_source.py      riders/{id}/{kind} → store
    simulator.py        模擬騎手（steady / rising / yawny / dropout / demo）
  api/                  ── 對瀏覽器（只讀 store，不知道資料從哪來）
    http_server.py      REST + SSE + 靜態檔
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
rider/                  板子端
  live_runner.py        進入點：單一攝影機 → DMS 推論 → Stage A → MQTT/HTTP，同程序內提供展示串流
  frame_source.py       唯一開攝影機的地方（推論與串流共用）
  stream_server.py      MJPEG 串流＋一般／展示模式（自動調整畫質）
  overlay.py            展示串流上的 DMS 疊圖（人臉框、網格、狀態文字）
  stage_a_scoring.py    規則式評分（按時間累積，與幀率無關）
  layer_b_features.py   PERCLOS（按時間加權、有暖機）等特徵
  guardian_helmet_bridge.py  接 H1 的 DMS 推論
scripts/                板子端的冒煙測試、校準錄製、離線分析
guardian_helmet_dms/    H1 的 NXP DMS 推論程式（備份，模型檔不在 repo）
legacy/platform_streamlit/  舊版 Streamlit 平台端，已被 server/ + web/ 取代
tests/                  單元測試＋假 broker＋HTTP 端到端
tools/fake_rider.py     假裝成板子發訊息
docs/API.md             資料接口
```

## 板子開機後要接的三件事

1. **分數**：板子的 live runner 每秒發 `riders/rider-01/fatigue_score`（與 `health`），這邊用 `--mqtt-host` 指到 broker 就會顯示，網頁不用改。
2. **展示細節**：展示模式時多發 `riders/rider-01/demo_state`，EAR／MAR／PERCLOS／加分原因的欄位就會亮起來。
3. **影像**：板子跑 `python3 rider/stream_server.py`，這邊 `--board-url` 指過去；頁面上的「開啟展示模式」按鈕會直接控制它。

## 已知待辦

- 板子 repo 已有 `platform/circuit_breaker.py`，和這裡的 `server/core/circuit_breaker.py` 是同一條規則的兩份；合併 repo 時留一份。
- 門檻 15／8 是 plan.md 的起始值，Stage A 時間尺度修正後要重新校準（改 `server/config.py` 或 `--pause/--resume`）。
- 模式切換沒有登入機制，只適合展示用的區網／Tailscale。
