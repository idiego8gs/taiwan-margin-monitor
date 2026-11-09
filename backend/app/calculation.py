"""Research estimates. Every assumption is explicit and results are not account maintenance ratios."""
from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
import json
from sqlalchemy import select
from .db import Observation, ProviderObservation, ProviderResponse
from .engine import Action, Day, DataQualityError, cost_for_day, metrics
from .market_totals import find_market_total
from .trading_calendar import parse_calendar
from .finmind import ProviderError

D=Decimal
MODEL_VERSION='research-v1.0.0'
DIVIDEND='TaiwanStockDividend'
DIVIDEND_RESULT='TaiwanStockDividendResult'
SPLIT='TaiwanStockSplitPrice'
REDUCTION='TaiwanStockCapitalReductionReferencePrice'

@dataclass(frozen=True)
class Event:
    day: date
    action: Action | None
    kind: str
    source: str
    reason: str | None = None


def _archived_response(db, dataset, code, start, end):
    return db.scalar(select(ProviderResponse).where(ProviderResponse.provider=='FinMind',
        ProviderResponse.dataset==dataset,ProviderResponse.code==code,
        ProviderResponse.start_date<=start,ProviderResponse.end_date>=end,
        ProviderResponse.http_status==200).order_by(ProviderResponse.id.desc()).limit(1))


def _data(response):
    try:
        obj=json.loads(response.payload,parse_float=Decimal)
        if obj.get('status')!=200 or not isinstance(obj.get('data'),list): raise ValueError()
        return obj['data']
    except (ValueError,TypeError,AttributeError):
        raise DataQualityError('invalid archived corporate-action response') from None


def load_actions(db, code, start, end):
    """Use verified cash ex-dividend events; block share actions until terms are known."""
    responses={dataset:_archived_response(db,dataset,code,start,end)
               for dataset in (DIVIDEND,DIVIDEND_RESULT,SPLIT,REDUCTION)}
    warnings=[]; events={}
    if any(not item for item in responses.values()):
        warnings.append('corporate_action_data_incomplete')
    # Other categories (e.g. face-value changes) are not yet covered.
    warnings.append('share_action_coverage_unverified')
    precise_cash={}
    if responses[DIVIDEND]:
        for row in _data(responses[DIVIDEND]):
            if row.get('stock_id')!=code: raise DataQualityError('unexpected dividend stock')
            value=row.get('CashExDividendTradingDate')
            if not value: continue
            try:
                day=date.fromisoformat(value)
                amount=D(str(row.get('CashEarningsDistribution',0)))+D(str(row.get('CashStatutorySurplus',0)))
                announced=date.fromisoformat(row['AnnouncementDate']) if row.get('AnnouncementDate') else None
            except (ValueError,InvalidOperation,TypeError):
                raise DataQualityError('invalid cash dividend terms') from None
            if not amount.is_finite() or amount<0: raise DataQualityError('invalid cash dividend amount')
            if start<=day<=end and amount>0:
                if day in precise_cash: raise DataQualityError('multiple dividend records on the same date')
                precise_cash[day]=(amount,announced)
    if responses[DIVIDEND_RESULT]:
        for row in _data(responses[DIVIDEND_RESULT]):
            if row.get('stock_id')!=code: raise DataQualityError('unexpected ex-dividend stock')
            try: day=date.fromisoformat(row['date'])
            except (ValueError,KeyError,TypeError): raise DataQualityError('invalid ex-dividend date') from None
            if not start<=day<=end: continue
            if day in events: raise DataQualityError('multiple ex-right events on the same date')
            event_type=str(row.get('stock_or_cache_dividend','')).strip()
            # TWSE uses "息" while TPEx uses "除息" for the same cash event.
            if event_type not in ('息','除息'):
                events[day]=Event(day,None,'unhandled_ex_right','FinMind '+DIVIDEND_RESULT,
                                  '除權或混合事件需要確認股數與配股條件')
                continue
            try: rounded=D(str(row['stock_and_cache_dividend']))
            except (KeyError,InvalidOperation,TypeError): raise DataQualityError('invalid ex-dividend amount') from None
            if not rounded.is_finite() or rounded<=0: raise DataQualityError('invalid ex-dividend amount')
            amount=rounded;source='FinMind '+DIVIDEND_RESULT
            if day in precise_cash:
                precise,announced=precise_cash[day]
                if abs(precise-rounded)>D('0.01'):
                    raise DataQualityError('cash dividend source mismatch')
                # An announcement after the effective date cannot be used as an as-of input.
                if announced is not None and announced<=day:
                    amount=precise;source='FinMind '+DIVIDEND+' + '+DIVIDEND_RESULT
                else:
                    warnings.append('dividend_announcement_date_unverified')
            else:
                warnings.append('dividend_announcement_date_unverified')
            events[day]=Event(day,Action(cash_distribution=amount),'cash_dividend',source)
    # A policy event without a matching result cannot be silently omitted.
    for day in precise_cash:
        if day not in events:
            events[day]=Event(day,None,'missing_dividend_result','FinMind '+DIVIDEND,
                              '已偵測股利，但缺少除息結果核對')
    for dataset in (SPLIT,REDUCTION):
        if responses[dataset]:
            for row in _data(responses[dataset]):
                if row.get('stock_id')!=code: raise DataQualityError('unexpected share-action stock')
                try: day=date.fromisoformat(row['date'])
                except (ValueError,KeyError,TypeError): raise DataQualityError('invalid share-action date') from None
                if start<=day<=end:
                    events[day]=Event(day,None,'unhandled_share_action','FinMind '+dataset,
                                      '分割、減資等事件需確認實際股數倍率')
    return events, sorted(set(warnings))


def load_calendar(db, start, end):
    response=_archived_response(db,'TaiwanStockTradingDate','MARKET',start,end)
    if not response: return None
    try:
        return set(parse_calendar(response.payload,response.start_date,response.end_date))
    except ProviderError as exc:
        raise DataQualityError('invalid archived trading calendar') from exc


def build_series(rows, *, events=None, calendar=None, finance_ratio=D('0.6'),
                 reconcile_decreases=False, warmup_assumed=False, buy_price_method='vwap'):
    """Return daily estimates through the first unresolvable error, never inventing trades.

    For an explicit research scenario only, a downward opening-balance revision keeps
    the prior average cost unchanged (proportional reduction). All such dates are logged.
    """
    if not finance_ratio.is_finite() or finance_ratio<=0 or finance_ratio>1:
        raise DataQualityError('finance ratio must be in (0,1]')
    if buy_price_method not in ('vwap','close'):
        raise DataQualityError('buy price method must be vwap or close')
    events=events or {}; issues=[]; output=[]; applied=[]
    previous_balance=None; previous_cost=None; warmup=warmup_assumed; reconciled=False
    initial_cost_sensitivity=D(0)
    previous_day=None; blocked=None
    ordered_calendar=sorted(calendar) if calendar is not None else None
    for row in rows:
        day=row.trading_date
        if previous_day is not None and day<=previous_day:
            blocked={'date':day,'kind':'unordered_or_duplicate_date'};break
        if calendar is not None:
            if day not in calendar:
                blocked={'date':day,'kind':'date_not_in_calendar'};break
            missing=(ordered_calendar[bisect_right(ordered_calendar,previous_day):bisect_left(ordered_calendar,day)]
                     if previous_day is not None else [])
            if missing:
                blocked={'date':day,'kind':'missing_trading_dates','missing_dates':missing};break
        required=[row.previous_balance,row.margin_buy,row.margin_sell,row.repayment,row.balance]
        if any(v is None for v in required):
            blocked={'date':day,'kind':'missing_margin_data'};break
        event=events.get(day)
        if event and event.action is None:
            blocked={'date':day,'kind':event.kind,'detail':event.reason,'source':event.source};break
        old=row.previous_balance-row.margin_sell-row.repayment
        if old<0 or old+row.margin_buy!=row.balance:
            blocked={'date':day,'kind':'balance_identity_mismatch',
                     'previous_balance':row.previous_balance,'buy':row.margin_buy,'sell':row.margin_sell,
                     'cash_repayment':row.repayment,'balance':row.balance};break
        if previous_balance is not None:
            expected=D(previous_balance)*(event.action.share_multiplier if event else D(1))
            if D(row.previous_balance)!=expected:
                detail={'date':day,'kind':'previous_balance_conflict','previous_date':previous_day,
                        'previous_day_balance':previous_balance,'expected_after_action':expected,
                        'reported_previous_balance':row.previous_balance}
                # Never assign a cost to unexplained newly added positions.
                if not reconcile_decreases or D(row.previous_balance)>expected:
                    blocked=detail;break
                detail['assumption']='按比例減少舊部位，平均成本不變；非已確認交易'
                issues.append(detail);reconciled=True
        # Once all prior lots are gone, inherited warm-up and revision uncertainty reset.
        if old==0:
            warmup=False; reconciled=False
            initial_cost_sensitivity=D(0)
        elif previous_balance is None and row.previous_balance>0:
            warmup=True
            initial_cost_sensitivity=D(1)
        try:
            model_day=Day(row.previous_balance,row.margin_buy,row.margin_sell,row.repayment,row.balance,
                          row.volume,D(row.turnover) if row.turnover is not None else None,
                          D(row.close) if row.close is not None else None)
            cost,flags=cost_for_day(model_day,previous_cost,event.action if event else None,
                                    buy_price_method=buy_price_method)
        except DataQualityError as exc:
            blocked={'date':day,'kind':'invalid_cost_input','detail':str(exc)};break
        if event:
            applied.append({'date':day,'kind':event.kind,'cash_distribution':event.action.cash_distribution,
                            'share_multiplier':event.action.share_multiplier,'source':event.source})
        if row.balance>0:
            multiplier=event.action.share_multiplier if event else D(1)
            initial_cost_sensitivity=initial_cost_sensitivity*D(old)/D(row.balance)/multiplier
        else:
            initial_cost_sensitivity=D(0)
        stat=metrics(row.balance,cost,D(row.close) if row.close is not None else None,finance_ratio)
        result={'date':day,'close':row.close,'estimated_cost':cost,'balance':row.balance,
                'margin_buy':row.margin_buy,'margin_sell':row.margin_sell,'repayment':row.repayment,
                'previous_balance':row.previous_balance,'margin_limit':row.limit,
                'volume_shares':row.volume,'turnover':row.turnover,
                'price_basis':('close' if buy_price_method=='close' and row.close is not None and row.close>0
                               else 'missing' if buy_price_method=='close'
                               else 'vwap' if row.volume and row.volume>0 and row.turnover is not None and row.turnover>0
                               else 'close_fallback' if row.close is not None and row.close>0 else 'missing'),
                'warmup':warmup,'initial_cost_sensitivity_pct':initial_cost_sensitivity*100,
                'reconciled_balance':reconciled,'finance_ratio':finance_ratio,
                'calibration_factor':None,'calibrated_maintenance':None,'estimated_loan':None,
                'raw_loan':stat['raw_loan'] if stat else None,
                'stock_value':stat['stock_value'] if stat else None,
                'raw_maintenance':stat['raw_maintenance'] if stat else None,
                'reference_price_uncalibrated':stat['reference_price'] if stat else None,
                'distance_pct_uncalibrated':stat['distance_pct'] if stat else None,
                'stock_profit_pct':stat['profit_pct'] if stat else None,
                'margin_utilization_pct':D(row.balance)/row.limit*100 if row.limit and row.limit>0 else None,
                'margin_to_volume_pct':D(row.balance)*1000/row.volume*100 if row.volume and row.volume>0 else None}
        for n in (1,5,20):
            prev=output[-n] if len(output)>=n else None
            result[f'balance_change_{n}d']=row.balance-prev['balance'] if prev else None
            a=result['raw_maintenance'];b=prev['raw_maintenance'] if prev else None
            result[f'maintenance_change_{n}d_pp']=a-b if a is not None and b is not None else None
        output.append(result);previous_cost=cost;previous_balance=row.balance;previous_day=day
        if row.balance==0: warmup=False;reconciled=False
    return {'items':output,'blocked':blocked,'reconciliations':issues,'actions':applied}


def calculate(db, code, *, as_of=None, start_at=None, finance_ratio=D('0.6'), reconcile_decreases=False,
              buy_price_method='vwap'):
    # Never join market-price series with different trading scopes into one cost series.
    today=datetime.now(ZoneInfo('Asia/Taipei')).date()
    effective_as_of=as_of or today
    if start_at is not None and start_at>effective_as_of:
        raise DataQualityError('start date must not be after as-of date')
    query=select(ProviderObservation).where(ProviderObservation.provider=='FinMind',ProviderObservation.code==code)
    oq=select(Observation).where(Observation.code==code)
    query=query.where(ProviderObservation.trading_date<=effective_as_of)
    oq=oq.where(Observation.trading_date<=effective_as_of)
    if start_at is not None:
        query=query.where(ProviderObservation.trading_date>=start_at)
        oq=oq.where(Observation.trading_date>=start_at)
    research=db.scalars(query.order_by(ProviderObservation.trading_date)).all()
    official=db.scalars(oq.order_by(Observation.trading_date)).all()
    if not research and not official: return None
    if research and (not official or research[-1].trading_date>official[-1].trading_date or
                     (research[-1].trading_date==official[-1].trading_date and len(research)>len(official))):
        rows=research;source='FinMind';market=official[-1].market if official else None
    else:
        market=official[-1].market
        rows=[x for x in official if x.market==market];source='official'
    start,end=rows[0].trading_date,rows[-1].trading_date
    events,warnings=load_actions(db,code,start,end)
    calendar=load_calendar(db,start,end)
    if calendar is None: warnings.append('trading_calendar_unverified')
    warnings.extend(['uncalibrated','finance_ratio_assumed','non_point_in_time_source',
                     'individual_not_account_maintenance','share_unit_assumed_1000'])
    if start_at is not None: warnings.append('user_specified_start_date')
    result=build_series(rows,events=events,calendar=calendar,finance_ratio=finance_ratio,
                        reconcile_decreases=reconcile_decreases,buy_price_method=buy_price_method)
    latest=result['items'][-1] if result['items'] else None
    if source=='FinMind':
        # A market classification observed later must not be assumed for an earlier result.
        same_day=[x for x in official if latest and x.trading_date==latest['date']]
        market=same_day[0].market if len(same_day)==1 else None
    market_total=find_market_total(db,market,latest['date']) if latest else None
    if latest and latest['warmup']: warnings.append('warmup_unresolved')
    if latest and latest['reconciled_balance']: warnings.append('balance_revision_assumed')
    if any(x['price_basis']=='close_fallback' and (x['margin_buy']>0 or x['warmup']) for x in result['items']):
        warnings.append('close_fallback_used')
    if latest and latest['date']==today: warnings.append('current_day_preliminary')
    return {'code':code,'market':market,'model_version':MODEL_VERSION,'source':source,
            'first_observation':start,'last_observation':end,'requested_as_of':as_of,
            'requested_start_at':start_at,'buy_price_method':buy_price_method,
            'effective_as_of':effective_as_of,
            'latest':latest,'data':result['items'],'blocked':result['blocked'],
            'reconciliations':result['reconciliations'],'actions':result['actions'],
            'official_market_loan_total':market_total,
            'quality_flags':sorted(set(warnings)),'calendar_verified_against_finmind':calendar is not None,
            'calibration_status':'unavailable_denominator_or_scope_not_verified','scope':'research_only',
            'reconciliation_policy':'proportional_decreases' if reconcile_decreases else 'strict'}
