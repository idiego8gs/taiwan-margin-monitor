"""Public, read-only, deliberately narrow research progress endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes, json

ROOT=Path('research')
HTML='''<!doctype html><html lang="zh-Hant"><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"><title>台股研究進度</title><style>body{background:#09121f;color:#e8f4ff;font:16px system-ui;margin:auto;max-width:850px;padding:24px}h1{font-size:25px}section{background:#111f30;border:1px solid #294055;border-radius:14px;padding:20px;margin:16px 0}strong{color:#52dbc5}p{line-height:1.7;color:#bdccdc}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #293a4b;text-align:left}small{color:#c3a469}</style><h1>台股融資研究 · 遠端進度</h1><section><p id="state">讀取中…</p><p id="time"></p><p id="count"></p><small>每 15 秒重新讀取；若長時間未更新，請檢查電腦或網路。此頁不提供帳戶資料或遠端操作。</small></section><section><h2>均線條件與統計結果</h2><p id="result">讀取中…</p><div style="overflow:auto"><table><thead><tr><th>門檻</th><th>持有</th><th>個股／指數趨勢</th><th>事件／日期</th><th>平均差額</th><th>校正 q 值</th></tr></thead><tbody id="rows"></tbody></table></div><p>訊號日使用 20 日與 60 日均線，僅列有足夠日期可檢定的組合。結果為市場配對報酬差額，非風險調整 Alpha。暖機、存活者偏差、公司行動缺漏與多重測試均影響解讀。</p></section><p><a style="color:#52dbc5" href="https://idiego8gs.github.io/taiwan-margin-monitor/">查看既有網站及持有 1–20 日回測</a></p><script>
async function update(){try{const r=await fetch('/api',{cache:'no-store'});const d=await r.json();document.getElementById('state').textContent='程序狀態：'+(d.alive?'正在執行':d.progress.status==='completed'?'已完成':'未偵測到執行程序');document.getElementById('time').textContent='進度最後更新：'+(d.progress.updated_at||'未知')+'；目標運算 '+Math.round((d.progress.target_seconds||0)/60)+' 分鐘';document.getElementById('count').textContent='已運算 '+Math.round((d.progress.elapsed_seconds||0)/60)+' 分鐘；抽樣次數 '+Number(d.progress.draws||0).toLocaleString();let b=d.result;document.getElementById('result').textContent=(d.final?'最終':'初步')+'快照：符合資料條件 '+b.eligible_stocks+' 檔；共 '+b.preplanned_tests+' 組，所有可配對事件都處於暖機期；不可據此宣稱 Alpha。';const rows=document.getElementById('rows');rows.replaceChildren();for(const x of b.results.filter(x=>x.p_unadjusted!==null)){let tr=document.createElement('tr');for(const v of [x.threshold+'%',x.holding_days+' 日',(x.stock_trend==='bull'?'多頭':'非多頭')+' / '+(x.index_trend==='bull'?'多頭':'非多頭'),x.events+' / '+x.signal_dates,Number(x.mean_excess_pp).toFixed(2)+' pp',Number(x.bh_q).toFixed(3)]){let td=document.createElement('td');td.textContent=v;tr.appendChild(td)}rows.appendChild(tr)}}catch(e){document.getElementById('state').textContent='連線中斷或資料暫時無法讀取，稍後重試。'}}update();setInterval(update,15000);</script></html>'''

HTML=HTML.replace('均線條件與統計結果','先前 2026 年短期研究')
HTML=HTML.replace('</html>','''<script>
const panel=document.createElement('section');panel.innerHTML='<h2>最新資料回補及配對研究</h2><p id="backfill"></p><p id="queue"></p><p id="factor"></p><p id="example"></p>';document.querySelector('h1').after(panel);
async function latest(){try{let r=await fetch('/api',{cache:'no-store'}),d=await r.json(),b=d.backfill,q=d.delisted,f=d.factor;document.getElementById('backfill').textContent='公司行動：'+b.count+' / 500 檔；'+(b.alive?'背景程序執行中':'未偵測到背景程序')+'；最後更新 '+(b.updated_at||'未知');document.getElementById('queue').textContent='下市櫃候選佇列：'+q.completed+' / '+q.target+' 檔；狀態 '+q.status+'；完成歸檔不等於可直接回測。';document.getElementById('factor').textContent='配對驗證：'+f.tested+' / '+f.planned_tests+' 組達最低檢定條件，BY 校正顯著 '+f.by_rejections+' 組。歷史資料並非 point-in-time，亦非乾淨樣本外。';let x=f.primary;document.getElementById('example').textContent='探索情境：130%、持有 5 日，'+x.events+' 組配對、'+x.dates+' 個訊號日；平均差額 '+Number(x.mean).toFixed(2)+' 個百分點，95% 區間 '+x.ci.map(v=>Number(v).toFixed(2)).join(' 至 ')+'。'}catch(e){document.getElementById('backfill').textContent='最新資料暫時無法讀取。'}}latest();setInterval(latest,15000);
</script></html>''')

def load(name):
    try:return json.loads((ROOT/name).read_text(encoding='utf8'))
    except (OSError,ValueError):return {}

def process_running(pid):
    handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
    if handle:ctypes.windll.kernel32.CloseHandle(handle)
    return bool(handle)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/': content=HTML.encode('utf8');mime='text/html; charset=utf-8'
        elif self.path=='/api':
            progress=load('trend_study_progress.json')
            alive=process_running(53420)
            final=progress.get('status')=='completed'
            backfill=load('top500_20261103/backfill/progress.json');queue=load('delisted_202110_202611/progress.json');factor=load('factor_validation_final.json')
            primary=next((x for x in factor.get('results',[]) if x['quality']=='sensitivity_5pct' and x['threshold']==130 and x['holding_days']==5 and x['trend']=='all'),None)
            safe_factor={'tested':factor.get('tested'),'planned_tests':factor.get('planned_tests'),'by_rejections':factor.get('by_rejections'),
              'primary':{'events':primary['summary']['events'],'dates':primary['summary']['signal_dates'],'mean':primary['summary']['mean_excess_pp'],'ci':primary['inference']['ci95_pp']} if primary else None}
            content=json.dumps({'progress':progress,'alive':alive,'final':final,'result':load('trend_study.json' if final else 'trend_study_preview.json'),
              'backfill':{'count':len(backfill.get('events_completed',{})),'alive':process_running(62780),'updated_at':backfill.get('updated_at')},
              'delisted':{'completed':len(queue.get('completed',{})),'target':queue.get('target',0),'status':queue.get('status','unknown')},'factor':safe_factor},ensure_ascii=False).encode('utf8');mime='application/json; charset=utf-8'
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(content)

if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',8793),Handler).serve_forever()
