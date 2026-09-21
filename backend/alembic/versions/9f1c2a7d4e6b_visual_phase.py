"""visual phase columns on media_assets

Revision ID: 9f1c2a7d4e6b
Revises: 25a3c5cc247c
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f1c2a7d4e6b'
down_revision: Union[str, Sequence[str], None] = '25a3c5cc247c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('media_assets', sa.Column('visual_phase_name', sa.String(length=30), nullable=True))
    op.add_column('media_assets', sa.Column('visual_phase_confidence', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('media_assets', 'visual_phase_confidence')
    op.drop_column('media_assets', 'visual_phase_name')
