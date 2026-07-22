"""add approval_status to leads

Revision ID: b4f9c2a1d8e3
Revises: ae131006cca1
Create Date: 2026-07-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f9c2a1d8e3'
down_revision: Union[str, Sequence[str], None] = 'ae131006cca1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('leads', sa.Column('approval_status', sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('leads', 'approval_status')
