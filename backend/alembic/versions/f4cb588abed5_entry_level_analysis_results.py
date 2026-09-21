"""entry-level analysis results (phase/delay/visual_phase combined per journal entry)

Revision ID: f4cb588abed5
Revises: 9f1c2a7d4e6b
Create Date: 2026-09-21 12:00:00.000000

Moves the per-file analysis fields (stage_summary/equipment_summary/
analyzed_at, plus visual_phase_name/visual_phase_confidence added by
9f1c2a7d4e6b) off MediaAsset and onto JournalEntry: phase/delay/visual_phase
now run once per uploaded batch (one journal entry) instead of once per
individual photo/video — see analysis.py's run_entry_analysis/
_maybe_advance_entry. MediaAsset.analysis_status stays (still needed for the
plan-normalization step, and to track each file's own vision-detection
progress), but the five result columns move.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4cb588abed5'
down_revision: Union[str, Sequence[str], None] = '9f1c2a7d4e6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'journal_entries',
        sa.Column('analysis_status', sa.String(length=12), server_default='pending', nullable=False),
    )
    op.add_column('journal_entries', sa.Column('stage_summary', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('equipment_summary', sa.Text(), nullable=True))
    op.add_column('journal_entries', sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_name', sa.String(length=30), nullable=True))
    op.add_column('journal_entries', sa.Column('visual_phase_confidence', sa.Float(), nullable=True))

    op.drop_column('media_assets', 'stage_summary')
    op.drop_column('media_assets', 'equipment_summary')
    op.drop_column('media_assets', 'analyzed_at')
    op.drop_column('media_assets', 'visual_phase_name')
    op.drop_column('media_assets', 'visual_phase_confidence')


def downgrade() -> None:
    op.add_column('media_assets', sa.Column('visual_phase_confidence', sa.Float(), nullable=True))
    op.add_column('media_assets', sa.Column('visual_phase_name', sa.String(length=30), nullable=True))
    op.add_column('media_assets', sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('media_assets', sa.Column('equipment_summary', sa.Text(), nullable=True))
    op.add_column('media_assets', sa.Column('stage_summary', sa.Text(), nullable=True))

    op.drop_column('journal_entries', 'visual_phase_confidence')
    op.drop_column('journal_entries', 'visual_phase_name')
    op.drop_column('journal_entries', 'analyzed_at')
    op.drop_column('journal_entries', 'equipment_summary')
    op.drop_column('journal_entries', 'stage_summary')
    op.drop_column('journal_entries', 'analysis_status')
