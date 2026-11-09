"""Combine market-wide stock margin estimates; this is not a customer account."""
from datetime import date
from decimal import Decimal
import re
from sqlalchemy import select
from .calculation import calculate
from .db import Observation
from .engine import DataQualityError

D=Decimal

def calculate_basket(db, codes, *, start_at=None, as_of=None, finance_ratio=D('0.6'), buy_price_method='vwap'):
    if not 1<=len(codes)<=10 or len(set(codes))!=len(codes) or any(not re.fullmatch(r'[A-Za-z0-9]{2,20}',x) for x in codes):
        raise DataQualityError('請輸入 1 至 10 個不重複的證券代號')
    if buy_price_method not in ('vwap','close'):
        raise DataQualityError('buy price method must be vwap or close')
    rows=[]; problems=[]
    for code in codes:
        result=calculate(db,code,as_of=as_of,start_at=start_at,finance_ratio=finance_ratio,
                         buy_price_method=buy_price_method)
        if result is None:
            problems.append({'code':code,'kind':'missing_data'});continue
        latest=result['latest']; blocked=result['blocked']
        if blocked or latest is None or latest['date']!=result['last_observation']:
            problems.append({'code':code,'kind':'incomplete_calculation','blocked':blocked,
                             'last_calculated':latest['date'] if latest else None});continue
        if latest['raw_loan'] is None or latest['stock_value'] is None:
            problems.append({'code':code,'kind':'missing_price_or_loan'});continue
        official=db.scalars(select(Observation).where(Observation.code==code,Observation.trading_date==latest['date'])).all()
        if len(official)>1:
            problems.append({'code':code,'kind':'ambiguous_official_market'});continue
        if official:
            o=official[0]
            pairs={'close':(o.close,latest['close']),'balance':(o.balance,latest['balance']),
                   'previous_balance':(o.previous_balance,latest['previous_balance']),
                   'margin_buy':(o.margin_buy,latest['margin_buy']),'margin_sell':(o.margin_sell,latest['margin_sell']),
                   'repayment':(o.repayment,latest['repayment'])}
            mismatch=[name for name,(a,b) in pairs.items() if a is None or b is None or a!=b]
            if mismatch:
                problems.append({'code':code,'kind':'official_source_conflict','fields':mismatch});continue
        rows.append({'code':code,'name':official[0].name if official else None,
                     'market':official[0].market if official else None,
                     'source':result['source'],'date':latest['date'],'start':result['first_observation'],
                     'days':len(result['data']),'close':latest['close'],'balance':latest['balance'],
                     'estimated_cost':latest['estimated_cost'],'stock_value':latest['stock_value'],
                     'raw_loan':latest['raw_loan'],'raw_maintenance':latest['raw_maintenance'],
                     'initial_cost_sensitivity_pct':latest['initial_cost_sensitivity_pct'],
                     'warmup':latest['warmup'],'quality_flags':result['quality_flags'],
                     'official_fields_match':bool(official)})
    dates={x['date'] for x in rows}
    if len(dates)>1: problems.append({'kind':'inconsistent_as_of_dates','dates':sorted(dates)})
    total_value=sum((x['stock_value'] for x in rows),D(0))
    total_loan=sum((x['raw_loan'] for x in rows),D(0))
    ratio=total_value/total_loan*100 if not problems and len(rows)==len(codes) and total_loan>0 else None
    return {'type':'market_margin_basket_research','codes':codes,'requested_start_at':start_at,
            'requested_as_of':as_of,'date':next(iter(dates)) if len(dates)==1 else None,
            'finance_ratio':finance_ratio,'buy_price_method':buy_price_method,'items':rows,'issues':problems,
            'stock_value_total':total_value if ratio is not None else None,
            'raw_loan_total':total_loan if ratio is not None else None,
            'uncalibrated_maintenance_pct':ratio,'calibrated_maintenance_pct':None,
            'scope':'以四檔全市場融資部位合併估算，非投資人整戶維持率',
            'assumptions':['融資成數假設一致；每張假設1000股',
                           '初始既有融資部位以起算日'+('收盤價' if buy_price_method=='close' else '成交均價（缺值時收盤價）')+'初始化',
                           '未使用上市或上櫃官方融資總額校準','資料可能事後修訂，非 point-in-time 資料'],
            'calculation':'合計融資股票市值 / 合計未校準估計融資金額 × 100'}
