"""entry spi_time — schedule pace behind each delay forecast (graph D)

Revision ID: c4a8f2e6d1b9
Revises: b7d3e1f5a2c8
Create Date: 2026-09-23 15:00:00.000000

journal_entries.spi_time: the delay worker's effective SPI(t)
(DelayForecastResult.spi_time), kept alongside delay_days so the project
timeline (GET /projects/{id}/timeline) can chart pace, not just its
consequence. Null for entries analysed before this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a8f2e6d1b9'
down_revision: Union[str, Sequence[str], None] = 'b7d3e1f5a2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('journal_entries', sa.Column('spi_time', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('journal_entries', 'spi_time')
