from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECTORY = """
CREATE VIEW stores.directory WITH (security_invoker = true) AS
SELECT id, name, slug, description, price_visibility
FROM stores.stores
WHERE status = 'active' AND is_published
"""

MY_ACTIVE_MEMBERSHIPS = """
CREATE VIEW membership.my_active_memberships WITH (security_invoker = true) AS
SELECT store_id
FROM membership.memberships
WHERE user_id = platform.actor_user_id() AND status = 'active'
"""


def upgrade() -> None:
    op.execute(DIRECTORY)
    op.execute(MY_ACTIVE_MEMBERSHIPS)
    op.execute("GRANT SELECT ON stores.directory TO haatly_app, haatly_ops")
    op.execute("GRANT SELECT ON membership.my_active_memberships TO haatly_app, haatly_ops")


def downgrade() -> None:
    op.execute("DROP VIEW membership.my_active_memberships")
    op.execute("DROP VIEW stores.directory")
