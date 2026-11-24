# 115學年-1 課堂形成性評量網站（國語・數學・社會）

專為國小高年級課堂投影與形成性評量設計的靜態多頁面教學系統。支援**本地放入檔案更新**與**掛載 Google 雲端資料夾定時自動同步**（含圖檔 PDF 自動轉為網頁圖片），完全免租伺服器且無單檔上傳大小限制。

## 網站頁面架構

| 頁面 | 路徑 | 說明 |
| --- | --- | --- |
| 總覽首頁 | `index.html` | 三科快速入口、各週進度矩陣與更新指南 |
| 國語講台 | `chinese.html` | 翰林版 · 情境閱讀雙欄投影、字詞句型、插圖燈箱與評分規準 |
| 數學講台 | `math.html` | 康軒版 · 是非／選擇／應用題、KaTeX 公式排版與逐行算式步驟演示 |
| 社會講台 | `social.html` | 南一版 · 時事與史料圖表、立場思辨雙欄卡與分層評分規準 |

---

## 方式一：掛載 Google 雲端資料夾自動更新（推薦，免碰 GitHub 檔案上限）

當資料夾內包含大量試題或**圖檔 PDF** 時，可直接掛載 Google 雲端資料夾，由 GitHub Actions 在雲端臨時下載並轉檔成極輕量的 JSON 與 PNG，**不佔用 Git 儲存空間**：

1. **設定雲端資料夾權限**：將存放試題與圖檔（含圖檔 PDF）的 Google 雲端資料夾設為「**知道連結的任何人 → 檢視者**」。
   - 若共用一個總資料夾，請以子資料夾或檔名包含 `國語` / `數學` / `社會` 區分科目；圖片或**圖檔 PDF** 建議放在 `images` / `配圖` 子資料夾（或檔名包含 `圖一` / `示意圖` / `第X題` 等）。
2. **填入掛載連結（擇一即可）**：
   - 修改根目錄的 `cloud_sync.json`：將 `"enabled"` 改為 `true`，並於 `"drive_folder_url"`（或三科各自的 `"folder_url"`）貼上雲端資料夾網址；**或**
   - 到 GitHub 倉庫 **Settings → Secrets and variables → Actions → Variables** 新增 `DRIVE_FOLDER_URL`（或 `DRIVE_FOLDER_CHINESE` / `DRIVE_FOLDER_MATH` / `DRIVE_FOLDER_SOCIAL`）。
3. **自動定時與手動觸發**：
   - 內建排程於**每日凌晨 04:00** 與**每週一上午 07:30（台灣時間）**自動抓取最新雲端檔案並發布網站。
   - 若剛改完檔案想馬上更新，只要到 GitHub **Actions** 分頁按 **Run workflow** 即可於 1 分鐘內同步上線。

### 圖檔 PDF 自動轉圖支援
- 若把圖檔 PDF 放入雲端 `images`（或本機 `sources/{subject}/images/`），或 DOCX 內附的 Google Drive 配圖連結指向單頁 PDF 檔，系統會自動用 PyMuPDF 轉成高解析 PNG 並裁切多餘白邊，直接內嵌於講台畫面與放大燈箱中。

---

## 方式二：本機手動放入檔案更新

1. **放入最新試題檔**（檔名以 `_` 開頭的範本檔會自動略過）：
   - 國語：`sources/chinese/`（`.pdf` / `.docx` / `.md`）
   - 數學：`sources/math/`（`.docx` / `.pdf` / `.md`）
   - 社會：`sources/social/`（`.docx` / `.pdf` / `.md`）
   - 若有獨立圖檔或**圖檔 PDF**，放入各科 `images/`（如 `第11週_圖一_...pdf`、`w11_q7.pdf`）。
2. **執行轉換腳本**：雙擊 `update.command`，或執行 `python3 scripts/build_data.py`。
3. **預覽或推送**：
   - 本機預覽：`python3 -m http.server 8765` 後開啟 `http://127.0.0.1:8765/`。
   - 推送更新：`git add . && git commit -m "更新試題" && git push`。

---

## 課堂講台快捷鍵

| 快捷鍵 | 功能 |
| --- | --- |
| `←` / `→` | 上一題 / 下一題 |
| `Space` | 分層揭曉答案 / 顯示下一步算式 |
| `E` | 直接展開或收合詳細解析與評分規準 |
| `1`～`4` | 標記選項 (A)～(D)（是非題：`1`=○、`2`=✕） |
| `T` | 思考計時器 |
| `R` | 班級隨機抽號器 |
| `B` | 板書畫筆標記 |
| `F` | 全螢幕投影 |
| `?` | 顯示快捷鍵說明 |

## GitHub Pages 首次啟用

1. 確認倉庫為 **Public**（免費版 GitHub 帳號須設為公開）。
2. 前往 GitHub 倉庫 **Settings → Pages**，在 **Build and deployment → Source** 選擇 **GitHub Actions**。
3. 到 **Actions** 分頁點擊 **Re-run jobs**（或 **Run workflow**），完成後即可透過 `https://chy23.github.io/CLA/` 使用。
