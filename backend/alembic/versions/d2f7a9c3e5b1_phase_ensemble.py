"""phase ensemble columns on journal_entries

Revision ID: d2f7a9c3e5b1
Revises: c4a8f2e6d1b9
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f7a9c3e5b1'
down_revision: Union[str, Sequence[str], None] = 'c4a8f2e6d1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('journal_entries', sa.Column('equipment_phase_probs', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_probs', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_status', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('journal_entries', 'visual_phase_status')
    op.drop_column('journal_entries', 'visual_phase_probs')
    op.drop_column('journal_entries', 'equipment_phase_probs')
