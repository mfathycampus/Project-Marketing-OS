"""brand_profiles.logo_media_id

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('brand_profiles', sa.Column('logo_media_id', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('brand_profiles', 'logo_media_id')
