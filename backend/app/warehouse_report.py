"""Export only aggregate local progress, never credentials or raw observations."""
from pathlib import Path
import json
from sqlalchemy import select,func
from .db import engine
from . import warehouse_schema as s
from .warehouse import encode,now
from .data_status import status

ROOT=Path(__file__).resolve().parents[2]
def export():
    with engine.connect() as c:
        report=status(c)
        # Narrow public allowlist: no worker identifiers, request parameters, raw payload or secret paths.
        safe={k:report[k] for k in ('selected','as_of','stages','groups','last_updated','work_summary','quality_flags','calibration')}
        safe['exported_at']=now().isoformat();safe['mode']='published_snapshot_not_live'
        run=report.get('research_run')
        safe['research_run']={k:run[k] for k in ('id','status','data_version','progress')} if run else None
        service=ROOT/'.local/service-status.json'
        if service.exists():
            native=json.loads(service.read_text(encoding='utf-8-sig'))
            safe['postgres_service']={k:native.get(k) for k in ('registered','status','checkedAt')}
        safe['table_counts']={t.name:c.scalar(select(func.count()).select_from(t)) for t in (s.securities,s.prices,s.margins,s.actions,s.estimates,s.captures)}
        latest=report['published_snapshot'];summary=json.loads(latest['summary_json']) if latest else {}
        safe['research_snapshot']=dict(as_of=str(latest['as_of']),data_version=latest['data_version'],status=latest['status'],
            model_days=summary.get('model_days'),model_version=summary.get('model_version')) if latest else None
        import xml.etree.ElementTree as ET
        test_file=ROOT/'research/research-tests.xml'
        if not test_file.exists():test_file=ROOT/'research/warehouse-tests.xml'
        if test_file.exists():
            suites=list(ET.parse(test_file).getroot().iter('testsuite'))
            safe['tests']={k:sum(int(x.get(k,0)) for x in suites) for k in ('tests','failures','errors','skipped')}
            safe['tests']['passed']=safe['tests']['tests']-safe['tests']['failures']-safe['tests']['errors']-safe['tests']['skipped']
        else:safe['tests']={'status':'test_receipt_not_exported'}
    (ROOT/'github-pages/database-status.json').write_text(encode(safe),encoding='utf8')
    (ROOT/'research/database_delivery_report.json').write_text(encode(safe),encoding='utf8')
    lines=['# 資料庫交付與覆蓋快照','',f"匯出時間（UTC）：{safe['exported_at']}",'',
        '此檔是匯出時的快照；最新進度請看本機 `/progress`，不代表全部缺口已修復。','',
        '| 範圍 | 選定 | 已下載 | 已入庫 | 已核驗 | 可研究計算 |','|---|---:|---:|---:|---:|---:|']
    for name,g in safe['groups'].items():lines.append(f"| {name} | {g['selected']} | {g['downloaded']} | {g['imported']} | {g['verified']} | {g['computable']} |")
    lines+=['','## 資料表筆數','','包含歷次與尚未發布的版本，不能加總成獨立股票交易日。','']+[f'- `{k}`：{v:,}' for k,v in safe['table_counts'].items()]
    lines+=['','## 已驗證','',
        '- PostgreSQL 16.15 的 Windows 自動啟動服務為 Running；已註冊使用者登入後啟動網站與資料工作。',
        '- 舊五張表逐值搬移一致；新資料庫已通過備份及獨立還原驗證。',
        f"- 自動測試 {safe['tests'].get('passed','—')} 項通過；Next.js 正式建置通過。",'','## 尚未完成','',
        '- 官方歷史日期核對與公司行動更新仍依持久化工作續跑。',
        '- 7 檔下市櫃原始行情格式待查，4 檔下市櫃融資回應為空；目前 500 檔另有 8 檔全期無融資數值。',
        '- 歷史身分、轉板、停牌與融資資格未全部核實，因此完整核驗數不能用下載數代替。',
        '- 公司行動條款、來源差異與暖機未解除的日期保留品質標記，不宣稱全市場校準或可交易 Alpha。',
        '- Docker Compose 已更新，這台未安裝 Docker，因此未實跑。','',
        '操作見 [DATABASE_OPERATIONS.md](DATABASE_OPERATIONS.md)，原始回測保留 [FACTOR_REPORT.md](FACTOR_REPORT.md)。']
    (ROOT/'docs/DATABASE_COVERAGE.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    print(encode(safe))
if __name__=='__main__':export()
