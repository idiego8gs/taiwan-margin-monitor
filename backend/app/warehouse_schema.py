"""Versioned warehouse; legacy tables remain available to existing API clients."""
from datetime import datetime, timezone
from sqlalchemy import (Table, Column as C, Integer as I, BigInteger as B, String as S,
    Text as T, Date as D, DateTime as DT, Numeric as N, Boolean, ForeignKey as FK,
    UniqueConstraint as U, CheckConstraint as CK, Index)
from .db import Base

def now(): return datetime.now(timezone.utc)
def table(name, *columns, **kw): return Table(name, Base.metadata, *columns, **kw)
def ident(): return C('id', I, primary_key=True)
def security(): return C('security_id', I, FK('securities.id'), nullable=False)
def capture(): return C('capture_id', I, FK('source_captures.id'), nullable=False)
def created(): return C('created_at', DT(timezone=True), nullable=False, default=now)

securities=table('securities', ident(), C('identity_key', S(120), nullable=False, unique=True),
    C('code', S(20), nullable=False, index=True), C('name', S(120), nullable=False),
    C('identity_status', S(40), nullable=False), created())
listings=table('security_listings', ident(), security(), C('market', S(12), nullable=False),
    C('valid_from', D), C('valid_to', D), C('source_key', S(200), nullable=False, unique=True),
    C('terms_json', T, nullable=False), C('verified', Boolean, nullable=False, default=False))
states=table('security_states', ident(), security(), C('kind', S(40), nullable=False),
    C('valid_from', D, nullable=False), C('valid_to', D), C('value_json', T, nullable=False),
    C('source_key', S(200), nullable=False, unique=True), C('verified', Boolean, nullable=False, default=False))
calendar=table('trading_calendar', ident(), C('market', S(12), nullable=False),
    C('trading_date', D, nullable=False), C('is_open', Boolean, nullable=False),
    C('source_key', S(200), nullable=False), U('market','trading_date','source_key'))
blobs=table('source_blobs', C('sha256', S(64), primary_key=True), C('payload', T, nullable=False),
    C('encoding', S(30), nullable=False, default='utf-8-text'), C('bytes_count', B, nullable=False))
captures=table('source_captures', ident(), C('capture_key', S(240), nullable=False, unique=True),
    C('provider', S(30), nullable=False), C('dataset', S(100), nullable=False), C('code', S(20)),
    C('market', S(12)), C('start_date', D), C('end_date', D), C('source_url', T, nullable=False),
    C('fetched_at', DT(timezone=True)), C('imported_at', DT(timezone=True), nullable=False, default=now),
    C('published_at', DT(timezone=True)), C('availability', S(40), nullable=False),
    C('http_status', I, nullable=False), C('sha256', S(64), FK('source_blobs.sha256'), nullable=False),
    C('parser_version', S(40), nullable=False), C('request_json', T, nullable=False))

def daily_columns():
    return [ident(),security(),C('trading_date',D,nullable=False),C('provider',S(30),nullable=False),
        C('market',S(12),nullable=False),C('basis',S(50),nullable=False),capture(),
        C('value_sha256',S(64),nullable=False),C('observed_at',DT(timezone=True),nullable=False),
        C('is_current',Boolean,nullable=False,default=True)]
def daily_unique(): return U('security_id','trading_date','provider','basis','value_sha256')
prices=table('daily_prices',*daily_columns(),*[C(k,N(28,10)) for k in ('open','high','low','close')],
    C('volume_shares',B),C('turnover_ntd',N(30,6)),C('source_unit',S(30),nullable=False),
    C('unit_multiplier',N(28,10),nullable=False),daily_unique(),
    CK('volume_shares IS NULL OR volume_shares >= 0','price_volume_nonnegative'))
margins=table('daily_margins',*daily_columns(),
    *[C(k,B) for k in ('margin_buy','margin_sell','repayment','previous_balance','balance','margin_limit')],
    C('source_unit',S(30),nullable=False),C('unit_multiplier',N(28,10),nullable=False),
    C('balance_shares',N(30,6)),C('identity_valid',Boolean),daily_unique(),
    CK('balance IS NULL OR balance >= 0','margin_balance_nonnegative'))
for t in (prices,margins):
    Index(f'ix_{t.name}_security_day_current',t.c.security_id,t.c.trading_date,t.c.is_current)
    Index(f'ix_{t.name}_market_day',t.c.market,t.c.trading_date)
    Index(f'ux_{t.name}_current',t.c.security_id,t.c.trading_date,t.c.provider,t.c.basis,
          unique=True,postgresql_where=t.c.is_current==True,sqlite_where=t.c.is_current==True)
actions=table('corporate_actions',ident(),security(),capture(),C('event_key',S(200),nullable=False,unique=True),
    C('kind',S(60),nullable=False),C('effective_date',D),C('announced_at',DT(timezone=True)),
    C('terms_json',T,nullable=False),C('verified',Boolean,nullable=False,default=False),
    C('share_multiplier',N(28,10)),C('cash_per_share',N(28,10)))
totals=table('market_totals',ident(),C('market',S(12),nullable=False),C('trading_date',D,nullable=False),
    capture(),C('amount_ntd',N(30,6),nullable=False),C('scope_json',T,nullable=False),
    U('market','trading_date','capture_id'))
indices=table('market_indices',ident(),C('market',S(12),nullable=False),C('trading_date',D,nullable=False),
    C('index_kind',S(40),nullable=False),C('close',N(28,10),nullable=False),capture(),
    U('market','trading_date','index_kind','capture_id'))
universes=table('universe_versions',C('id',S(64),primary_key=True),C('name',S(100),nullable=False),
    C('as_of',D),C('pit_verified',Boolean,nullable=False,default=False),C('definition_json',T,nullable=False),created())
members=table('universe_members',ident(),C('universe_id',S(64),FK('universe_versions.id'),nullable=False),security(),
    C('rank',I),U('universe_id','security_id'))
issues=table('quality_issues',ident(),C('issue_key',S(240),nullable=False,unique=True),
    C('security_id',I,FK('securities.id')),C('trading_date',D),C('kind',S(80),nullable=False,index=True),
    C('status',S(40),nullable=False,index=True),C('severity',S(20),nullable=False),
    C('evidence_json',T,nullable=False),C('first_seen_at',DT(timezone=True),nullable=False,default=now),
    C('last_seen_at',DT(timezone=True),nullable=False,default=now),C('resolved_at',DT(timezone=True)))
jobs=table('ingestion_jobs',C('id',S(36),primary_key=True),C('dedupe_key',S(240),nullable=False,unique=True),
    C('priority',I,nullable=False,default=100,server_default='100'),
    C('kind',S(60),nullable=False),C('status',S(30),nullable=False,index=True),C('params_json',T,nullable=False),
    C('checkpoint_json',T,nullable=False,default='{}'),C('result_json',T),C('attempts',I,nullable=False,default=0),
    C('max_attempts',I,nullable=False,default=5),C('next_attempt_at',DT(timezone=True),nullable=False,default=now),
    C('heartbeat_at',DT(timezone=True)),C('lease_until',DT(timezone=True)),C('worker_id',S(120)),
    C('last_error',T),created(),C('updated_at',DT(timezone=True),nullable=False,default=now))
limits=table('provider_limits',C('provider',S(30),primary_key=True),C('next_allowed_at',DT(timezone=True),nullable=False),
    C('interval_seconds',I,nullable=False),CK('interval_seconds > 0','positive_interval'))
segments=table('estimate_segments',C('id',S(64),primary_key=True),security(),C('model_version',S(80),nullable=False),
    C('data_version',S(64),nullable=False),C('start_date',D,nullable=False),C('end_date',D),
    C('break_json',T),C('warmup',Boolean,nullable=False))
estimates=table('daily_estimates',ident(),security(),C('trading_date',D,nullable=False),
    C('model_version',S(80),nullable=False),C('data_version',S(64),nullable=False),
    C('segment_id',S(64),FK('estimate_segments.id'),nullable=False),
    C('estimated_cost',N(28,10)),C('raw_maintenance',N(28,10)),C('finance_ratio',N(20,10),nullable=False),
    C('calibration_factor',N(28,10)),C('quality_json',T,nullable=False),C('metrics_json',T,nullable=False),
    U('security_id','trading_date','model_version','data_version'))
snapshots=table('published_snapshots',C('id',S(64),primary_key=True),C('as_of',D,nullable=False),
    C('status',S(30),nullable=False),C('data_version',S(64),nullable=False),C('summary_json',T,nullable=False),created())
runs=table('research_runs',C('id',S(64),primary_key=True),C('data_version',S(64),nullable=False),
    C('protocol_json',T,nullable=False),C('status',S(30),nullable=False),created())
results=table('research_results',ident(),C('run_id',S(64),FK('research_runs.id'),nullable=False),
    C('result_key',S(200),nullable=False),C('metrics_json',T,nullable=False),U('run_id','result_key'))
executions=table('research_execution',C('run_id',S(64),FK('research_runs.id'),primary_key=True),
    C('heartbeat_at',DT(timezone=True)),C('progress_json',T,nullable=False),C('last_error',T),
    C('updated_at',DT(timezone=True),nullable=False,default=now))
research_snapshots=table('research_snapshots',C('id',S(64),primary_key=True),C('data_version',S(64),nullable=False),
    C('start_date',D,nullable=False),C('end_date',D,nullable=False),C('path',T,nullable=False),
    C('manifest_json',T,nullable=False),created())
eligibility=table('research_eligibility',ident(),C('snapshot_id',S(64),FK('research_snapshots.id'),nullable=False),
    security(),C('trading_date',D,nullable=False),C('segment_id',S(64)),
    C('price_status',S(30),nullable=False),C('margin_status',S(30),nullable=False),
    C('strict',Boolean,nullable=False),C('exploratory',Boolean,nullable=False),C('reasons_json',T,nullable=False),
    U('snapshot_id','security_id','trading_date'))
Index('ix_eligibility_snapshot_date',eligibility.c.snapshot_id,eligibility.c.trading_date)
rules=table('alert_rules',ident(),security(),C('owner_key',S(120),nullable=False),C('rule_json',T,nullable=False),
    C('enabled',Boolean,nullable=False,default=True))
alerts=table('alert_events',ident(),C('rule_id',I,FK('alert_rules.id'),nullable=False),
    C('snapshot_id',S(64),FK('published_snapshots.id'),nullable=False),C('event_json',T,nullable=False),
    C('read_at',DT(timezone=True)),created(),U('rule_id','snapshot_id'))
checks=table('data_checks',C('name',S(100),primary_key=True),C('summary_json',T,nullable=False),
    C('checked_at',DT(timezone=True),nullable=False,default=now))
Index('ix_issues_security_kind_day',issues.c.security_id,issues.c.kind,issues.c.trading_date)
Index('ix_captures_lookup',captures.c.provider,captures.c.code,captures.c.dataset,captures.c.start_date,captures.c.end_date)
Index('ix_jobs_claim',jobs.c.status,jobs.c.priority,jobs.c.next_attempt_at,jobs.c.created_at)
