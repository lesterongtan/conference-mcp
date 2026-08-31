"""Add worker capability heartbeats for provider diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0002_worker_provider_status"
down_revision = "0001_service_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(100), primary_key=True),
        sa.Column("capabilities_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "worker_heartbeats_updated_idx", "worker_heartbeats", ["updated_at"]
    )


def downgrade() -> None:
    op.drop_index("worker_heartbeats_updated_idx", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
