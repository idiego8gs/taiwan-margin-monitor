"""Restore a dump into a NEW disposable database and compare its core table counts.

Never restores over margin. The validation DB is kept for inspection unless --drop
is explicitly supplied, and its name is generated/validated by this module.
"""
import argparse,json,os,re,subprocess,uuid
from pathlib import Path
import psycopg
from psycopg import sql
from .warehouse import encode

ROOT=Path(__file__).resolve().parents[2]
def restore(path,drop=False):
    settings=json.loads((ROOT/'.local/database.json').read_text())
    name='margin_restore_'+uuid.uuid4().hex[:12]
    assert re.fullmatch(r'margin_restore_[0-9a-f]{12}',name)
    options=dict(host='127.0.0.1',port=settings['port'],user='margin_admin',password=settings['admin_password'])
    with psycopg.connect(dbname='postgres',autocommit=True,**options) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    env=os.environ.copy();env['PGPASSWORD']=settings['admin_password']
    subprocess.run([str(ROOT/'.local/pgsql/bin/pg_restore.exe'),'-h','127.0.0.1','-p',str(settings['port']),'-U','margin_admin',
        '-d',name,'--exit-on-error','--no-owner','--no-privileges',str(Path(path).resolve())],env=env,check=True)
    counts={}
    with psycopg.connect(dbname=name,**options) as c:
        tables=c.execute("select tablename from pg_tables where schemaname='public' order by tablename").fetchall()
        for (table,) in tables:counts[table]=c.execute(sql.SQL('select count(*) from {}').format(sql.Identifier(table))).fetchone()[0]
        revision=c.execute('select version_num from alembic_version').fetchone()[0]
        precision=c.execute('select close from provider_observations where code=%s order by trading_date limit 1',('2330',)).fetchone()
    result={'restored_database':name,'revision':revision,'table_counts':counts,'example_price':precision[0] if precision else None,'success':True}
    baseline=ROOT/'research/database_migration.json'
    if baseline.exists():
        from sqlalchemy import create_engine
        from .migrate_legacy import checksum,TABLES
        from .db import Base
        expected=json.loads(baseline.read_text())
        restored=create_engine(f"postgresql+psycopg://margin_admin:{settings['admin_password']}@127.0.0.1:{settings['port']}/{name}")
        with restored.connect() as c:
            checks={t:checksum(c,Base.metadata.tables[t])==expected[t] for t in TABLES if counts[t]==expected[t]['count']}
        restored.dispose();result['unchanged_baseline_value_checks']=checks
        if not all(checks.values()):raise ValueError('baseline exact-value restore validation failed')
    manifest=Path(str(path)+'.manifest.json')
    if manifest.exists():
        expected=json.loads(manifest.read_text())['table_counts']
        if counts!=expected:raise ValueError('restored table counts differ from dump snapshot')
        result['manifest_verified']=True
    if drop:
        with psycopg.connect(dbname='postgres',autocommit=True,**options) as c:c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        result['validation_database_dropped']=True
    return result
def main():
    p=argparse.ArgumentParser();p.add_argument('dump');p.add_argument('--drop',action='store_true');a=p.parse_args()
    result=restore(a.dump,a.drop)
    (ROOT/'research/postgresql_restore_check.json').write_text(encode(result),encoding='utf8');print(encode(result))
if __name__=='__main__':main()
