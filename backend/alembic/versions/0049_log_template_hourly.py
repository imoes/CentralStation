"""error clusters: hourly rollup per pattern (28 days) for the hour-of-week baseline

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 5-min-Zählwerte bleiben 14 Tage; für die Grundlast je Wochenstunde braucht es die
    # gleiche Stunde mehrerer Vorwochen — daher eine stündliche Verdichtung über 28 Tage.
    op.create_table(
        "log_template_hourly",
        sa.Column("key", sa.String(16), primary_key=True),
        sa.Column("hour_start", sa.DateTime(timezone=True), primary_key=True, index=True),
        sa.Column("count", sa.Integer, nullable=False),
    )
    # Vorhandene 5-min-Werte gleich verdichten (sonst wartete die Wochenstunde 14 Tage länger).
    op.execute("""
        INSERT INTO log_template_hourly (key, hour_start, count)
        SELECT key, date_trunc('hour', bucket_start), sum(count)
        FROM log_template_buckets
        WHERE bucket_start < date_trunc('hour', now())
        GROUP BY 1, 2
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("log_template_hourly")
