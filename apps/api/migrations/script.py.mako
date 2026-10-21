"""${message}

版本：${up_revision}
上版：${down_revision | comma,n}
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """应用本次结构变更。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回退结构；涉及数据损失时先补齐恢复说明。"""
    ${downgrades if downgrades else "pass"}
