"""error clusters: templates, 5-min buckets, episodes, detector state, incident kind

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB

# Starten AUS: eingeschaltet wird nach bestandenem Backtest (Admin → KI → Fehler-Cluster).
_SETTINGS = [
    ("error_clusters.enabled", "false"),
    ("error_clusters.mail_enabled", "false"),
]


def upgrade() -> None:
    op.create_table(
        "log_templates",
        sa.Column("key", sa.String(16), primary_key=True),
        sa.Column("template", sa.Text, nullable=False),
        sa.Column("examples", JSONB),
        sa.Column("first_seen", TS, nullable=False, index=True),
        sa.Column("last_seen", TS, nullable=False, index=True),
        sa.Column("muted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("muted_by", sa.String(255)),
        sa.Column("muted_reason", sa.Text),
        sa.Column("muted_at", TS),
    )
    op.create_table(
        "log_template_buckets",
        sa.Column("key", sa.String(16), primary_key=True),
        sa.Column("bucket_start", TS, primary_key=True, index=True),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("n_entities", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entities", JSONB),
        sa.Column("complete", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "entity_buckets",
        sa.Column("entity", sa.String(255), primary_key=True),
        sa.Column("bucket_start", TS, primary_key=True, index=True),
        sa.Column("count", sa.Integer, nullable=False),
    )
    op.create_table(
        "error_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(16), nullable=False, index=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="log"),
        sa.Column("status", sa.String(10), nullable=False, server_default="open", index=True),
        sa.Column("rules", JSONB),
        sa.Column("reasons", JSONB),
        sa.Column("notify_class", sa.String(10), nullable=False, server_default="digest"),
        sa.Column("started_at", TS, nullable=False, index=True),
        sa.Column("last_active_at", TS, nullable=False),
        sa.Column("closed_at", TS),
        sa.Column("peak15", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entities", JSONB),
        sa.Column("sources", JSONB),
        sa.Column("notified_rate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notified_entities", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notified_at", TS),
        sa.Column("triage", JSONB),
        sa.Column("triage_at", TS),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), index=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "error_cluster_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("miner_state", sa.LargeBinary),
        sa.Column("last_bucket", TS),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    # Art des Incidents: "host" (alle bestehenden), "log_cluster", "fleet". Die
    # Fehler-Cluster-Erkennung ersetzt den Host-Correlator und erzeugt alle drei.
    op.add_column("incidents", sa.Column("kind", sa.String(20), nullable=False,
                                         server_default="host"))
    op.create_index("ix_incidents_kind", "incidents", ["kind"])
    for key, val in _SETTINGS:
        op.execute(sa.text(
            "INSERT INTO global_settings (key, value_plain, is_secret) "
            "SELECT :key, :val, false WHERE NOT EXISTS "
            "(SELECT 1 FROM global_settings WHERE key = :key)"
        ).bindparams(key=key, val=val))


def downgrade() -> None:
    op.drop_index("ix_incidents_kind", table_name="incidents")
    op.drop_column("incidents", "kind")
    for t in ("error_cluster_state", "error_clusters", "entity_buckets",
              "log_template_buckets", "log_templates"):
        op.drop_table(t)
    for key, _ in _SETTINGS:
        op.execute(sa.text("DELETE FROM global_settings WHERE key = :key").bindparams(key=key))
