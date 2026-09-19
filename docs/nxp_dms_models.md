# NXP DMS 模型解析（Layer A）

## 先講清楚命名，避免混淆

- **Layer A** = NXP eIQ DMS reference design 提供的三個模型（本文件內容）。直接沿用，不訓練、不重轉模型。
- **Stage A** = 我們自己寫的規則式疲勞評分（`rider/stage_a_scoring.py`）。是純規則、leaky integrator，**沒有任何模型、沒有可學習權重**。
- Stage A 吃的不是模型的原始輸出，是 `guardian_helmet/dms/utils.py` 從模型輸出算出來的幾個幾何比例值（MAR/EAR/pitch）。

## 三個模型，都是 Google MediaPipe 的輕量臉部追蹤模型

檔案位置：`/root/guardian_helmet/models/*.tflite`

| | face_detection_short_range | face_landmark | iris_landmark |
|---|---|---|---|
| 檔案大小 | 224KB | 1.2MB | 2.6MB |
| 輸入 | 128×128×3 | 192×192×3（對齊裁切過的臉） | 64×64×3（裁切過的單眼） |
| 輸出 | 896 個 anchor 的框+6關鍵點偏移＋信心分數 | 468 點 ×(x,y,z)=1404 維＋1 個信心分數 | 71 點眼周輪廓＋5 點虹膜，各 ×(x,y,z) |
| CONV_2D 層數 | 21 | 25 | **55**（輸入最小、層數卻最多） |
| 啟動函數 | RELU | PReLU | PReLU |
| 量化 | int8 | int8 | int8 |

### face_detection_short_range —— BlazeFace 架構
單階段（single-shot）anchor-based 偵測器，不像 R-CNN 系列先框後分類，直接在特徵圖每個位置預測「這裡有沒有臉＋框在哪＋6個粗略關鍵點」。896 個 anchor 對應 `face_detection.py` 的 `ANCHOR_STRIDES=[8,16], ANCHOR_NUM=[2,6]`：stride 8 那層 16×16 格×2 anchor=512，stride 16 那層 8×8 格×6 anchor=384，512+384=896。骨幹是 depthwise-separable conv（21 CONV_2D + 16 DEPTHWISE_CONV_2D，MobileNet 系列省算量手法）+ 殘差 ADD。**這裡的信心分數輸出就是先前 `SCORE_THRESH` bug 卡住的那個值**（見 SESSION_LOG_2026-09-16.md）。

### face_landmark —— 468 點 FaceMesh
吃 face_detection 框出、對齊裁切後的臉，回歸出完整 468 點 3D 網格。用 PReLU 而非 RELU，是 Google 原版 MediaPipe Face Mesh 系列的特徵。第二個輸出（單一分數）是「這張裁切圖裡真的有清楚的臉」的信心值，用來過濾誤裁。**不含虹膜點**——虹膜是另一個獨立模型（iris_landmark）另外輸出。

已驗證幾個常用索引（跑在真實照片 `frame_yawn.jpg` 上標註過，見下方）：1=鼻尖、152=下巴、13/14=上下內唇中點、33/263=左右眼外角、61/291（`layer_b_features.py` 用）或 78/308（`utils.py` 用）=嘴角——**這是兩邊各自選的點不一樣，導致 MAR 尺度不同、門檻不能共用**，已經在改真實 pipeline 時處理過。

### iris_landmark —— 眼周+虹膜
輸入最小（64×64）但捲積層數最多（55層），反映虹膜/眼周定位對精度要求最高，Google 選擇「窄而深」而非「寬而淺」去榨小圖裡的精度。輸出兩組：71 點眼周輪廓（`EYE_KEY_NUM=71`，跟 468 點是**完全不同的獨立編號系統**，不能互相查）+ 5 點虹膜。

## 兩個容易忽略的細節

1. **目前這三個模型跑在 CPU 上，不是 NPU**——載入時自動套用 XNNPACK CPU delegate（把整張圖收斂成一個 `DELEGATE` 節點，用 CPU SIMD 加速）。要吃到 Ethos-U65 NPU 加速，要改用 `models_npu/*_vela.tflite`（已 Vela 編譯好），並在建構 `FaceDetector`/`FaceMesher`/`EyeMesher` 時傳 `delegate_path="/usr/lib/libethosu_delegate.so"`。目前 `main.py` 和 `rider/guardian_helmet_bridge.py` 都還沒吃到 NPU（`delegate_path=""` 預設值）。
2. **三個模型都是出廠就 int8 量化好的**（NXP eIQ Model Zoo 提供，我們不用自己量化），這也是為什麼 Vela 能直接吃這幾個模型去編譯給 Ethos-U65。

## 三套完全不同的「點編號系統」，不要搞混

1. **468 點臉部網格**（face_landmark.tflite 輸出）——整張臉
2. **71 點局部眼部網格**（iris_landmark.tflite 其中一個輸出）——只在裁切過的單眼小圖座標系裡，跟 468 點編號無關
3. **6 點粗略關鍵點**（face_detection_short_range.tflite 輸出）——左眼、右眼、鼻子、嘴巴、左臉頰、右臉頰，是最早、最粗的一層，用於裁切對齊跟粗略姿態估計（`decode_pose` 用的就是這 6 點，不是 468 點）
