"""feed_searches table + feed_disabled_search_ids in user_preferences

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-25
"""
from typing import Sequence, Union
import uuid
import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── feed_searches ───────────────────────────────────────────────────────
    op.create_table(
        "feed_searches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("index_pattern", sa.String(60), nullable=False, server_default="cs-feed-*"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("query_string", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_feed_searches_user_id", "feed_searches", ["user_id"])

    # ── Seed: system searches ───────────────────────────────────────────────
    bind = op.get_bind()
    bind.execute(
        sa.text("""
        INSERT INTO feed_searches (id, user_id, index_pattern, name, query_string,
                                   enabled, is_system, position)
        VALUES
          (:id1, NULL, 'cs-feed-graylog',
           'Filebeat (Hyde-relevant)',
           'metadata.hyde_relevant:true AND NOT metadata.source_host:(nsa* OR nss* OR nsc*)',
           true, true, 0),
          (:id2, NULL, 'cs-feed-graylog',
           'HTTP-Fehler (Container)',
           'metadata.http_response_code:>=400 AND metadata.container_name:*',
           true, true, 1),
          (:id3, NULL, 'cs-feed-graylog',
           'Syslog Errors',
           'metadata.level:<=4 AND NOT body:uprobes',
           true, true, 2),
          (:id4, NULL, 'cs-feed-wazuh',
           'Wazuh Security Alerts (Level 7+)',
           'metadata.rule_level:>=7',
           true, true, 3),
          (:id5, NULL, 'cs-feed-checkmk', 'Alle CheckMK-Alerts', '', true, true, 10),
          (:id6, NULL, 'cs-feed-graylog', 'Alle Graylog-Logs', '', true, true, 11),
          (:id7, NULL, 'cs-feed-wazuh', 'Alle Wazuh-Alerts', '', true, true, 12),
          (:id8, NULL, 'cs-feed-*', 'Alle Quellen', '', true, true, 13),
          (:id9, NULL, 'cs-feed-*', 'Kritische und Hohe Alerts', 'severity:(critical OR high)', true, true, 14)
        """),
        {
            "id1": str(uuid.uuid4()),
            "id2": str(uuid.uuid4()),
            "id3": str(uuid.uuid4()),
            "id4": str(uuid.uuid4()),
            "id5": str(uuid.uuid4()),
            "id6": str(uuid.uuid4()),
            "id7": str(uuid.uuid4()),
            "id8": str(uuid.uuid4()),
            "id9": str(uuid.uuid4()),
        },
    )

    # ── user_preferences: feed_disabled_search_ids ──────────────────────────
    op.add_column(
        "user_preferences",
        sa.Column("feed_disabled_search_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "feed_disabled_search_ids")
    op.drop_index("ix_feed_searches_user_id", table_name="feed_searches")
    op.drop_table("feed_searches")
