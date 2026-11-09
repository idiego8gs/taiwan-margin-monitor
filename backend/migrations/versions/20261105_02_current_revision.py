"""Enforce one current revision per security/source/date/basis."""
from alembic import op
import sqlalchemy as sa
revision='20261105_02'
down_revision='20261105_01'
branch_labels=None
depends_on=None
def upgrade():
    for name in ('daily_prices','daily_margins'):
        op.create_index(f'ux_{name}_current',name,['security_id','trading_date','provider','basis'],unique=True,
            postgresql_where=sa.text('is_current = true'),sqlite_where=sa.text('is_current = 1'))
def downgrade():
    for name in ('daily_prices','daily_margins'):op.drop_index(f'ux_{name}_current',table_name=name)
