"""Index security quality checks and durable work/source lookups."""
from alembic import op
revision='20261105_04'
down_revision='20261105_03'
branch_labels=None
depends_on=None
def upgrade():
    op.create_index('ix_issues_security_kind_day','quality_issues',['security_id','kind','trading_date'])
    op.create_index('ix_captures_lookup','source_captures',['provider','code','dataset','start_date','end_date'])
    op.create_index('ix_jobs_claim','ingestion_jobs',['status','priority','next_attempt_at','created_at'])
def downgrade():
    op.drop_index('ix_jobs_claim',table_name='ingestion_jobs')
    op.drop_index('ix_captures_lookup',table_name='source_captures')
    op.drop_index('ix_issues_security_kind_day',table_name='quality_issues')
