"""narrative reports (blocks 5/6) — structured phase/delay facts + per-file equipment + GPT narrative

Revision ID: a1e6c9d2b4f0
Revises: f4cb588abed5
Create Date: 2026-09-21 13:00:00.000000

Adds what report.py's narrative generator needs (see analysis.py's
_build_entry_facts):

- journal_entries.phase_name/phase_confidence/delay_days/expected_completion:
  the same numbers stage_summary already renders into one text blob, now
  also kept as their own columns — closes the "No structured phase/delay
  columns" item in integrations/README.md's Open items.
- journal_entries.narrative_report: block 5's own short GPT narrative for
  this entry.
- media_assets.equipment_counts: this file's OWN equipment counts (as
  opposed to EquipmentObservation's day-merged, cross-file totals), so the
  narrative can cite a specific photo/video's own findings.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1e6c9d2b4f0'
down_revision: Union[str, Sequence[str], None] = 'f4cb588abed5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('journal_entries', sa.Column('phase_name', sa.String(length=100), nullable=True))
    op.add_column('journal_entries', sa.Column('phase_confidence', sa.Float(), nullable=True))
    op.add_column('journal_entries', sa.Column('delay_days', sa.Integer(), nullable=True))
    op.add_column('journal_entries', sa.Column('expected_completion', sa.Date(), nullable=True))
    op.add_column('journal_entries', sa.Column('narrative_report', sa.Text(), nullable=True))
    op.add_column('media_assets', sa.Column('equipment_counts', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('media_assets', 'equipment_counts')
    op.drop_column('journal_entries', 'narrative_report')
    op.drop_column('journal_entries', 'expected_completion')
    op.drop_column('journal_entries', 'delay_days')
    op.drop_column('journal_entries', 'phase_confidence')
    op.drop_column('journal_entries', 'phase_name')
