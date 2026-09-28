"""visual_phase_details — full visual-phase result; fusion moves to services/phase

Revision ID: e3b8c1d7f9a2
Revises: d2f7a9c3e5b1
Create Date: 2026-09-27 00:00:00.000000

journal_entries.visual_phase_details: the whole services/visual_phase result
as JSON (phase probabilities, evidence), and the marker that it has arrived
(status done | failed | timeout) — analysis._maybe_advance_entry waits for
it before firing phase.command, and services/phase fuses every entry's
visual probabilities with the equipment model over the project's history.

That replaces the backend-side per-entry ensemble of d2f7a9c3e5b1, so its
three columns (equipment_phase_probs, visual_phase_probs,
visual_phase_status) are dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3b8c1d7f9a2'
down_revision: Union[str, Sequence[str], None] = 'd2f7a9c3e5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('journal_entries', sa.Column('visual_phase_details', sa.Text(), nullable=True))
    op.drop_column('journal_entries', 'visual_phase_status')
    op.drop_column('journal_entries', 'visual_phase_probs')
    op.drop_column('journal_entries', 'equipment_phase_probs')


def downgrade() -> None:
    op.add_column('journal_entries', sa.Column('equipment_phase_probs', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_probs', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_status', sa.String(length=10), nullable=True))
    op.drop_column('journal_entries', 'visual_phase_details')
