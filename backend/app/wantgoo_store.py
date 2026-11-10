"""Atomically register a checked Wantgoo rebuild as an independent warehouse vintage."""
import argparse
import csv
from datetime import date
import gzip
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path

from .db import engine
from .wantgoo_rebuild import MODEL


def packed(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def archive_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as archive:
        yield from csv.DictReader(archive)


def store(root):
    summary = json.loads((root / 'market.json').read_text(encoding='utf-8'))['summary']
    if summary['model'] != MODEL:
        raise ValueError('Unexpected model')
    version = summary['dataVersion']
    snapshot = sha256(f'{MODEL}:{version}'.encode()).hexdigest()
    archive = root / 'stock_days.csv.gz'
    segment_rows, stocks, count = [], set(), 0
    for security, rows in groupby(archive_rows(archive), key=lambda row: row['security_id']):
        stocks.add(security)
        for index, ((segment, status), items) in enumerate(groupby(rows, key=lambda row: (
                row['segment'], '' if row['segment'] else row['status']))):
            batch = list(items)
            count += len(batch)
            sid = sha256(f'{snapshot}:{security}:{index}'.encode()).hexdigest()
            segment_rows.append((sid, int(security), MODEL, version, date.fromisoformat(batch[0]['date']),
                                 date.fromisoformat(batch[-1]['date']), packed({'status': status or 'calculated'}),
                                 any(float(r['seed_weight'] or '0') > .05 for r in batch)))
    if count != summary['stockDays'] or len(stocks) > summary['stocks']:
        raise ValueError('Archive does not match the checked result')
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (MODEL,))
            cur.execute('SELECT 1 FROM published_snapshots WHERE id = %s', (snapshot,))
            if cur.fetchone():
                cur.execute('SELECT count(*) FROM daily_estimates WHERE model_version = %s AND data_version = %s', (MODEL, version))
                if cur.fetchone()[0] != count:
                    raise ValueError('Existing warehouse vintage is incomplete')
                return {'snapshot': snapshot, 'rows': count, 'alreadyStored': True}
            with cur.copy('''COPY estimate_segments (id,security_id,model_version,data_version,start_date,end_date,break_json,warmup) FROM STDIN''') as copy:
                for row in segment_rows:
                    copy.write_row(row)
            segment_iter = iter(segment_rows)
            with cur.copy('''COPY daily_estimates (security_id,trading_date,model_version,data_version,segment_id,
                          estimated_cost,raw_maintenance,finance_ratio,quality_json,metrics_json) FROM STDIN''') as copy:
                for security, rows in groupby(archive_rows(archive), key=lambda row: row['security_id']):
                    for _, items in groupby(rows, key=lambda row: (row['segment'], '' if row['segment'] else row['status'])):
                        sid, segment_security, *_ = next(segment_iter)
                        if int(security) != segment_security:
                            raise ValueError('Archive changed during import')
                        for r in items:
                            seed = float(r['seed_weight']) if r['seed_weight'] else None
                            quality = dict(status=r['status'], seed_type=r['seed_type'] or None, seed_weight=seed,
                                           warmup=seed is not None and seed > .05, balance_revision=r['balance_revision'] == 'True',
                                           corporate_action=r['corporate_action'] or None, vwap_source=r['vwap_source'] or None,
                                           price_provider=r['price_source'] or None, margin_provider=r['margin_source'] or None,
                                           price_revision=r['price_revision'] or None, margin_revision=r['margin_revision'] or None)
                            metrics = {field: r[field] or None for field in ('close', 'vwap', 'lots', 'buys',
                                       'previous_lots', 'reported_previous_lots', 'collateral_ntd', 'estimated_debt_ntd')}
                            copy.write_row((int(security), date.fromisoformat(r['date']), MODEL, version, sid,
                                            r['cost'] or None, r['maintenance_pct'] or None, '.6', packed(quality), packed(metrics)))
            cur.execute('SELECT count(*) FROM daily_estimates WHERE model_version = %s AND data_version = %s', (MODEL, version))
            if cur.fetchone()[0] != count:
                raise ValueError('Imported row count mismatch')
            metadata = {**summary, 'model_version': MODEL, 'data_version': version, 'calibration': 'none'}
            cur.execute('''INSERT INTO published_snapshots (id,as_of,status,data_version,summary_json,created_at)
                           VALUES (%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)''',
                        (snapshot, date.fromisoformat(summary['lastDate']), 'research_only', version, packed(metadata)))
        connection.commit()
    finally:
        connection.close()
    return {'snapshot': snapshot, 'rows': count, 'alreadyStored': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checked_output', type=Path)
    print(packed(store(parser.parse_args().checked_output)))
