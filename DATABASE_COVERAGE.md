# 資料庫交付與覆蓋快照

匯出時間（UTC）：2026-11-05T06:47:49.939951+00:00

此檔是匯出時的快照；最新進度請看本機 `/progress`，不代表全部缺口已修復。

| 範圍 | 選定 | 已下載 | 已入庫 | 已核驗 | 可研究計算 |
|---|---:|---:|---:|---:|---:|
| delisted | 57 | 57 | 56 | 0 | 36 |
| top500 | 500 | 500 | 500 | 0 | 491 |

## 資料表筆數

包含歷次與尚未發布的版本，不能加總成獨立股票交易日。

- `securities`：557
- `daily_prices`：708,486
- `daily_margins`：727,753
- `corporate_actions`：6,001
- `daily_estimates`：910,282
- `source_captures`：4,021

## 尚未完成

- 官方歷史日期核對與 2026-11-04 公司行動更新仍依持久化工作續跑。
- 7 檔下市櫃原始行情格式待查，4 檔下市櫃融資回應為空；目前 500 檔另有 8 檔全期無融資數值。
- 歷史身分、轉板、停牌與融資資格未全部核實，因此完整核驗數不能用下載數代替。
- 公司行動條款、來源差異與暖機未解除的日期保留品質標記，不宣稱全市場校準或可交易 Alpha。
- Windows PostgreSQL 系統服務已註冊為自動啟動並實際驗證 Running；網站及 workers 使用本機啟動腳本。
- Docker Compose 已更新，這台未安裝 Docker，因此未實跑。

操作見 [DATABASE_OPERATIONS.md](DATABASE_OPERATIONS.md)，原始回測保留 [FACTOR_REPORT.md](FACTOR_REPORT.md)。
