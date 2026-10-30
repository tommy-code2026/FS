# PEACE 股票品質分析

## 本機使用

雙擊 `啟動PEACE.bat`，等待瀏覽器開啟後輸入股票代號。直接以 `file://` 開啟 `index.html` 不會啟動資料服務。

## 透過 GitHub 與 Render 發佈

GitHub Pages 提供 PEACE 網頁，Render 執行股票資料 API。使用者開啟 GitHub Pages 網址即可查詢，不需要在自己的電腦啟動 BAT 或安裝 Python。

1. 將 `index.html`、`peace_server.py` 和 `render.yaml` 推送到 GitHub 儲存庫。
2. 在 Render 選擇 **New → Blueprint**，連結該 GitHub 儲存庫；若服務已存在，請同步 Blueprint 設定或在 Environment 新增 `ALLOWED_ORIGINS=https://tommy-code2026.github.io`。
3. Yahoo Finance 無法提供報價時，後端會嘗試 Nasdaq；年度財報會嘗試 SEC EDGAR（美國上市公司）。SEC 要求在 Render 設定 `SEC_USER_AGENT`，格式需包含應用程式名稱與可聯絡的 Email，例如 `PEACE Stock Analyzer contact your-email@example.com`。
4. 若免費來源仍無法提供資料，可到 Financial Modeling Prep 申請 API key，並只在 Render 的 Environment 設定 `FMP_API_KEY`。不要將金鑰寫入 `index.html`、程式碼或 GitHub。
5. 確認 `peace-stock-analyzer` Web Service 已部署，且 `/healthz` 回應成功。
6. 在 GitHub 儲存庫設定 **Pages**，將 `main` 分支的根目錄設為發佈來源。
7. 確認 GitHub Pages 網址為 `https://tommy-code2026.github.io/FS/`，頁面呼叫 `https://peace-stock-analyzer.onrender.com/api/stock`。

行情先使用 Yahoo Finance，再以 Nasdaq 備援；若仍缺資料且已設定 `FMP_API_KEY`，再嘗試 FMP。年度財報先取得 Yahoo Finance 資料，再以 SEC EDGAR 官方年報欄位覆蓋可用值；SEC 缺欄時由 Yahoo 保留，若已設定 FMP key 則再補缺。SEC 僅涵蓋其收錄公司，並需設定 `SEC_USER_AGENT`。台股每日 PE 優先使用 TWSE OpenAPI，Yahoo Finance 為備援；TWSE 季度財報不會未經核對直接加總成年度資料。Nasdaq 目前只作行情備援，不作年度財報來源。

VIX/VXN 來源為 CBOE，Fear & Greed 使用 CNN，S&P 500 PE 使用 Multpl。StockAnalysis、Stockrow、GuruFocus、Morningstar、MacroMicro 等網站未確認有穩定且允許程式化存取的免費 API；頁面可瀏覽不代表可用作自動資料備援。質化分析會保留來源連結並將未取得資料明確標示，不以不穩定網頁解析或付費內容冒充已抓取數據。不同來源的覆蓋市場與欄位不相同，缺少欄位會保留為無資料。後端只允許設定的 GitHub Pages 網域跨網域讀取 API。免費方案可能在閒置後休眠，重新使用時第一次載入會比較慢。

質化頁目前會依市場抓取可直接核對的官方原文：美股從 SEC EDGAR 的近期 10-K、10-Q、8-K、DEF 14A 申報文件擷取主題片段；台股從 MOPS 重大訊息擷取公告原文。每筆顯示來源、文件名稱、日期、直接網址及摘錄。SEC 仍須設定含聯絡 Email 的 `SEC_USER_AGENT`；缺少設定或來源不可用時，行情資料仍會照常回傳，質化來源狀態會說明原因。

Yahoo Finance 與 SEC 的單次 HTTP 請求逾時均為 5 秒，不代表整個查詢只需 5 秒。HTTP 429、其他 HTTP 錯誤或逾時會由來源層或 API 備援路徑捕獲；失敗時保留錯誤訊息並使用空資料或已存在的 SEC 成功快取，不會把失敗結果寫入 SEC 快取。單份 SEC 文件失敗不會丟棄其他已抓到的文件。所有行情備援均失敗時，API 仍回傳明確的 JSON 錯誤，而非假造行情。可執行 `python -m unittest test_peace_server -v` 驗證限流與逾時行為。

這些摘錄是關鍵字定位用的待核對證據，不代表正面或負面判定。依質化來源規則，公司自行申報內容不能單獨證明護城河；目前 USPTO/TIPO 專利、監管機關、總體物價、產業價值鏈、公司 IR 與競爭者資料尚未自動抓取，因此缺少交叉來源或同業比較時不推定為符合。
