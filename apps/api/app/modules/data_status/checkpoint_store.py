"""来源检查状态仓储：全局会话互斥和短事务，网络调用不占发布事务。"""

from contextlib import contextmanager

import sqlalchemy as sa

from app.modules.data_status.timeliness import merge_update_timing


class CheckpointStore:
    def __init__(self, engine):
        self.engine = engine
        self.table = sa.Table(
            "source_checkpoints", sa.MetaData(), schema="public", autoload_with=engine
        )

    @contextmanager
    def locked(self):
        with self.engine.connect() as c:
            acquired = c.scalar(
                sa.text("SELECT pg_try_advisory_lock(hashtext('project-source-sync'))")
            )
            c.commit()
            try:
                yield c if acquired else None
            finally:
                if c.in_transaction():
                    c.rollback()
                if acquired:
                    c.execute(sa.text("SELECT pg_advisory_unlock(hashtext('project-source-sync'))"))
                    c.commit()

    def read(self, c):
        with c.begin():
            return {r["source_id"]: dict(r) for r in c.execute(sa.select(self.table)).mappings()}

    def write(self, c, code, **values):
        with c.begin():
            if "web_updated_at" in values:
                prior = (
                    c.execute(sa.select(self.table).where(self.table.c.source_id == code))
                    .mappings()
                    .first()
                )
                prior = dict(prior) if prior else {}
                checked = values["web_checked_at"]
                if prior.get("web_checked_at") and checked < prior["web_checked_at"]:
                    raise ValueError("WEB_CHECK_OLDER_OR_SOURCE_UNINITIALIZED")
                values["platform_snapshot"] = {
                    **prior.get("platform_snapshot", {}),
                    "update_timing": merge_update_timing(prior, values["web_updated_at"], checked),
                }
            result = c.execute(
                self.table.update().where(self.table.c.source_id == code).values(**values)
            )
            if not result.rowcount:
                c.execute(self.table.insert().values(source_id=code, **values))
