from alembic import context
from sqlalchemy import create_engine, pool
from app.db import Base, DATABASE_URL

config=context.config
url=config.attributes.get('database_url',DATABASE_URL)
if context.is_offline_mode():
    context.configure(url=url,target_metadata=Base.metadata,literal_binds=True,compare_type=True)
    with context.begin_transaction():context.run_migrations()
else:
    engine=create_engine(url,poolclass=pool.NullPool)
    with engine.connect() as connection:
        locked=connection.dialect.name=='postgresql'
        if locked:
            connection.exec_driver_sql('SELECT pg_advisory_lock(12517001)');connection.commit()
        context.configure(connection=connection,target_metadata=Base.metadata,compare_type=True)
        try:
            with context.begin_transaction():context.run_migrations()
        finally:
            if locked:
                connection.exec_driver_sql('SELECT pg_advisory_unlock(12517001)');connection.commit()
