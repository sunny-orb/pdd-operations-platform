"""天气发布只读快照；目录/文档/定义向量不随抓取状态变化。"""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.product_board.repository import BoardError
from app.modules.weather.contracts import WeatherDocument


def definition_revision():
    return hashlib.sha256(
        b"".join(
            Path(__file__).with_name(name).read_bytes()
            for name in ("contracts.py", "parser.py", "repository.py")
        )
    ).hexdigest()


def _time(value):
    return value.isoformat() if value is not None else None


@contextmanager
def snapshot(engine, publication_id=None, token=None):
    """只读本地已发布父版本；历史显式ID保留，不向上游查询。"""
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            repo = Repository(c, publication_id)
            if token is not None and token != repo.meta["data_version"]:
                raise BoardError("DATA_VERSION_CHANGED", 409)
            yield repo
    except SQLAlchemyError as exc:
        raise BoardError("DATABASE_UNAVAILABLE", 503) from exc


class Repository:
    def __init__(self, c, publication_id):
        rows = [
            dict(row)
            for row in c.execute(
                sa.text("""
          SELECT w.id,w.issue_date,w.revision_no,w.content_sha256,w.parser_revision,
                 w.document->>'issue_time' AS issue_time
          FROM public.weather_publications w
          JOIN public.dataset_versions v ON v.id=w.id
          WHERE v.dataset_code='weather_publication' AND v.state='published'
          ORDER BY w.issue_date DESC,w.revision_no DESC,w.id
        """)
            ).mappings()
        ]
        by_id = {row["id"]: row for row in rows}
        if publication_id and publication_id not in by_id:
            raise BoardError("WEATHER_PUBLICATION_NOT_FOUND", 422)
        selected = by_id.get(publication_id) if publication_id else next(iter(rows), None)
        default = next(iter(rows), None)
        catalog, seen = [], set()
        for row in rows:
            if row["issue_date"] not in seen:
                seen.add(row["issue_date"])
                catalog.append(
                    {
                        key: str(row[key]) if key == "issue_date" else row[key]
                        for key in ("id", "issue_date", "revision_no", "issue_time")
                    }
                )
        vector = {
            "definition": definition_revision(),
            "publications": [(r["id"], r["content_sha256"], r["parser_revision"]) for r in rows],
        }
        token = hashlib.sha256(json.dumps(vector, sort_keys=True).encode()).hexdigest()
        last = (
            c.execute(
                sa.text("""SELECT status,queued_at,finished_at,error_code
           FROM public.task_runs WHERE job_code='sync_weather'
           ORDER BY queued_at DESC,id DESC LIMIT 1""")
            )
            .mappings()
            .first()
        )
        success = c.scalar(
            sa.text("""SELECT max(finished_at) FROM public.task_runs
                                      WHERE job_code='sync_weather' AND status='succeeded'""")
        )
        self.data = None
        if selected:
            raw = c.scalar(
                sa.text("SELECT document FROM public.weather_publications WHERE id=:id"),
                {"id": selected["id"]},
            )
            document = WeatherDocument.model_validate(raw).model_dump()
            self.data = dict(
                id=selected["id"],
                issue_date=str(selected["issue_date"]),
                revision_no=selected["revision_no"],
                document=document,
            )
        partial = bool(last and last["status"] != "succeeded")
        # meta默认发布的解析完整度独立于显式历史选择，切日不改变全局状态。
        if default:
            raw = c.scalar(
                sa.text("SELECT document FROM public.weather_publications WHERE id=:id"),
                {"id": default["id"]},
            )
            partial = partial or any(
                raw[name]["status"] != "parsed" for name in ("today_rain", "today_temperature")
            )
        self.meta = dict(
            data_version=token,
            status="source_unavailable" if not rows else "partial" if partial else "ready",
            default_publication_id=default["id"] if default else None,
            publications=catalog,
            last_success_at=_time(success),
            last_attempt_at=_time(last["queued_at"]) if last else None,
            last_attempt_status=last["status"] if last else None,
            error_code=last["error_code"] if last else None,
        )
