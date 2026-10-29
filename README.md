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

行情及財報會依序使用 Yahoo Finance、Nasdaq、SEC EDGAR，並可選擇 FMP API；VIX/VXN 備援來源為 CBOE，Fear & Greed 使用 CNN，S&P 500 PE 使用 Multpl。後端只允許設定的 GitHub Pages 網域跨網域讀取 API。免費方案可能在閒置後休眠，重新使用時第一次載入會比較慢。各來源覆蓋市場與欄位不相同，第三方來源若暫時無法使用或不支援該代號，頁面會顯示缺少資料或錯誤。

Word 參考清單中的部分網站只有網頁、要求付費/登入，或回傳 403/429，沒有穩定且允許程式化存取的免費 API，因此沒有用不穩定的網頁解析取代正式資料來源。Nasdaq 與 SEC 主要涵蓋美國上市公司；例如 `2330.TW` 若 Yahoo 不可用且未設定可支援該市場的 FMP API，可能無法完成抓取。
