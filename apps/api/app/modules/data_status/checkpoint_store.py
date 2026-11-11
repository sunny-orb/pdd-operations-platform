"""来源检查状态仓储：全局会话互斥和短事务，网络调用不占发布事务。"""

from contextlib import contextmanager

import sqlalchemy as sa

from app.modules.data_status.timeliness import merge_update_timing


def read_legacy_tasks(c):
    """读取首轮周对账前冻结的普通任务引用，调用方负责一致事务。"""
    return {
        row["source_id"]: dict(row)
        for row in c.execute(
            sa.text("""
            SELECT r.source_id,t.status,t.queued_at,t.finished_at,t.error_code,t.lease_expires_at
            FROM public.source_reconciliations r
            JOIN public.task_runs t ON t.id=r.legacy_task_id
        """)
        ).mappings()
    }


class CheckpointStore:
    def __init__(self, engine):
        self.engine = engine
        metadata = sa.MetaData()
        self.table = sa.Table("source_checkpoints", metadata, schema="public", autoload_with=engine)
        self.reconciliations = sa.Table(
            "source_reconciliations", metadata, schema="public", autoload_with=engine
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

    def read(self, c, table=None):
        if table is None:
            table = self.table
        with c.begin():
            return {r["source_id"]: dict(r) for r in c.execute(sa.select(table)).mappings()}

    def write(self, c, code, **values):
        """在调用方事务内更新检查点，并保留网页时效及覆盖摘要。"""
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
        self.upsert(c, self.table, code, **values)

    def write_reconciliation(self, c, code, **values):
        self.upsert(c, self.reconciliations, code, **values)

    @staticmethod
    def upsert(c, table, code, **values):
        """调用方持会话锁及短事务；需要时与同源接受状态原子结算。"""
        result = c.execute(table.update().where(table.c.source_id == code).values(**values))
        if not result.rowcount:
            c.execute(table.insert().values(source_id=code, **values))
