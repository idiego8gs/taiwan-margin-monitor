"""Parse daily official market financing totals without inferring stock-level loans."""
from decimal import Decimal, InvalidOperation
import json
from sqlalchemy import select
from .db import RawResponse


def _amount(value):
    try: result=Decimal(str(value).replace(',','').strip())
    except (InvalidOperation,ValueError): return None
    if not result.is_finite() or result<=0 or result!=result.to_integral_value():return None
    return result*1000  # both official reports label these totals in thousands of NTD


def parse_total(payload, market, day):
    try: obj=json.loads(payload)
    except (ValueError,TypeError): return None
    if not isinstance(obj,dict) or obj.get('date')!=day.strftime('%Y%m%d'):return None
    if market=='TWSE':
        if obj.get('stat')!='OK':return None
        for t in obj.get('tables',[]):
            fields=t.get('fields',[])
            if '項目' not in fields or '今日餘額' not in fields:continue
            label=fields.index('項目');value=fields.index('今日餘額')
            rows=[r for r in t.get('data',[]) if len(r)>max(label,value) and r[label]=='融資金額(仟元)']
            if len(rows)==1:return _amount(rows[0][value])
    if market=='TPEx':
        if str(obj.get('stat','')).lower()!='ok':return None
        for t in obj.get('tables',[]):
            fields=[str(f).strip() for f in t.get('fields',[])]
            if '資餘額' not in fields:continue
            value=fields.index('資餘額')
            rows=[r for r in t.get('summary',[]) if len(r)>max(1,value) and r[1]=='融資金(仟元)']
            if len(rows)==1:return _amount(rows[0][value])
    return None


def find_market_total(db, market, day):
    if market not in ('TWSE','TPEx') or day is None:return None
    marker='marginTrading/MI_MARGN?' if market=='TWSE' else 'margin_bal_result.php?'
    responses=db.scalars(select(RawResponse).where(RawResponse.trading_date==day,
                         RawResponse.source.contains(marker)).order_by(RawResponse.id.desc())).all()
    for response in responses:
        value=parse_total(response.payload,market,day)
        if value is not None:
            return {'date':day,'market':market,'amount_ntd':value,'source_url':response.source,
                    'note':'官方整體市場融資金額；尚未核實模型分母的標的範圍是否一致'}
    return None
