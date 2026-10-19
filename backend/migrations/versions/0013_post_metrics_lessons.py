"""post_metrics + lessons (the learning loop)

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'post_metrics',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('publication_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('views', sa.Integer(), nullable=True),
        sa.Column('reach', sa.Integer(), nullable=True),
        sa.Column('likes', sa.Integer(), nullable=True),
        sa.Column('comments', sa.Integer(), nullable=True),
        sa.Column('shares', sa.Integer(), nullable=True),
        sa.Column('saves', sa.Integer(), nullable=True),
        sa.Column('clicks', sa.Integer(), nullable=True),
        sa.Column('avg_watch_s', sa.Float(), nullable=True),
        sa.Column('completion', sa.Float(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['publication_id'], ['publications.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_post_metrics_publication_id', 'post_metrics', ['publication_id'], unique=True)
    op.create_index('ix_post_metrics_project_id', 'post_metrics', ['project_id'])
    op.create_table(
        'lessons',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('based_on', sa.Integer(), nullable=False),
        sa.Column('ai_note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_lessons_project_id', 'lessons', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_lessons_project_id', table_name='lessons')
    op.drop_table('lessons')
    op.drop_index('ix_post_metrics_project_id', table_name='post_metrics')
    op.drop_index('ix_post_metrics_publication_id', table_name='post_metrics')
    op.drop_table('post_metrics')
