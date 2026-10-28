"""M5固定报告RR读取；只读本地版本，缺源不变为零，历史不跟当前事实。"""

import hashlib
import json
from contextlib import contextmanager
from decimal import localcontext
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.product_board.repository import BoardError

ROLES = {
    "j1_returns": ("jst_returns", "return_records", "售后确认期间明细"),
    "j2_shipped_sales": ("jst_shipped_sales", "product_sales_records", "已发货支付期间明细"),
    "j3_cancelled_sales": ("jst_cancelled_sales", "product_sales_records", "已取消支付期间明细"),
}
IDENTITY_FIELDS = (
    "source_shop_id",
    "source_shop_name",
    "source_product_category",
    "source_virtual_category",
    "source_style_code",
    "source_product_code",
    "source_product_name",
    "source_subcategory",
    "source_color_specification",
)
COMMON_FIELDS = (
    *IDENTITY_FIELDS,
    "source_row_key",
    "row_kind",
    "date_status",
    "period_start",
    "period_end",
)


def revision():
    return hashlib.sha256(
        b"".join(
            Path(__file__).with_name(n).read_bytes()
            for n in (
                "repository.py",
                "query.py",
                "metrics.py",
                "amounts.py",
                "analysis.py",
                "classification.py",
                "targets.py",
            )
        )
    ).hexdigest()


@contextmanager
def snapshot(engine, report_id=None, token=None):
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            repo = Repository(c, report_id)
            if token is not None and token != repo.token:
                raise BoardError("DATA_VERSION_CHANGED", 409)
            with localcontext() as ctx:
                ctx.prec = 50
                yield repo
    except SQLAlchemyError as exc:
        raise BoardError("DATABASE_UNAVAILABLE", 503) from exc


class Repository:
    def __init__(self, c, report_id):
        self.c = c
        self.reports = [
            dict(r)
            for r in c.execute(
                sa.text("""
            SELECT r.*,v.sealed_at,v.rule_set_id,v.content_fingerprint
            FROM public.aftersales_reports r
            JOIN public.dataset_versions v ON v.id=r.dataset_version_id
            WHERE v.state='published' AND v.dataset_code='aftersales_report'
            ORDER BY r.period_end DESC,
            CASE r.report_type WHEN 'monthly' THEN 0 WHEN 'weekly' THEN 1 ELSE 2 END,
            r.period_start DESC,r.revision_no DESC,r.id ASC
        """)
            ).mappings()
        ]
        logical = set()
        self.defaults = []
        for r in self.reports:
            key = (r["report_type"], r["period_start"], r["period_end"])
            if key not in logical:
                self.defaults.append(r)
                logical.add(key)
        self.by_id = {r["id"]: r for r in self.reports}
        if report_id and report_id not in self.by_id:
            raise BoardError("REPORT_NOT_FOUND", 422)
        self.report = self.by_id.get(report_id) if report_id else next(iter(self.defaults), None)
        self.token = hashlib.sha256(
            json.dumps(
                {
                    "definition": revision(),
                    "reports": [
                        (r["id"], r["dataset_version_id"], r["rule_set_id"]) for r in self.reports
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._summaries = {}
        self._inputs = {}
        self._rows = {}
        self._classification = {}

    def classification_parameters(self, report=None):
        """懒读所选报告固定包；旧报告未绑定/空词典不回退当前配置。"""
        report = report or self.report
        rule_id = report.get("rule_set_id") if report else None
        if not rule_id:
            return None
        if rule_id not in self._classification:
            row = (
                self.c.execute(
                    sa.text("SELECT domain,schema_revision,parameters FROM rule_sets WHERE id=:id"),
                    dict(id=rule_id),
                )
                .mappings()
                .first()
            )
            self._classification[rule_id] = (
                row["parameters"]
                if row and row["domain"] == "aftersales" and row["schema_revision"] == "v1"
                else None
            )
        return self._classification[rule_id]

    def inputs(self, report=None):
        report = report or self.report
        if not report:
            return {}
        if report["id"] not in self._inputs:
            self._inputs[report["id"]] = {
                r["input_role"]: dict(r)
                for r in self.c.execute(
                    sa.text("""
                SELECT i.input_role,i.window_start,i.window_end,v.* FROM public.version_inputs i
                JOIN public.dataset_versions v ON v.id=i.input_version_id
                WHERE i.output_version_id=:id AND i.input_role=ANY(:roles)
            """),
                    dict(id=report["dataset_version_id"], roles=list(ROLES)),
                ).mappings()
            }
        return self._inputs[report["id"]]

    def source_available(self, role, report=None):
        """元信息只核版本与窗口，不扫描事实；供轻量轮询。"""
        report = report or self.report
        if not report:
            return False
        source = self.inputs(report).get(role)
        return bool(
            source
            and source["state"] == "published"
            and source["dataset_code"] == ROLES[role][0]
            and source["window_start"] == report["period_start"]
            and source["window_end"] == report["period_end"]
        )

    def rows(self, role, report=None):
        report = report or self.report
        if not report:
            return None
        key = (report["id"], role)
        if key in self._rows:
            return self._rows[key]
        source = self.inputs(report).get(role)
        code, table, _ = ROLES[role]
        if not source or source["state"] != "published" or source["dataset_code"] != code:
            self._rows[key] = None
            return None
        start, end = source["window_start"], source["window_end"]
        if start != report["period_start"] or end != report["period_end"]:
            self._rows[key] = None
            return None
        fields = (
            (
                *COMMON_FIELDS,
                "actual_returned_qty",
                "actual_inbound_amount",
                "source_return_reason",
                "source_aftersale_type",
            )
            if role == "j1_returns"
            else (*COMMON_FIELDS, "sales_amount", "source_sales_qty")
        )
        result = [
            dict(r)
            for r in self.c.execute(
                sa.text(
                    f"SELECT {','.join(fields)} FROM public.{table} WHERE dataset_version_id=:vid "
                    "AND row_kind='detail' AND period_start>=:start AND period_end<=:end "
                    "AND period_start IS NOT NULL AND period_end IS NOT NULL "
                    "ORDER BY source_row_key LIMIT 300001"
                ),
                dict(vid=source["id"], start=start, end=end),
            ).mappings()
        ]
        if len(result) > 300000:
            raise BoardError("REPORT_SCOPE_ROW_LIMIT", 503)
        self._rows[key] = result
        return result

    def source_summary(self, role, report=None):
        """元信息/历史聚合只读取SQL汇总，不向Python装载全部经营行。"""
        report = report or self.report
        if not self.source_available(role, report):
            return None
        key = (report["id"], role)
        if key in self._summaries:
            return self._summaries[key]
        src = self.inputs(report)[role]
        fields = (
            ("actual_returned_qty", "actual_inbound_amount")
            if role == "j1_returns"
            else ("sales_amount", "source_sales_qty")
        )
        sums = ",".join(
            f"CASE WHEN COUNT({field})=COUNT(*) THEN SUM({field}) END AS {field}"
            for field in fields
        )
        value = dict(
            self.c.execute(
                sa.text(
                    "SELECT COUNT(*) AS rows,MIN(period_start) AS first,"
                    f"MAX(period_end) AS last,{sums} "
                    f"FROM public.{ROLES[role][1]} WHERE dataset_version_id=:vid "
                    "AND row_kind='detail' "
                    "AND period_start>=:start AND period_end<=:end "
                    "AND period_start IS NOT NULL AND period_end IS NOT NULL"
                ),
                dict(vid=src["id"], start=src["window_start"], end=src["window_end"]),
            )
            .mappings()
            .one()
        )
        self._summaries[key] = value
        return value

    def metadata(self):
        sources = []
        inputs = self.inputs()
        heads = {
            r["dataset_code"]: dict(r)
            for r in self.c.execute(
                sa.text(
                    "SELECT v.dataset_code,v.id,v.source_locator FROM public.current_datasets d "
                    "JOIN public.dataset_versions v ON v.id=d.version_id "
                    "WHERE v.state='published' AND d.dataset_code=ANY(:codes)"
                ),
                dict(codes=[x[0] for x in ROLES.values()]),
            ).mappings()
        }
        for role, (code, _, label) in ROLES.items():
            v = inputs.get(role)
            task = (
                self.c.execute(
                    sa.text(
                        "SELECT status,started_at,finished_at,error_code FROM public.task_runs "
                        "WHERE job_code=:job ORDER BY queued_at DESC,id DESC LIMIT 1"
                    ),
                    dict(job="sync_" + code),
                )
                .mappings()
                .first()
            )
            success = self.c.execute(
                sa.text(
                    "SELECT max(finished_at) FROM public.task_runs "
                    "WHERE status='succeeded' AND job_code=:job AND result_version_id=:vid"
                ),
                dict(job="sync_" + code, vid=v["id"] if v else None),
            ).scalar_one_or_none()
            summary = self.source_summary(role)
            available = summary is not None
            no_records = available and not summary["rows"]
            head = heads.get(code)
            pending = bool(head and (v is None or head["id"] != v["id"]))
            note = (
                "Q仅在1119组历史非空样本中对应，有限范围参考；A存在补贴及两倍金额反例，"
                "实际收货金额未映射为实际入库金额。当前接收为明细行，不是原商品/原因汇总行。"
                if role == "j1_returns"
                else "J2只用实发金额sfje，缺值不回退销售金额；J3用已取消销售金额。"
            )
            note += (
                "完整性未确认；确认日期和支付日期不是同批订单。"
                if available
                else "尚无相容且已发布的本报告输入，不用其他看板或历史快照补值。"
            )
            if pending:
                note += " 来源已有新版本；所选报告固定原输入，报告待重算，新值尚未进入此报告。"
            sources.append(
                dict(
                    code=code,
                    label=label,
                    locator=v["source_locator"] if v else head["source_locator"] if head else None,
                    status="new_input_pending_report"
                    if pending
                    else "failed_retained"
                    if available and task and task["status"] == "failed"
                    else "no_records"
                    if no_records
                    else "observed"
                    if available
                    else "source_unavailable",
                    observed_from=str(summary["first"]) if available and summary["first"] else None,
                    observed_to=str(summary["last"]) if available and summary["last"] else None,
                    last_success_at=iso(success),
                    last_attempt_at=iso(task["started_at"]) if task else None,
                    error_code=task["error_code"] if task else None,
                    note=note,
                )
            )
        availability = [self.source_available(role) for role in ROLES]
        observed = [s["observed_to"] for s in sources if s["observed_to"]]
        return dict(
            data_version=self.token,
            status="source_unavailable" if not self.report or not any(availability) else "partial",
            default_report_id=self.defaults[0]["id"] if self.defaults else None,
            observed_to=max(observed, default=None),
            complete_through=None,
            last_success_at=max(
                (s["last_success_at"] for s in sources if s["last_success_at"]), default=None
            ),
            last_attempt_at=max(
                (s["last_attempt_at"] for s in sources if s["last_attempt_at"]), default=None
            ),
            sources=sources,
        )


def iso(value):
    return value.isoformat() if value is not None else None


def public_report(r):
    if r is None:
        return None
    return dict(
        id=r["id"],
        report_type=r["report_type"],
        label=r["source_label"]
        or (
            f"{r['period_start']:%Y年%m月}售后月报"
            if r["report_type"] == "monthly"
            else "滚动30天"
            if (r["period_end"] - r["period_start"]).days == 29
            else "售后分析报告"
        ),
        period_start=str(r["period_start"]),
        period_end=str(r["period_end"]),
        generated_at=iso(r["sealed_at"]),
        document_url=r["document_url"],
        sheet_url=r["sheet_url"],
    )
