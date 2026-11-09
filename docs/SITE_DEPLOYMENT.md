# 融資維持率網站快照與部署

網站使用 `site-close-principal-0.6-v1`，預設以 2026-11-03 選定的 500 檔為股票池。收盤價用於新增融資成本，融資成數假設 60%。暖機值、餘額斷點、公司行動待核、零融資與行情缺漏均保留狀態。

本機 API 讀取 `.local/site/latest.json` 所指的不可變快照。個股路徑使用證券 ID，例如 `/stocks/s-1/`。既有本機代號網址也可解析到唯一證券。舊 API 及回測維持原版本。

## 本機產生與核對

```powershell
$env:PYTHONPATH = 'backend'
python -m app.site_snapshot --from-warehouse --seed .local/site/warehouse-seed.json.gz
python -m app.site_update --seed .local/site/warehouse-seed.json.gz --output .local/site/site-seed.json.gz --root .local/site
python scripts/build-site.py .local/site .local/pages-preview
```

使用已安裝 `backend/requirements.txt` 的 Python。由倉庫建立種子時需要連線 PostgreSQL。Windows 離線安裝若使用 `.deps`，設 `PYTHONPATH=.deps;backend`。新版 API 上線時需重啟 FastAPI 及本機 Next.js 服務，或在另一個連接埠測試。GitHub Pages 建置預設用 `/taiwan-margin-monitor` 作為路徑前綴。

`GET /api/v2/stocks?snapshot_id=...` 取得完整首頁清單。個股可用 `GET /api/v2/stocks/{security_id}/history?snapshot_id=...&start=YYYY-MM-DD&end=YYYY-MM-DD`。省略日期範圍時，API 內容與靜態個股 JSON 位元相同。

## GitHub 初次啟用

1. 將應用程式及 `.github/workflows/margin-pages.yml` 放上預設分支。
2. 在 GitHub Settings → Pages 將發布來源改為 **GitHub Actions**。
3. 新增 Actions repository secret **SITE_STATE_KEY**。使用本機產生的隨機密碼，不要將它加入 Git、Release 或工作流程輸出。
4. 建立標籤 **site-state-v1** 的 GitHub Release，上傳加密資產 **state-bootstrap.json.gz.enc**。建立方式如下，選擇最新一次驗證過的 `site-seed.json.gz`。

```powershell
node scripts/site-state.mjs seal .local/site/site-seed.json.gz .local/site/state-bootstrap.json.gz.enc .local/site/SITE_STATE_KEY.txt
```

5. 在 Actions 執行 **Verify and publish margin history**。成功後確認網站首頁能開啟個股，並確認 publish job 的公開版本與歷史檔雜湊驗證通過。接著確認一次自然排程成功，才視為每日更新已啟用。

UTC 12:15、14:15、23:15 分別對應台北時間 20:15、22:15、07:15。交易日由兩市場的月指數報表驗證。每次回查最近五個已知交易日並補到最新可取得日。兩市場價量與融資報表齊備後才能新增該日。公司行動來源的錯誤會中止工作流程。交易日未公布的情況保留下一次查詢資格，個股未知原因以品質欄標示。

## 補跑、還原與品質

- 手動重跑時可填 `through` 為日期，作為本次抓取上限。抓取上限不會刪除已保存歷史。
- 還原時填 `restore_snapshot` 為已存加密檢查點的完整版本 ID。保留上一個可用的 Release 資產。核對網站實際版本後再宣布還原完成。
- 每次更新的公開版本需先有同名的加密檢查點。部署前失敗沿用舊網站。部署後若無法確認 CDN 內容，該次工作會失敗，從上一個版本手動還原。
- 首頁「各市場資料日期」為雙市場來源核驗進度。個股「最後可估算日」可能較早。日期差異、暖機、未校準及待核換股條款均可能影響比較。
- 雲端狀態包含重新計算所需的標準化輸入與來源指紋，完整 PostgreSQL 和原始歷史庫留在本機。官網快照匯出只接受經核對的展示檔；本機種子、金鑰與憑證不得納入 Pages artifact。
- FinMind 僅針對官方報表個股行情缺漏補查，標註替代來源。公司行動只使用明確的換股率。未取得可核對的條款時切斷成本段。
- 進一步核對估算時使用逐日 CSV。官方價量與融資變動來源分列，原始快照可由版本 ID 核對。130% 參考價為估計成本 × 0.6 × 1.3。
