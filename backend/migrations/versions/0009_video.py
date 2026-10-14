"""video: content_items.media_type + video_assets

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('content_items', sa.Column('media_type', sa.String(length=10), nullable=False, server_default='image'))
    op.create_table('video_assets',
    sa.Column('content_item_id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('mime_type', sa.String(length=50), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['content_item_id'], ['content_items.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['design_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_video_assets_content_item_id'), 'video_assets', ['content_item_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_video_assets_content_item_id'), table_name='video_assets')
    op.drop_table('video_assets')
    op.drop_column('content_items', 'media_type')
