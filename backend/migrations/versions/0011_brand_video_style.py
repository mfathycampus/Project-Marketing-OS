"""brand_profiles.video_style

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('brand_profiles', sa.Column('video_style', sa.String(length=20), nullable=False, server_default='modern'))


def downgrade() -> None:
    op.drop_column('brand_profiles', 'video_style')
