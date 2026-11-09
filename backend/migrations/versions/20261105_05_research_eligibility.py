"""Immutable research input snapshots and daily eligibility."""
from alembic import op
import sqlalchemy as sa

revision='20261105_05'
down_revision='20261105_04'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('research_snapshots',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('data_version',sa.String(64),nullable=False),
        sa.Column('start_date',sa.Date,nullable=False),sa.Column('end_date',sa.Date,nullable=False),
        sa.Column('path',sa.Text,nullable=False),sa.Column('manifest_json',sa.Text,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('research_eligibility',
        sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('snapshot_id',sa.String(64),sa.ForeignKey('research_snapshots.id'),nullable=False),
        sa.Column('security_id',sa.Integer,sa.ForeignKey('securities.id'),nullable=False),
        sa.Column('trading_date',sa.Date,nullable=False),sa.Column('segment_id',sa.String(64)),
        sa.Column('price_status',sa.String(30),nullable=False),sa.Column('margin_status',sa.String(30),nullable=False),
        sa.Column('strict',sa.Boolean,nullable=False),sa.Column('exploratory',sa.Boolean,nullable=False),
        sa.Column('reasons_json',sa.Text,nullable=False),
        sa.UniqueConstraint('snapshot_id','security_id','trading_date'))
    op.create_index('ix_eligibility_snapshot_date','research_eligibility',['snapshot_id','trading_date'])

def downgrade():
    op.drop_index('ix_eligibility_snapshot_date',table_name='research_eligibility')
    op.drop_table('research_eligibility')
    op.drop_table('research_snapshots')
