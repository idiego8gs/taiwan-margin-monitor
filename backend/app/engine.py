from dataclasses import dataclass
from decimal import Decimal

D = Decimal

class DataQualityError(ValueError):
    pass

@dataclass(frozen=True)
class Day:
    previous_balance: int
    buy: int
    sell: int
    cash_repayment: int
    balance: int
    volume_shares: int | None
    turnover: Decimal | None
    close: Decimal | None

@dataclass(frozen=True)
class Action:
    # cost = (previous cost - cash distribution) / share multiplier.
    # Only use verified, effective-date actions with known terms.
    share_multiplier: Decimal = D('1')
    cash_distribution: Decimal = D('0')

def adjusted_cost(cost: Decimal, action: Action) -> Decimal:
    if action.share_multiplier <= 0 or action.cash_distribution < 0 or cost <= action.cash_distribution:
        raise DataQualityError('invalid corporate action terms')
    return (cost - action.cash_distribution) / action.share_multiplier

def cost_for_day(day: Day, previous_cost: Decimal | None, action: Action | None = None,
                 *, buy_price_method: str = 'vwap'):
    if buy_price_method not in ('vwap', 'close'):
        raise DataQualityError('buy price method must be vwap or close')
    quantities = (day.previous_balance, day.buy, day.sell, day.cash_repayment, day.balance)
    if any(not isinstance(q, int) or q < 0 for q in quantities):
        raise DataQualityError('invalid or missing quantity')
    old = day.previous_balance - day.sell - day.cash_repayment
    if old < 0 or old + day.buy != day.balance:
        raise DataQualityError('balance identity mismatch')
    if day.balance == 0:
        return None, []
    flags = []
    price = None
    if buy_price_method == 'close':
        if day.close is not None and day.close > 0:
            price = day.close
    elif day.turnover is not None and day.turnover > 0 and day.volume_shares is not None and day.volume_shares > 0:
        price = day.turnover / day.volume_shares
    elif day.close is not None and day.close > 0:
        price = day.close
        flags.append('close_fallback')
    if previous_cost is None and day.previous_balance:
        if action is not None:
            raise DataQualityError('action cannot be applied without previous cost')
        if price is None:
            raise DataQualityError('warm-up requires a price')
        previous_cost = price
        flags.append('warm_up')
    if action is not None and previous_cost is not None:
        previous_cost = adjusted_cost(previous_cost, action)
        flags.append('corporate_action')
    if old and (previous_cost is None or previous_cost <= 0):
        raise DataQualityError('missing previous cost')
    if day.buy and price is None:
        raise DataQualityError('missing buy price')
    return (D(old) * (previous_cost or D(0)) + D(day.buy) * (price or D(0))) / day.balance, flags

def metrics(balance: int, cost: Decimal | None, close: Decimal | None, finance_ratio: Decimal = D('0.6'), calibration_factor: Decimal | None = None):
    if balance < 0 or finance_ratio <= 0 or finance_ratio > 1:
        raise DataQualityError('invalid balance or finance ratio')
    if calibration_factor is not None and calibration_factor <= 0:
        raise DataQualityError('invalid calibration factor')
    if not balance or cost is None or close is None or cost <= 0 or close <= 0:
        return None
    raw_loan = D(balance) * 1000 * cost * finance_ratio
    value = D(balance) * 1000 * close
    loan = raw_loan * (calibration_factor if calibration_factor is not None else D(1))
    reference = loan * D('1.3') / (D(balance) * 1000)
    return {'raw_loan': raw_loan, 'stock_value': value, 'raw_maintenance': value / raw_loan * 100,
            'estimated_loan': loan, 'estimated_maintenance': value / loan * 100,
            'reference_price': reference, 'distance_pct': (close - reference) / close * 100,
            'profit_pct': (close / cost - 1) * 100, 'calibrated': calibration_factor is not None}

def calibration(official: Decimal | None, raw_loans: list[Decimal], scope_matches: bool):
    if not scope_matches or official is None:
        return None
    if official <= 0 or any(x < 0 for x in raw_loans):
        raise DataQualityError('invalid calibration inputs')
    total = sum(raw_loans, D(0))
    return official / total if total > 0 else None
