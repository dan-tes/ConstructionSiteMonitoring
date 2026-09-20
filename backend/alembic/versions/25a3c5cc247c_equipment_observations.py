"""equipment observations

Revision ID: 25a3c5cc247c
Revises: 1d5a73fa5801
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '25a3c5cc247c'
down_revision: Union[str, Sequence[str], None] = '1d5a73fa5801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('equipment_observations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('counts', sa.Text(), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('project_id', 'date', name='uq_equipment_observations_project_date'),
    )
    op.create_index(
        op.f('ix_equipment_observations_project_id'), 'equipment_observations', ['project_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_equipment_observations_project_id'), table_name='equipment_observations')
    op.drop_table('equipment_observations')
