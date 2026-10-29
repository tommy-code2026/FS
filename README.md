# PEACE 股票品質分析

## 本機使用

雙擊 `啟動PEACE.bat`，等待瀏覽器開啟後輸入股票代號。直接以 `file://` 開啟 `index.html` 不會啟動資料服務。

## 透過 GitHub 與 Render 發佈

GitHub Pages 提供 PEACE 網頁，Render 執行股票資料 API。使用者開啟 GitHub Pages 網址即可查詢，不需要在自己的電腦啟動 BAT 或安裝 Python。

1. 將 `index.html`、`peace_server.py` 和 `render.yaml` 推送到 GitHub 儲存庫。
2. 在 Render 選擇 **New → Blueprint**，連結該 GitHub 儲存庫；若服務已存在，請同步 Blueprint 設定或在 Environment 新增 `ALLOWED_ORIGINS=https://tommy-code2026.github.io`。
3. 到 Financial Modeling Prep 申請 API key，並只在 Render 的 Environment 設定 `FMP_API_KEY`。不要將金鑰寫入 `index.html`、程式碼或 GitHub。
4. 確認 `peace-stock-analyzer` Web Service 已部署，且 `/healthz` 回應成功。Yahoo Finance 被限流時，後端會使用 FMP API 作為備援。
5. 在 GitHub 儲存庫設定 **Pages**，將 `main` 分支的根目錄設為發佈來源。
6. 確認 GitHub Pages 網址為 `https://tommy-code2026.github.io/FS/`，頁面呼叫 `https://peace-stock-analyzer.onrender.com/api/stock`。

`render.yaml` 會使用 Render 指定的連接埠、綁定公開服務介面，並以 `/healthz` 檢查服務狀態。後端只允許設定的 GitHub Pages 網域跨網域讀取 API。免費方案可能在閒置後休眠，重新使用時第一次載入會比較慢。資料由 Yahoo Finance、CNN 與 Multpl 等來源即時取得；第三方來源若暫時無法使用，頁面會顯示缺少資料或錯誤。
