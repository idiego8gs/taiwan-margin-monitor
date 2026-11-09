"""Fixed, retrospective threshold study. Signals never inspect future outcomes."""
from collections import Counter,defaultdict,deque
from math import log,ceil
from statistics import mean,stdev,median
import hashlib
import numpy as np
from .factor_validation import net_return

THRESHOLDS=(130,125,120,110)
HORIZONS=tuple(range(1,21))
MODES=('strict','sensitivity_5pct')
GROUPS=('all','bull_bull','bull_nonbull','nonbull_bull','nonbull_nonbull')
CALIPERS={'prior_return_5':5.,'volatility_20':2.,'log_turnover_20':1.}

def valid(m,mode):
    if not m or m.get('raw_maintenance') is None:return False
    if mode=='strict':return not m['warmup']
    v=m.get('initial_cost_sensitivity_pct')
    return v is not None and float(v)<=5

def crossing(row,mode,threshold):
    a=row['model'];b=row['previous_model']
    return bool(valid(a,mode) and valid(b,mode) and a['segment']==b['segment'] and
        float(b['raw_maintenance'])>threshold>=float(a['raw_maintenance']))

def build_features(records):
    header=records[0];cal=header['calendar'];regimes={};report=Counter();by_day=defaultdict(list)
    for market,days in cal.items():
        regimes[market]={};window=deque(maxlen=60)
        for day in days:
            row=header['indices'][market].get(day)
            if not row or float(row['close'])<=0:window.clear();continue
            window.append(float(row['close']))
            if len(window)==60:regimes[market][day]='bull' if mean(list(window)[-20:])>mean(window) else 'nonbull'
    for stock in records[1:]:
        sec=stock['security'];market=sec['market'];days=cal[market];quotes={r['date']:r for r in stock['days']}
        stock['quotes']=quotes;actions={x['date'] for x in stock['actions']};stock['action_days']=actions
        eligibility = {r['trading_date']: r for r in stock.get('eligibility', [])}
        stock['suspended_days'] = {day for day, item in eligibility.items()
            if item['price_status']=='not_applicable'}
        if sec['cohort']!='top500':
            report['delisted_supplemental_stocks']+=1;continue
        closes=deque(maxlen=60);turnover=deque(maxlen=20)
        for i,day in enumerate(days):
            row=quotes.get(day);p=row.get('price') if row else None
            if day in stock['suspended_days'] or not p or p['close'] is None or float(p['close'])<=0 or not p['volume_shares'] or not p['turnover_ntd'] or float(p['turnover_ntd'])<=0:
                closes.clear();turnover.clear();continue
            closes.append(float(p['close']));turnover.append(float(p['turnover_ntd']))
            if len(closes)<60 or day not in regimes[market] or day[:4]<'2022':continue
            if any(d in actions for d in days[i-59:i+1]):report['action_in_lookback']+=1;continue
            prev=quotes.get(days[i-1]) if i else None
            m=row.get('model');pm=prev.get('model') if prev else None
            if not m or not pm:continue
            eligible_modes = MODES
            if header.get('format',1)>=2:
                eligible_modes = tuple(mode for mode, field in zip(MODES, ('strict','exploratory'))
                    if eligibility.get(day, {}).get(field)
                    and eligibility.get(days[i-1], {}).get(field))
                if not eligible_modes:continue
            cs=list(closes);rs=[100*(cs[j]/cs[j-1]-1) for j in range(40,60)]
            feature={'prior_return_5':100*(cs[-1]/cs[-6]-1),'volatility_20':stdev(rs),'log_turnover_20':log(mean(turnover))}
            trend=('bull' if mean(cs[-20:])>mean(cs) else 'nonbull')+'_'+regimes[market][day]
            by_day[(market,day)].append({'security_id':sec['id'],'code':sec['code'],'day':day,'market':market,
                'index':i,'features':feature,'trend':trend,'model':m,'previous_model':pm,
                'eligible_modes':eligible_modes,'stock':stock})
            report['feature_stock_days']+=1
    for group in by_day.values():group.sort(key=lambda x:(x['code'],x['security_id']))
    return header,by_day,dict(report)

def choose_pairs(by_day,mode,threshold):
    """Select on contemporaneous features only; forward prices are untouched."""
    selected=[]
    for (market,day),rows in sorted(by_day.items()):
        usable=[r for r in rows if mode in r['eligible_modes']
            and valid(r['model'],mode) and valid(r['previous_model'],mode)
            and r['model']['segment']==r['previous_model']['segment']]
        signals=[r for r in usable if crossing(r,mode,threshold)]
        if not signals:continue
        signal_ids={r['security_id'] for r in signals};controls=[r for r in usable if r['security_id'] not in signal_ids]
        by_trend=defaultdict(list)
        for control in controls:
            by_trend[control['trend']].append(control)
        reference={
            trend:{name:mean(row['features'][name] for row in candidates) for name in CALIPERS}
            for trend,candidates in by_trend.items()
        }
        for signal in signals:
            best=None
            for control in by_trend[signal['trend']]:
                scaled=[(signal['features'][k]-control['features'][k])/width for k,width in CALIPERS.items()]
                if any(abs(x)>1 for x in scaled):continue
                distance=sum(x*x for x in scaled)/len(scaled);key=(distance,control['code'],control['security_id'])
                if best is None or key<best[0]:best=(key,control)
            selected.append((signal|{'control_pool_mean':reference.get(signal['trend'])},
                best[1] if best else None,best[0][0] if best else None))
    return selected

def outcome(row,horizon,header):
    days=header['calendar'][row['market']];i=row['index'];stock=row['stock']
    if i+horizon>=len(days):return None,'outside_frozen_calendar',None,None
    entry=days[i+1];exit=days[i+horizon]
    if stock['suspended_days'].intersection(days[i+1:i+horizon+1]):
        return None,'verified_suspension',entry,exit
    if not stock.get('action_coverage_end') or exit>stock['action_coverage_end']:
        return None,'action_coverage_unverified',entry,exit
    if any(x in stock['action_days'] for x in days[i+1:i+horizon+1]):return None,'corporate_action_in_window',entry,exit
    window=[stock['quotes'].get(x) for x in days[i+1:i+horizon+1]]
    if any(not q or not q.get('price') or not q['price']['volume_shares'] or q['price']['volume_shares']<=0 for q in window):
        return None,'missing_price_or_suspension',entry,exit
    entry_price=window[0]['price']['open'];exit_price=window[-1]['price']['close']
    value=net_return(float(entry_price) if entry_price is not None else None,float(exit_price) if exit_price is not None else None)
    return value,('ok' if value is not None else 'invalid_entry_or_exit'),entry,exit

def events_for_horizon(pairs,horizon,header,group='all'):
    events=[];next_eligible={}
    for signal,control,distance in pairs:
        if group!='all' and signal['trend']!=group:continue
        sid=signal['security_id'];i=signal['index']
        if i<next_eligible.get(sid,0):continue
        next_eligible[sid]=i+horizon
        value,reason,entry,exit=outcome(signal,horizon,header)
        other,other_reason,_,_=outcome(control,horizon,header) if control else (None,'no_eligible_control',None,None)
        events.append({'signal_date':signal['day'],'security_id':sid,'signal_code':signal['code'],
            'control_id':control['security_id'] if control else None,'control_code':control['code'] if control else None,
            'market':signal['market'],'trend':signal['trend'],'entry_date':entry,'exit_date':exit,
            'control_trend':control['trend'] if control else None,
            'distance':distance,'signal_features':signal['features'],'control_features':control['features'] if control else None,
            'control_pool_mean':signal['control_pool_mean'],
            'maintenance':signal['model']['raw_maintenance'],'previous_maintenance':signal['previous_model']['raw_maintenance'],
            'segment':signal['model']['segment'],'signal_net_pct':value,'signal_outcome_status':reason,
            'control_net_pct':other,'control_outcome_status':other_reason,
            'matched_excess_pp':value-other if value is not None and other is not None else None})
    return events

def summarize(events,group):
    rows=[x for x in events if group=='all' or x['trend']==group]
    matched=[x for x in rows if x['matched_excess_pp'] is not None]
    attr=Counter()
    for x in rows:
        if x['signal_outcome_status']!='ok':attr['signal_'+x['signal_outcome_status']]+=1
        if x['control_outcome_status']!='ok':attr['control_'+x['control_outcome_status']]+=1
    signal=[x['signal_net_pct'] for x in matched];excess=[x['matched_excess_pp'] for x in matched]
    observed=[x['signal_net_pct'] for x in rows if x['signal_net_pct'] is not None]
    pending=sum(x['signal_outcome_status']=='outside_frozen_calendar' for x in rows)
    mature=len(rows)-pending
    periods={}
    for year in sorted({x['signal_date'][:4] for x in matched}):
        ys=[x['matched_excess_pp'] for x in matched if x['signal_date'].startswith(year)]
        periods[year]={'matched':len(ys),'mean_excess_pp':mean(ys)}
    months=defaultdict(list)
    for x in matched:
        months[x['signal_date'][:7]].append(x)
    total_net=sum(signal)
    total_excess=sum(excess)
    monthly={}
    for month, subset in sorted(months.items()):
        remaining=len(matched)-len(subset)
        monthly[month]={
            'matched':len(subset),
            'share_pct':100*len(subset)/len(matched),
            'without_month_net_pct':(total_net-sum(x['signal_net_pct'] for x in subset))/remaining if remaining else None,
            'without_month_excess_pp':(total_excess-sum(x['matched_excess_pp'] for x in subset))/remaining if remaining else None,
        }
    balance={name:{
        'mean_difference_before_matching':mean(x['signal_features'][name]-x['control_pool_mean'][name] for x in matched),
        'mean_difference':mean(x['signal_features'][name]-x['control_features'][name] for x in matched),
        'median_absolute_difference':median(abs(x['signal_features'][name]-x['control_features'][name]) for x in matched),
    } for name in CALIPERS} if matched else {}
    return {'signals':len(rows),'matched':len(matched),'signal_dates':len({x['signal_date'] for x in matched}),
        'pending':pending,'mature':mature,'mature_unpaired':mature-len(matched),
        'mature_unpaired_pct':100*(mature-len(matched))/mature if mature else None,
        'observable_signals':len(observed),'mean_observed_signal_net_pct':mean(observed) if observed else None,
        'mean_control_net_pct':mean(x['control_net_pct'] for x in matched) if matched else None,
        'months_descriptive':monthly,'matched_feature_balance':balance,
        'attrition':dict(attr),'mean_signal_net_pct':mean(signal) if signal else None,
        'mean_excess_pp':mean(excess) if excess else None,'median_excess_pp':median(excess) if excess else None,
        'win_pct':100*sum(v>0 for v in excess)/len(excess) if excess else None,'years_descriptive':periods},matched

def bootstrap(task, block=20):
    """Vectorized date-block bootstrap with per-result deterministic seed."""
    key,calendar,matched,draws=task;n=len(calendar);index={d:i for i,d in enumerate(calendar)}
    sums=np.zeros(n,dtype=np.float64);counts=np.zeros(n,dtype=np.int64)
    for item in matched:
        i=index[item['signal_date']];sums[i]+=item['matched_excess_pp'];counts[i]+=1
    unique=int(np.count_nonzero(counts));base={'observations':len(matched),'signal_dates':unique,'block_days':block,'draws_requested':draws}
    if unique<30 or n<2*block:return key,base|{'status':'insufficient_data','reason':'fewer than 30 signal dates or calendar too short','p_value':None,'ci95_pp':None}
    observed=float(sums.sum()/counts.sum())
    # Circular blocks give edge dates the same inclusion weight as interior dates.
    prefix=np.r_[0.,np.cumsum(np.r_[sums,sums[:block]])]
    pc=np.r_[0,np.cumsum(np.r_[counts,counts[:block]])]
    seed=int.from_bytes(hashlib.sha256(key.encode()).digest()[:8],'big');rng=np.random.default_rng(seed)
    blocks=ceil(n/block);widths=np.full(blocks,block);widths[-1]=n-block*(blocks-1);samples=[]
    for first in range(0,draws,2048):
        size=min(2048,draws-first);starts=rng.integers(0,n,size=(size,blocks))
        numer=(prefix[starts+widths]-prefix[starts]).sum(axis=1);denom=(pc[starts+widths]-pc[starts]).sum(axis=1)
        samples.extend((numer[denom>0]/denom[denom>0]).tolist())
    if len(samples)<draws*.95:return key,base|{'status':'insufficient_data','reason':'too many empty resamples','p_value':None,'ci95_pp':None}
    a=np.asarray(samples);se=float(a.std(ddof=1))
    if se<=1e-15:return key,base|{'status':'insufficient_variation','p_value':None,'ci95_pp':None}
    deviations=np.abs(a-observed)
    p=(1+int(np.count_nonzero(deviations>=abs(observed))))/(len(a)+1)
    # Invert the same two-sided centered test at alpha=.05.
    rank=len(a)-(int(.05*(len(a)+1))-1)-1
    radius=float(np.partition(deviations,rank)[rank])
    return key,base|{'status':'ok','mean_excess_pp':observed,'ci95_pp':[observed-radius,observed+radius],
        'bootstrap_se_pp':se,'t_stat':observed/se,'p_value':p,'valid_draws':len(a),'seed':seed}

def bootstrap_with_sensitivity(task):
    key, result = bootstrap(task)
    if result['status']=='ok':
        result['block_sensitivity']={str(block): bootstrap(task, block)[1] for block in (40,60)}
    return key, result
