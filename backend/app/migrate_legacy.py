"""Copy the frozen SQLite baseline into a migrated PostgreSQL, then compare every value.

Each table commits atomically. A populated target is accepted only when its full
ordered value digest equals the source; this command never merges a different DB.
"""
import argparse,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import create_engine,select,func,text
from .db import Base,engine
from .warehouse import encode

TABLES=('raw_responses','observations','watchlist','provider_responses','provider_observations')
def checksum(conn,table):
    h=hashlib.sha256();count=0
    query=select(table).order_by(*table.primary_key.columns)
    for row in conn.execution_options(stream_results=True).execute(query).mappings():
        values={k:(v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc))
                if isinstance(v,datetime) else v for k,v in row.items()}
        h.update((encode(values)+'\n').encode());count+=1
    return {'count':count,'sha256':h.hexdigest()}
def migrate(source_url,target_engine=engine):
    source=create_engine(source_url);report={}
    for name in TABLES:
        table=Base.metadata.tables[name]
        with source.connect() as src,target_engine.begin() as target:
            expected=checksum(src,table)
            exists=target.scalar(select(func.count()).select_from(table))
            if exists and checksum(target,table)!=expected:raise ValueError(f'{name}: target differs; refusing overwrite')
            if not exists and expected['count']:
                if target.dialect.name=='postgresql':
                    driver=target.connection.driver_connection
                    columns=','.join('"'+c.name+'"' for c in table.columns)
                    with driver.cursor().copy(f'COPY "{name}" ({columns}) FROM STDIN') as copy:
                        for row in src.execution_options(stream_results=True).execute(select(table)):copy.write_row(tuple(row))
                else:
                    result=src.execute(select(table))
                    while batch:=result.mappings().fetchmany(1000):target.execute(table.insert(),[dict(x) for x in batch])
            actual=checksum(target,table)
            if actual!=expected:raise ValueError(f'{name}: post-copy verification failed')
            if target.dialect.name=='postgresql' and 'id' in table.c:
                target.execute(text("SELECT setval(pg_get_serial_sequence(:table,'id'), COALESCE((SELECT max(id) FROM "+name+"),1), (SELECT count(*)>0 FROM "+name+"))"),{'table':name})
            report[name]=actual
        print(name,expected['count'],'verified',flush=True)
    return report
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--report',default='research/database_migration.json');a=p.parse_args()
    report=migrate('sqlite:///'+str(Path(a.source).resolve()).replace('\\','/'))
    Path(a.report).write_text(json.dumps(report,indent=2),encoding='utf8')
if __name__=='__main__':main()
