"""add phase to match_day_snapshots pk

Revision ID: ce21561175fd
Revises: c8f123e664c0
Create Date: 2026-05-09 19:44:55.679126

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "ce21561175fd"
down_revision: Union[str, Sequence[str], None] = "c8f123e664c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add `phase` to `match_day_snapshots` and include it in the primary key.

    Tournament seasons can have the same `match_day` value in multiple phases
    (e.g., quarter-final leg 1 and leg 2 in KNOCKOUT), so the snapshot row
    needs to be keyed on `(season_id, phase, match_day)`. Leagues use the
    empty-string default for `phase`, which keeps existing rows correct.

    SQLite cannot ALTER an existing PK in place, so we copy the table to a
    new one with the desired schema. `batch_alter_table` handles the dance.
    """
    with op.batch_alter_table("match_day_snapshots", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("phase", sa.Text(), nullable=False, server_default="")
        )
        batch_op.create_primary_key(
            "pk_match_day_snapshots", ["season_id", "phase", "match_day"]
        )


def downgrade() -> None:
    with op.batch_alter_table("match_day_snapshots", recreate="always") as batch_op:
        batch_op.create_primary_key(
            "pk_match_day_snapshots", ["season_id", "match_day"]
        )
        batch_op.drop_column("phase")
