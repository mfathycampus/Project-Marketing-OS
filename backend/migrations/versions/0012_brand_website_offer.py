"""brand_profiles.website + offer (video end card)

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('brand_profiles', sa.Column('website', sa.String(length=120), nullable=False, server_default=''))
    op.add_column('brand_profiles', sa.Column('offer', sa.String(length=120), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('brand_profiles', 'offer')
    op.drop_column('brand_profiles', 'website')
