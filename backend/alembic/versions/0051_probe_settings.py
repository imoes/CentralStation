"""Prüf-Container: SSH-Account als Admin-Einstellungen (Nutzer, Schlüssel, Test-Host)

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-11
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Der Schlüssel wird verschlüsselt gespeichert (is_secret) und vom Administrator
    # eingetragen — eine Migration kennt ihn nicht.
    op.execute("""
        INSERT INTO global_settings (key, value_plain, is_secret) VALUES
            ('probe.ssh_user', 'marvin', false),
            ('probe.ssh_key', NULL, true),
            ('probe.test_host', NULL, false)
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM global_settings WHERE key IN ('probe.ssh_user','probe.ssh_key','probe.test_host')")
