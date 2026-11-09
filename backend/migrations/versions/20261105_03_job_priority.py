"""Prioritize current top-500 gaps and incremental updates ahead of older candidates."""
from alembic import op
import sqlalchemy as sa
revision='20261105_03'
down_revision='20261105_02'
branch_labels=None
depends_on=None
def upgrade():op.add_column('ingestion_jobs',sa.Column('priority',sa.Integer(),nullable=False,server_default='100'))
def downgrade():op.drop_column('ingestion_jobs','priority')
