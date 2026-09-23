"""project closed_at — closing a project freezes its plan-vs-actual report

Revision ID: b7d3e1f5a2c8
Revises: a1e6c9d2b4f0
Create Date: 2026-09-23 12:00:00.000000

projects.closed_at: set by POST /projects/{id}/close, which also stores
the plan-vs-actual workbook (progress.py) as a media_assets row with
role='final_report'. role is a plain String column, so the new role value
needs no schema change of its own.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d3e1f5a2c8'
down_revision: Union[str, Sequence[str], None] = 'a1e6c9d2b4f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'closed_at')
