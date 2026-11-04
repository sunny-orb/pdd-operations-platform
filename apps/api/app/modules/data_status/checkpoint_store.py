"""来源检查状态仓储：全局会话互斥和短事务，网络调用不占发布事务。"""

from contextlib import contextmanager

import sqlalchemy as sa


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
            result = c.execute(
                self.table.update().where(self.table.c.source_id == code).values(**values)
            )
            if not result.rowcount:
                c.execute(self.table.insert().values(source_id=code, **values))
