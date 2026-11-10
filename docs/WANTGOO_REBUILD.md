# 玩股網成本遞推的全庫重算

模型 `wantgoo-vwap-0.6-v1` 逐日讀取 PostgreSQL 的目前版本價量與融資資料，處理庫內所有證券，包括上櫃及下市櫃。大盤日序列僅加總當天庫內有可計算資料的上市非 ETF 股票。

若當日融資餘額為 `Q`，融資買進為 `B`，成交均價為 `A`，則平均成本為 `((Q-B) × 前期成本 + B × A) / Q`。部位歸零後，重新建立的部位直接按買進日均價入帳。若歷史開頭或中斷後已有舊部位，起始成本以首個可計算日成交均價估算，並保存起點對後續成本的權重。若缺少必要輸入，該日保留缺失狀態。

成交均價採成交金額除以股數。已有封存的玩股網日 K 在收盤價與資料庫一致時優先使用，其他日期使用交易所或 FinMind 的成交資料。各來源的成交範圍及零股處理可能不同。融資流水需滿足當日餘額恆等式。相鄰日若有報載餘額修訂，模型按當日餘額扣除當日買進後，沿用前期平均成本，並保留修訂標記。

2026-09-07 沛爾生醫（6949）依證交所公告一股換二十股，分割當日以前期成本除以 20 後套入相同遞推式。使用換股率的結果符合玩股網個股觀測的量級。2025-06-30 材料*-KY（4763）驗證結果則符合保留名目成本。其他公司行動依目前候選模型保留名目成本並在已知時標記。兩個已核對事件顯示，平台的公司行動處理方式仍須逐類驗證。 [證交所面額變更報表](https://www.twse.com.tw/exchangeReport/TWTB7U?date=undefined&response=html&selectType=undefined)

大盤重算值為 `100 × Σ(融資股數 × 收盤價) / [0.60 × Σ(融資股數 × 平均成本)]`。每日使用同一公式。原始大盤值與已公布的個股維持率用於驗證，未用來設定這次全庫的起始成本或縮放曲線。2026-11-06 缺少的上市價格與融資流水由已封存的同日證交所報表補入重算，保留來源欄位。

## 2026-11-10 的重算結果

- 557 檔、695,077 筆證券日逐筆處理。541 檔共有 640,487 筆可計算的維持率，其餘記錄保留零餘額或缺失原因。
- 2015-01-05 至 2026-11-09 有 2,886 個上市市場日期可加總。2018-01-02 僅 17 檔納入，2026-07-28 為 387 檔。可比較的歷史範圍以當日納入成分及涵蓋率為準。
- 與玩股網原始 490 個交易日比較，平均絕對誤差約 1.62 個百分點。2026-07-28 估算 140.294%、網站 139.685%。2026-11-09 估算 139.876%、網站 139.434%。11/9 納入個股約占交易所當日已定價非 ETF 融資市值的 92.57%。
- 官網以兩條曲線顯示重算與原始值。早期及缺全市場基準日期，涵蓋率欄保留空白。資料庫覆蓋率的分母為庫內已取得有效價格的正餘額部位，不包含缺價格或缺融資的部位。

## 重產與入庫

在已設定 PostgreSQL 和 backend 相依套件的工作區執行。`--candles` 指向含玩股網日 K 與對應 SHA-256 紀錄的目錄。`--market-baseline` 選用從完整同日 TWSE 報表彙出的 `date,collateral_ntd` CSV。`--official-supplement` 選用需回補的上市日期明細，欄位為 `date,code,close,volume_shares,amount_ntd,previous,buy,sell,repay,lots`。兩者可由先前研究的 `official_daily_inputs.parquet` 產生。

```powershell
$env:PYTHONPATH = '.deps;backend'
python -m app.wantgoo_rebuild --output .local/wantgoo-full-vwap-20261110 `
  --candles tw-margin-maintenance/data/raw/wantgoo/2026/11/10 `
  --market-baseline .local/wantgoo-market-baseline-20261110.csv `
  --official-supplement .local/wantgoo-official-20261106.csv
python -m app.wantgoo_store .local/wantgoo-full-vwap-20261110
python scripts/build-wantgoo-data.py tw-margin-maintenance/output/wantgoo_reverse `
  --rebuild .local/wantgoo-full-vwap-20261110/market.json
```

重算輸出包含逐股 `stock_days.csv.gz` 及大盤 `market.json`。資料版本 `e12f56b5187001176ad9a757d108d5cb53b26239fe04577eb29a8b79d644004c` 以新模型版本寫入 `daily_estimates` 和 `estimate_segments`，可用既有研究 API 參數 `model_version=wantgoo-vwap-0.6-v1` 查詢。網站只發布合計曲線及先前 4763 驗證資料；逐檔全庫明細留在本機。
