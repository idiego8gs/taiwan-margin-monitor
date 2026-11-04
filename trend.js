fetch('./trend.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json()}).then(b=>{
 const s=document.createElement('section');s.className='panel';s.style.marginTop='24px';
 const title=document.createElement('h2');title.style.padding='0 20px';title.textContent='個股與大盤均線條件 · 多重比較';s.appendChild(title);
 const desc=document.createElement('p');desc.style.padding='0 20px';desc.textContent='最終結果：320 組比較中，只有 3 組達到至少 10 個訊號日期的檢定門檻；Benjamini–Hochberg 多重比較校正後，沒有顯著結果。所有可配對事件均在成本暖機期，排除暖機期後無事件，不能用來證明 Alpha。';s.appendChild(desc);
 const info=document.createElement('p');info.style.padding='0 20px';info.textContent='研究資料：2026-07-08 至 2026-11-03，符合條件 261 檔；使用訊號日個股及上市／上櫃市場指數 20 日與 60 日均線。執行 2 億次日期叢集符號翻轉，檢定條件與持有期組合；僅 3 組可檢定。未作風險因子調整，且歷史資料、存活者偏差和交易成本模型仍有限制。';s.appendChild(info);
 const wrap=document.createElement('div');wrap.className='scroll';const table=document.createElement('table');const head=table.insertRow();for(const text of ['門檻','持有','個股／指數','事件／訊號日','平均配對差額','未校正 p','校正 q']){const th=document.createElement('th');th.textContent=text;head.appendChild(th)}
 for(const x of b.results.filter(x=>x.p_unadjusted!==null)){const row=table.insertRow();for(const value of [x.threshold+'%',x.holding_days+' 日',(x.stock_trend==='bull'?'多頭':'非多頭')+'／'+(x.index_trend==='bull'?'多頭':'非多頭'),x.events+'／'+x.signal_dates,Number(x.mean_excess_pp).toFixed(2)+' pp',Number(x.p_unadjusted).toFixed(4),Number(x.bh_q).toFixed(3)]){const td=row.insertCell();td.textContent=value}}wrap.appendChild(table);s.appendChild(wrap);
 const foot=document.createElement('p');foot.style.padding='0 20px';foot.textContent='計算於 '+b.generated_at+' 完成；執行約 53 分鐘，提前達到 2 億次抽樣上限。此結果是固定快照，未持續更新。';s.appendChild(foot);document.querySelector('footer').before(s);
}).catch(()=>{});
