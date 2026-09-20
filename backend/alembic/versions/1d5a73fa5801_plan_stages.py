"""plan stages

Revision ID: 1d5a73fa5801
Revises: ef1229e23ee8
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1d5a73fa5801'
down_revision: Union[str, Sequence[str], None] = 'ef1229e23ee8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('plan_duration_days', sa.Float(), nullable=True))
    op.create_table('plan_stages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.Column('phase', sa.String(length=100), nullable=False),
    sa.Column('phase_order', sa.Integer(), nullable=False),
    sa.Column('planned_duration_days', sa.Float(), nullable=False),
    sa.Column('expected_equipment', sa.Text(), server_default='[]', nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_plan_stages_project_id'), 'plan_stages', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_plan_stages_project_id'), table_name='plan_stages')
    op.drop_table('plan_stages')
    op.drop_column('projects', 'plan_duration_days')
