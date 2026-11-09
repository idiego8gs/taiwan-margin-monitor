"""Durable research execution heartbeat."""
from alembic import op
import sqlalchemy as sa
revision='20261105_06'
down_revision='20261105_05'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('research_execution',
        sa.Column('run_id',sa.String(64),sa.ForeignKey('research_runs.id'),primary_key=True),
        sa.Column('heartbeat_at',sa.DateTime(timezone=True)),sa.Column('progress_json',sa.Text,nullable=False),
        sa.Column('last_error',sa.Text),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
def downgrade():op.drop_table('research_execution')
