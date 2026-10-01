"""error clusters: remember when the mail was actually sent

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # notified_at heißt "Regeln haben eine Mail beschlossen", mailed_at "sie ist raus".
    # Dazwischen liegen bis zu 90 s Wartezeit auf die KI; ein Neustart darin verlor die
    # Mail bisher still. Episoden mit notified_at, aber ohne mailed_at holt der Runner nach.
    op.add_column("error_clusters", sa.Column("mailed_at", sa.DateTime(timezone=True)))
    # Bestehende Episoden gelten als zugestellt — sonst ginge jetzt alles noch einmal raus.
    op.execute("UPDATE error_clusters SET mailed_at = notified_at WHERE notified_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("error_clusters", "mailed_at")
