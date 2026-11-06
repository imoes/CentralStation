"""generatives Dashboard heißt KI-Cockpit (vorher KI-Lagebild)

Der Name ist der Schlüssel, unter dem das Dashboard gefunden wird
(generative_designer.GENERATIVE_DASHBOARD_NAME) — Konstante und Zeilen ändern sich
gemeinsam, sonst entstünde ein zweites Dashboard und das alte bliebe verwaist.

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-06
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE dashboards SET name = '🪄 KI-Cockpit' WHERE name = '🪄 KI-Lagebild'")


def downgrade() -> None:
    op.execute("UPDATE dashboards SET name = '🪄 KI-Lagebild' WHERE name = '🪄 KI-Cockpit'")
