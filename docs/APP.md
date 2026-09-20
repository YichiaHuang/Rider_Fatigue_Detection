# 騎手 App（`app/`）：架構與部署

騎手端的外送 App：上線接單、在地圖上看取餐／送達路線、看自己的疲勞分數，分數升高時用**全螢幕畫面＋提示音＋中文語音＋震動＋系統通知**提醒；平台暫停派單時，App 上不會再出現新訂單。

它是一個 **PWA（可安裝的網頁 App）**：手機用瀏覽器打開網址，「加入主畫面」後就有圖示、全螢幕、可離線開啟，和一般 App 一樣用。選 PWA 的理由：

- iPhone 與 Android 同一份程式，**不用上架、不用編譯**（這個專案的筆電端本來就沒有 node／Android SDK）。
- 由現有的 `python3 -m server` 直接提供，後端一樣**零套件**。
- 之後要上架，同一份 `app/` 可以原封不動包成 APK（見第 5 節）。

## 1. 整體架構

```
板子 (i.MX93)                  筆電／雲端：python3 -m server                        手機
                        ┌──────────────────────────────────────────────┐
 Stage A ──MQTT───────▶ │ sources/mqtt_source ─┐                       │
                        │ sources/simulator ───┼▶ core/store ──┐       │   派單員
                        │                      │  （分數、熔斷） ├─ api ─┼──▶ web/  儀表板（SSE）
                        │ sources/order_simulator              │       │
                        │        └─ offer() ─▶ core/orders ────┘       │   騎手
                        │              （訂單、上線狀態；派單前問熔斷器） ─ api ─┼──▶ app/  騎手 App（每秒輪詢）
                        │ sources/routing ◀── 查路線（有快取）── api ◀─────┼─── 手機 GPS 位置
                        └───────┬──────────────────────────────────────┘        │
                                ▼ HTTPS                                          ▼ 直接向 OpenStreetMap 抓
                        OSRM 路線服務（連不上 → 直線估計）                        地圖圖磚
```

**派單規則**（`server/core/orders.py`，檢查放在 `OrderBook.offer()` 裡，任何訂單來源都繞不過去）：

1. **疲勞暫停**：熔斷器暫停的騎手不會收到新訂單，還沒回覆的訂單會被撤回；**已經接的單不會被收走**——先把手上這單送完，再休息。
2. **恢復要同時滿足兩件事**：至少休息 `min_rest_sec`（60 秒，`--min-rest`），**而且**分數降到 8 以下。分數可以掉得很快（騎手只要不看鏡頭就會衰減），休息時間沒辦法這樣騙。這條規則在熔斷器裡（`server/core/circuit_breaker.py`），所以儀表板和 App 看到的是同一個狀態。App 的休息卡會列出兩個條件各自的進度（倒數、目前分數）。
3. **沒有可信的偵測訊號就不派新單**（`dispatch_blocked: "no_signal"`）：偵測裝置沒連上、訊號延遲／中斷、鏡頭抓不到臉、攝影機故障。否則拔掉攝影機就成了繼續接單的方法。已經在畫面上、等待回覆的訂單不會因此撤回——騎手回頭看兩秒（抓不到臉）不該弄丟正要接的單。
4. 暫停期間躲開鏡頭沒有用：抓不到臉時板子不送分數，熔斷器停在「暫停」，要有即時的低分數才會恢復。

### 後端新增的部分（沿用原本的三層）

| 層 | 檔案 | 責任 |
|---|---|---|
| core | `server/core/orders.py` | 訂單生命週期、騎手上線狀態、疲勞／派單規則。不碰網路 |
| core | `server/core/store.py` | 多了 `rider()`、`dispatch_block()`（回傳不能派單的原因）、`record_event()` 三個讀寫口 |
| core | `server/core/circuit_breaker.py` | 暫停至少持續 `min_rest_sec` |
| sources | `server/sources/order_simulator.py` | 模擬商家訂單（含座標）。換成真實訂單來源＝再寫一個呼叫 `OrderBook.offer()` 的 Source |
| sources | `server/sources/routing.py` | 向 OSRM 查道路路線；有快取，失敗時退回直線估計並暫停重試 30 秒。換路線服務只動這個檔 |
| api | `server/api/http_server.py` | `/api/app/*` 路由，以及把 `app/` 掛在 `/app/` |

### App 前端（`app/`，原生 ES modules，分層方式和 `web/` 相同）

```
app/
  index.html               畫面骨架
  manifest.webmanifest     讓手機可以「安裝」：名稱、圖示、全螢幕
  sw.js                    service worker：離線開啟、系統通知。API 回應一律不快取
  css/tokens.css           色彩 token（與儀表板同一套）
  css/app.css              版面與元件
  js/
    main.js                只做接線：api → store → 畫面＋提醒引擎
    config.js              輪詢間隔、存在手機裡的設定（騎手、提醒開關、伺服器位址）
    api/client.js          資料層：唯一知道後端網址的檔案
    state/store.js         前端狀態
    state/alertLevel.js    normal／warning／paused／unknown 的判定（純函式）
    state/routeSync.js     什麼時候要重抓路線（訂單或階段變了、第一次拿到定位、偏離路線超過 60 公尺）
    geo/position.js        手機 GPS；只在上線或送單中開啟
    geo/rideSimulator.js   模擬騎乘：沿著路線前進的假定位（展示用，和真 GPS 二選一）
    nav/progress.js        騎手在路線上的哪裡、下一個轉彎還有多遠（純幾何，不連網）
    nav/guidance.js        什麼時候唸轉彎提示（約 300／120／30 公尺）
    alerts/                ── 提醒
      alertEngine.js       「什麼時候提醒、走哪些管道」的唯一決策點，只在狀態改變時觸發
      sound.js             提示音（Web Audio 合成，不用音檔）
      voice.js             中文語音播報
      haptics.js           震動
      notify.js            系統通知（經由 service worker）
      wakeLock.js          上線時保持螢幕不關
    screens/               login／home／alertOverlay／settings，只讀 store，動作用 callback 交回 main.js
    screens/orderMap.js    地圖（整個 App 只建立一張，訂單卡和導航畫面輪流使用）
    screens/navigation.js  全螢幕 App 內導航
    utils/text.js          所有顯示與語音文字
    utils/dom.js           DOM 小工具
  vendor/leaflet/          Leaflet 1.9.4（BSD-2 授權，直接放進 repo，不依賴 CDN）
```

### 地圖與路線

- **地圖**：Leaflet＋OpenStreetMap 圖磚，免金鑰。訂單卡裡顯示取餐點（取）、送達點（送）、騎手位置（藍點＋精度圈）。
- **路線**：App 向自己的後端要（`GET /api/app/{id}/route?from=緯度,經度`），後端再問 OSRM。走後端的理由：所有手機共用一份快取、只有一個地方知道路線服務是誰、App 維持「只跟一個後端講話」。
  - 接單前：（騎手 →）取餐點 → 送達點，讓騎手看得到這單實際要跑多遠；接單後同樣；取餐後：騎手 → 送達點。
  - 沒有 GPS（HTTP、使用者拒絕、室內收不到）時只畫取餐點 → 送達點。
  - OSRM 連不上時畫**虛線**的直線估計，文字標「直線估計」——猜的東西不能長得像真的路。
  - 顯示「到取餐點 1.2 公里・約 4 分」；時間是 OSRM 的汽車路網估計，機車實際會略有出入。
- **省流量**：狀態每秒輪詢，但路線只在訂單／階段改變、第一次拿到定位、或偏離路線超過 60 公尺（至少隔 8 秒）才重抓。沿著路線前進不需要任何請求，手機自己算。

### App 內導航

接單後按「開始導航」進入全螢幕導航：上方是**下一個轉彎**（箭頭＋距離＋「右轉 進入 光復路二段」），中間地圖跟著騎手走，下方是到這一站的剩餘距離與時間，以及「已取餐／已送達」按鈕。按「已取餐」後不用離開導航，路線自動換成往送達點。

- **為什麼要自己做**：跳到 Apple／Google 地圖時這個 App 會被放到背景，疲勞提醒送不到。在 App 內導航時，提醒的全螢幕畫面直接蓋在導航上面。外部導航仍保留成次要連結，並明寫這個代價。
- **語音**：每個轉彎最多唸三次——約 300 公尺（只有長路段才唸）、約 120 公尺「前方 120 公尺，右轉進入光復路二段」、約 30 公尺「右轉」；抵達時唸「抵達取餐點」。**疲勞提醒的語音優先**：提醒正在唸的時候導航不插話（丟掉，不排隊——晚十秒的轉彎提示比沒有更糟）。
- **偏離路線**：離路線超過 60 公尺就重新規劃，並唸「已重新規劃路線」。
- **資料來源**：後端把 OSRM 的轉彎步驟原樣（不含文字）放在路線回應的 `steps`；中文怎麼說寫在 `app/js/utils/text.js`。路線服務連不上時只有直線方向、沒有轉彎指示，畫面上會明講。
- 拖動地圖後不再自動跟隨，出現「回到我的位置」。地圖固定北方朝上（Leaflet 不能旋轉地圖）。
- **模擬騎乘**（設定 →「展示用」，或網址加 `?demo_ride=1`）：會場裡沒辦法真的騎，開啟後不讀手機 GPS，位置從陽明交大光復校區出發，按下「開始導航」後以約 65 km/h 沿路線前進，到站停下等你按「已取餐」。只要位置是模擬的，導航畫面就會一直顯示「模擬定位」。`?nav=1` 會在有訂單時自動進入導航（展示用書籤：`/app/?rider=rider-03&demo_ride=1&nav=1`）。
- **隱私**：GPS 只在上線或送單中開啟；位置只用來查路線（會經過後端送到 OSRM），後端不儲存、儀表板也看不到。
- 模擬訂單的門牌是虛構的；座標是「該路名上真實的一點」（用 OpenStreetMap 查出、並確認路線服務會把它對到同一條路）。座標若落在巷子裡幾公尺，路線服務會為了到那個點繞一公里。

### 提醒怎麼分級

| 事件 | 條件 | 畫面 | 聲音／語音 | 震動 | 通知 |
|---|---|---|---|---|---|
| 注意疲勞 | 分數 ≥ `warn_threshold`（8） | 黃色全螢幕，12 秒自動關閉 | 兩聲提示音＋一句語音 | 短 | 有 |
| 暫停派單 | 平台熔斷（≥ 15） | 紅色全螢幕，需按「我知道了」 | 警示音 12 秒內響 3 次＋語音 2 次 | 長 | 有（常駐） |
| 恢復派單 | 休息滿 60 秒**且**分數 ≤ 8 | 自動關閉提醒 | 上行提示音＋語音 | 短 | 有 |
| 偵測訊號中斷 | 板子離線／抓不到臉（上線中才提醒） | 儀表變灰並說明原因 | 一聲＋語音 | 短 | 無 |
| 新訂單 | 平台派單 | 訂單卡＋倒數 | 提示音＋唸出店名、距離、金額 | 短 | App 在背景時才有 |

- 「注意疲勞」有遲滯：要降到 8 以下才解除，分數在 10 上下晃動不會讓手機一直響。
- 騎手按下確認後會回報平台，儀表板的事件紀錄會出現「騎手已確認…」，平台端看得到提醒真的送到人了。
- 設計取捨：依 `plan.md` 的原則，**介入點是派單**，提醒是「告訴騎手發生了什麼事」。所以聲音刻意做得短、不刺耳、會自己停——在車流中嚇到人本身就是風險。每個管道都能在設定裡單獨關掉，也能試聽。

### 為什麼 App 用輪詢、不用 SSE

儀表板用 SSE；App 改成每秒抓一次 `/api/app/{id}/state`（一包小 JSON）。原因：行動網路與通道服務常會緩衝 SSE（Cloudflare 的免費臨時通道就明說不支援）；而且「多久沒收到回應」直接就是 App 需要的連線中斷判斷——連線斷了，App 會明講「疲勞提醒暫時無法送達」，不會假裝沒事。

## 2. 在本機執行

```sh
python3 -m server --simulate-real      # 沒有板子也能跑；儀表板 /，騎手 App /app/
python3 -m server --no-routing         # 會場沒有網路：不查 OSRM，路線一律畫直線估計
python3 -m server --routing-url http://localhost:5000   # 改用自己架的 OSRM
```

想看提醒，登入時選 **騎手 03**（模擬資料，約 3.5 分鐘一個循環，會經過 注意 → 暫停 → 恢復），或自己餵分數：

```sh
python3 tools/fake_rider.py --http http://localhost:8000 --rider rider-06 --wave
```

接真實板子時流程不變（`tools/connect_board.sh`），App 登入選 **騎手 01** 就是板子上的那位。

## 3. 部署到手機（展示用）

手機要能連到跑 `python3 -m server` 的那台機器。**通知、安裝到主畫面、螢幕常亮、GPS 定位這四項瀏覽器規定要 HTTPS**；提示音、語音、震動、地圖與取餐→送達路線在 HTTP 也能用。地圖圖磚由手機直接上網抓、路線由筆電上網查，兩邊都要有網路。

| | 方式 | HTTPS | 手機網路 | 適合 |
|---|---|---|---|---|
| A | Cloudflare 臨時通道 | ✓ | 任何網路（4G 也行） | **建議**：最快，功能全開 |
| B | Tailscale Funnel | ✓ | 任何網路 | **要固定網址時**（需 tailnet 管理者啟用一次） |
| C | 同一個 Wi-Fi／熱點，HTTP | ✗ | 同網段 | 現場沒網路時的備案 |

### A. Cloudflare 臨時通道（建議）

```sh
# 一次性：下載單一執行檔（WSL 裡）
curl -L -o ~/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/cloudflared

# 每次展示：伺服器開著的情況下
tools/share_app.sh          # 背景啟動通道並印出手機要開的網址；會自己監看、壞了自動換一條
tools/share_app.sh url      # 再看一次目前的網址
tools/share_app.sh stop     # 展示結束
```

不要直接跑 `cloudflared tunnel --url ...`：臨時通道的連線只要中斷幾分鐘（筆電掛在手機熱點上、熱點休眠；換 Wi-Fi；闔上筆電），Cloudflare 就把那條通道刪掉，cloudflared 會永遠卡在 `Unauthorized: Tunnel not found` 重試，手機那端一直是斷線，直到有人發現。`share_app.sh` 每 5 秒看一次通道的健康狀態，連續 45 秒沒有可用連線就換一條新的（走 TCP 的 http2，手機熱點常擋 UDP）。

**代價：換通道網址就會變**（臨時通道留不住網址），iPhone 主畫面上的圖示指的是舊網址，要刪掉重新「加入主畫面」。展示期間要避免這件事，就別讓筆電的網路中斷；要一個永遠不變的網址，用下面 B 的 Tailscale Funnel，或申請 Cloudflare 帳號做 named tunnel。

把網址做成 QR code 貼在攤位上最方便。注意：

- 這條通道會把**整個伺服器**（含儀表板、`/api/ingest`、板子模式切換）公開在網路上，而目前沒有登入機制——只在展示期間開著，結束就 Ctrl+C。
- 臨時通道不支援 SSE，所以**儀表板請照舊用筆電本機網址開**，通道只給手機 App 用。

### B. Tailscale Funnel：固定網址（手機不用裝任何東西）

Cloudflare 臨時通道每換一條網址就變，iPhone 主畫面的圖示就得重裝。Tailscale **Funnel** 給的是綁在這台筆電上的固定公開網址 `https://<筆電名>.<tailnet>.ts.net`，筆電斷網再回來網址也不變。

一次性設定（**需要 tailnet 的管理者帳號**，在瀏覽器登入 Tailscale 後台操作）：在 Windows 的 PowerShell 執行 `tailscale funnel 8008`，第一次會印出一個啟用連結（`https://login.tailscale.com/f/funnel?node=…`），用管理者帳號打開、按同意（會一併開啟 HTTPS 憑證）。

之後每次：

```powershell
tailscale funnel --bg 8008        # 背景執行；重開機後仍然有效
tailscale funnel status           # 顯示 https://<筆電名>.<tailnet>.ts.net
tailscale funnel reset            # 展示結束後關掉
```

手機開 `https://<筆電名>.<tailnet>.ts.net/app/`。

- **為什麼是 8008 不是 8000**：Funnel 只能轉給 Windows 的 `localhost`。伺服器跑在 WSL，Windows 的 `localhost:8000` 常被 VS Code 的「自動轉發連接埠」佔住，那條轉發在伺服器重啟幾次後會卡死（連線掛著不回應）。伺服器因此固定多開一個 8008（`--tunnel-port`，同一個程式、同一份資料），由 WSL 自己的轉發器處理，實測正常。
- **`tools/keep_funnel.sh`（建議展示時一直開著）**：`tailscale funnel status` 顯示「Funnel on」不代表公網真的通——筆電換過網路後，Tailscale 入口端會忘記這個節點，手機連上去 TLS 交握直接被切斷（iPhone 上看起來就是一片黑，連錯誤頁都沒有），關掉再開 Funnel 才會恢復。這個腳本每 30 秒從公網探測一次、連續失敗兩次就自動重新註冊；`check` 只探測一次。
- **在這台筆電上測 Funnel 網址不準**：MagicDNS 會把自己的主機名解析成 tailnet 內部位址，`curl https://<筆電名>.<tailnet>.ts.net/` 只是在測本機的 serve，沒經過網際網路。要測公網路徑得先用公共 DNS 查出 IP 再 `curl --resolve`（`keep_funnel.sh check` 就是這樣做），或用另一台不在 tailnet 上的裝置。
- 和 Cloudflare 通道一樣，這會把**整個伺服器**公開在網路上、沒有登入機制——只在展示期間開著。
- Funnel 支援長連線，儀表板（SSE）也能從這個網址開。
- 只想給自己人用、不想公開：把 `funnel` 換成 `serve`，但手機就得裝 Tailscale 並加入同一個 tailnet。

### C. 同一個 Wi-Fi／手機熱點（HTTP）

伺服器跑在 WSL2，區網上的手機連不到 WSL 的內部 IP，要請 Windows 轉送。**系統管理員 PowerShell**：

```powershell
$wsl = (wsl hostname -I).Trim().Split(' ')[0]
netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=$wsl
netsh advfirewall firewall add rule name="rider-app 8000" dir=in action=allow protocol=TCP localport=8000
ipconfig        # 找 Wi-Fi 介面的 IPv4，例如 172.20.10.5
```

手機開 `http://172.20.10.5:8000/app/`。WSL 重開後 IP 會變，要重做第二行（先 `netsh interface portproxy reset`）。這個模式下沒有通知、不能安裝、螢幕不會常亮；Android 可以到 `chrome://flags/#unsafely-treat-insecure-origin-as-secure` 把這個網址加進去，功能就會全開。

### 裝到手機主畫面

- **Android（Chrome）**：打開網址 → 選單 →「安裝應用程式／加到主畫面」。
- **iPhone（Safari）**：打開網址 → 分享 →「加入主畫面」。**iPhone 的通知只有從主畫面圖示開啟時才有**（iOS 16.4 以上）。
- 第一次開啟要按「開始使用」：瀏覽器規定聲音、語音、通知權限都必須由使用者點擊觸發。

### 展示前檢查

1. 手機**媒體音量**開大；iPhone 把側邊靜音鍵切到響鈴（靜音時 Web Audio 不出聲，語音通常仍會唸）。
2. 設定（右上角齒輪）→ 試聽「注意疲勞」「暫停派單」。
3. 按「上線接單」，6–14 秒內會來第一張模擬訂單。
4. App 要留在**前景**（手機架上、螢幕亮著，上線時 App 會自動保持螢幕不關）。見下一節的限制。

## 4. 已知限制

- **App 被滑掉或手機鎖屏後收不到提醒。** 現在的通知是「頁面還活著時發的本機通知」。要在 App 關閉時也叫得醒手機，需要 Web Push（VAPID 簽章要 ECDSA，Python 標準函式庫沒有，得加一個套件）或原生 App 的 FCM／APNs。外送員的手機本來就架在龍頭上開著接單畫面，所以展示與實際使用情境影響有限，但正式產品必須補上。
- **沒有登入。** 騎手是從名單上點選的，任何人都能選任何騎手。正式版要帳號登入＋每支手機一個 token，`/api/app/*` 驗證 token。
- 訂單、上線狀態、今日統計都在記憶體裡，伺服器重開就清空。
- 訂單是模擬的，沒有串任何真實外送平台；地址是虛構的。
- App 內導航是展示等級：沒有車道指引、沒有即時路況、不會因塞車改道，地圖不會隨行進方向旋轉。GPS 在高樓間漂移時可能誤判偏離而重新規劃。
- 路線用的是 OSRM 的公開示範伺服器與 OpenStreetMap 的公開圖磚：免費、無保證、有流量限制，只適合展示。正式版要自架 OSRM／圖磚或改用付費服務。
- OSRM 公開伺服器只有汽車路網：不會避開機車禁行路段，時間也不是機車的。
- iPhone 沒有震動 API；語音的音色取決於手機內建的中文語音。

## 5. 之後要變成正式產品

```
手機 App ──HTTPS──▶ 反向代理 (Caddy／Nginx，自動憑證) ──▶ python3 -m server（systemd 常駐）
板子 ────MQTT over TLS（帳密）──▶ Mosquitto（同一台雲端主機）──┘
```

1. **主機**：一台小型雲端 VM。`python3 -m server --host 127.0.0.1 --mqtt-host 127.0.0.1` 用 systemd 常駐，前面放 Caddy（兩行設定就有 HTTPS）。板子改成直接連雲端的 MQTT broker（開 TLS 與帳密），不再需要 SSH 通道。
2. **上架**：同一份 `app/` 不用改。Android 用 [PWABuilder](https://www.pwabuilder.com/) 或 Bubblewrap 包成 TWA（需要上面那個固定的 HTTPS 網址）；要用原生功能（背景推播、藍牙接安全帽）就用 Capacitor 包，這時 App 沒有自己的網域，到登入頁「進階」填平台網址即可——後端的 `/api/app/*` 已經開了 CORS。
3. **補齊**：登入與 token、Web Push／FCM、訂單與統計寫進資料庫、真實訂單來源（新增一個 Source）、自架 OSRM（用機車設定檔）與圖磚服務。
