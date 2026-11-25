"""助手销售、天气与更新状态适配：固定发布版本，仅整理既有领域结果。"""

from datetime import date
from pathlib import Path

from app.modules.data_status.repository import read_status
from app.modules.product_board.repository import BoardError, wire
from app.modules.sales import source_policy
from app.modules.sales.detail_queries import SalesDetails
from app.modules.sales.query import SalesQuery, SalesService
from app.modules.sales.region_queries import RegionQueries
from app.modules.sales.trend import read_performance_trend
from app.modules.weather.repository import snapshot as weather_snapshot

from .business import fact, report, table, window

CAPABILITIES = (
    "sales_performance",
    "sales_trend",
    "sales_shops",
    "sales_targets",
    "sales_ratings",
    "sales_regions",
    "sales_product_regions",
    "sales_product_options",
    "weather",
    "data_status",
)
METRICS = (
    ("shop_revenue", "成交金额", "currency"),
    ("shop_visitors", "店铺访客数", "number"),
    ("buyers", "成交买家数", "number"),
    ("orders", "成交订单数", "number"),
    ("buyer_conversion", "成交转化率", "percent"),
    ("aov", "客单价", "currency"),
    ("refund_rate", "同期经营退款金额占比", "percent"),
    ("promotion_spend", "推广花费", "currency"),
    ("promotion_ctr", "推广点击率", "percent"),
    ("promotion_roi", "推广投产比", "ratio"),
)
REGION_METRICS = (
    ("transaction_amount", "成交金额", "currency"),
    ("transaction_orders", "成交订单数", "number"),
    ("transaction_buyers", "成交买家数", "number"),
    ("aov", "客单价", "currency"),
)
PRODUCT_METRICS = (
    ("actual_shipped_amount", "实际发货金额", "currency"),
    ("actual_shipped_qty", "实际发货数量", "number"),
    ("actual_returned_amount", "实际退货金额", "currency"),
    ("actual_returned_qty", "实际退货数量", "number"),
    ("refund_rate", "实退金额占实发金额比率", "percent"),
)
SALES_SOURCE = "店铺销售看板已发布经营数据"


def describe(engine, domain="sales"):
    """提供实际观察日期与店铺身份映射；不以经营日历冒充已收到的数据。"""
    if domain == "weather":
        with weather_snapshot(engine) as repo:
            publications = repo.meta["publications"]
            dates = [p["issue_date"] for p in publications]
            return wire(
                dict(
                    domain=domain,
                    data_version=repo.meta["data_version"],
                    shops=[],
                    date_from=min(dates) if dates else None,
                    date_to=max(dates) if dates else None,
                    options=publications[:25],
                    total=len(publications),
                    status=repo.meta["status"],
                )
            )
    if domain == "data_status":
        value = read_status(engine)
        return wire(
            dict(
                domain=domain,
                date_from=None,
                date_to=None,
                shops=[],
                generated_at=value["meta"]["generated_at"],
            )
        )
    if domain != "sales":
        raise BoardError("INVALID_SCOPE")
    value = SalesService(engine).filters()
    data, meta = value["data"], value["meta"]
    return wire(
        dict(
            domain="sales",
            data_version=meta["data_version"],
            date_from=meta["observed_from"],
            date_to=meta["observed_through"],
            shops=[
                dict(
                    id=s["shop_id"],
                    name=s["shop_name"],
                    shop_source_id=s["source_shop_id"],
                    display_name=s["display_name"],
                )
                for s in data["shops"]
            ],
            sources=data["sources"],
            capabilities=data["capabilities"],
            options=dict(months=data["months"], default_month=data["default_month"]),
        )
    )


def _validate(params):
    """有界列表和默认窗口约束先行；领域继续校验业务身份与版本。"""
    for key, default, low, high in (
        ("limit", 10, 1, 25),
        ("offset", 0, 0, None),
        ("recent_days", 7, 1, 366),
    ):
        value = params.get(key, default)
        if type(value) is not int or value < low or (high is not None and value > high):
            raise BoardError("INVALID_SCOPE")
    for key in ("shop_id", "month", "category", "keyword", "product_key", "view"):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 1000):
            raise BoardError("INVALID_SCOPE")


def _version(params):
    value = params.get("data_version")
    if not isinstance(value, str) or not value:
        raise BoardError("DATA_VERSION_REQUIRED")
    return value


def _page(title, columns, rows, params, total=None):
    offset, limit = params.get("offset", 0), params.get("limit", 10)
    cells = [
        {key: row.get(key) for key, _label, _unit in columns}
        for row in rows[offset : offset + limit]
    ]
    return table(title, columns, cells, total=len(rows) if total is None else total, offset=offset)


def _facts(data, columns):
    return [
        fact(
            key,
            label,
            data.get(key),
            unit,
            status=data.get("field_states", {}).get(key)
            or ("observed" if data.get(key) is not None else "unavailable"),
        )
        for key, label, unit in columns
    ]


def _shop_revenue_fact(key, label, amount, items):
    """成交额沿用看板原值，按该合计的完整店铺范围说明缺记录和空金额。"""
    missing = sum(len(item["coverage"]["missing_dates"]) for item in items)
    nulls = sum(item["coverage"]["field_null_counts"]["shop_revenue"] for item in items)
    status, reason = "observed", None
    if amount is None:
        status, reason = (
            ("field_null", "期间有销售记录，已记录的成交额均为空。")
            if nulls
            else ("no_records", "期间没有销售记录。")
        )
    elif missing or nulls:
        status = "partial"
        reason = f"按已有金额累计。缺记录 {missing} 个店铺日，成交额为空 {nulls} 个店铺日。"
    return fact(key, label, amount, "currency", status=status, reason=reason)


def _coverage(data):
    coverage = data.get("coverage", {})
    notes = ["仅反映已观察数据，来源未声明完整日。"]
    if coverage:
        notes.append(
            f"销售已观察{coverage['observed_days']}个店铺日，"
            f"缺{len(coverage['missing_dates'])}个店铺日；缺记录不补零。"
        )
    if "missing_shop_days" in data:
        missing = data["missing_shop_days"]
        notes.append(
            f"所选范围缺{len(missing) if isinstance(missing, list) else missing}个店铺日。"
        )
    if "null_counts" in data:
        notes.append(
            "空字段记录数：" + "，".join(f"{k}={v}" for k, v in data["null_counts"].items())
        )
    if "source" in data:
        source = data["source"]
        notes.append(
            f"来源状态：{source['state']}；实际观察截至："
            f"{source.get('observed_through') or '暂无记录'}。"
        )
    return notes


def _query(engine, capability, params, version):
    info = describe(engine)
    source_code = {
        "sales_ratings": "shop_ratings",
        "sales_regions": "shop_regions",
        "sales_product_regions": "product_regions",
    }.get(capability)
    coverage = info["sources"].get(source_code, {}) if source_code else {}
    start = coverage.get("observed_from") or info["date_from"]
    end = coverage.get("observed_through") or info["date_to"]
    try:
        first, last = window(
            params,
            date.fromisoformat(start) if start else None,
            date.fromisoformat(end) if end else None,
        )
    except (TypeError, ValueError) as exc:
        raise BoardError("INVALID_SCOPE") from exc
    query = SalesQuery(first, last, version, params.get("shop_id") or None)
    # 明细查询亦使用同一已核身份；不让某些领域入口的宽松校验透传未知店铺。
    if query.shop_id and query.shop_id not in {s["id"] for s in info["shops"]}:
        raise BoardError("INVALID_SCOPE")
    return query


def read(engine, capability, params):
    """输入能力与已绑定版本，输出有界、可JSON序列化的看板报告。"""
    if capability not in CAPABILITIES:
        raise BoardError("INVALID_SCOPE")
    _validate(params)
    if capability == "data_status":
        return _status(engine, params)
    version = _version(params)
    if capability == "weather":
        return _weather(engine, params, version)
    if capability == "sales_product_options":
        return _options(engine, params, version)
    if capability == "sales_targets":
        return _targets(engine, params, version)
    query = _query(engine, capability, params, version)
    scope = dict(shop_id=query.shop_id)
    period = dict(date_from=query.date_from, date_to=query.date_to)
    facts, tables, series = [], [], []
    if capability == "sales_performance":
        data = SalesService(engine).performance(query)["data"]
        title = "店铺经营表现"
        facts = _facts(data, METRICS)
        refund = data["refund_match"]
        facts.extend(
            _facts(
                refund,
                (
                    ("matched_refunds", "配对退款金额", "currency"),
                    ("matched_sales", "配对成交金额", "currency"),
                    ("matched_days", "退款配对店铺日数", "number"),
                    ("excluded_sales", "未配对成交金额", "currency"),
                ),
            )
        )
        tables = [
            _page(
                "推广渠道",
                (
                    ("code", "渠道ID", "text"),
                    ("channel", "渠道", "text"),
                    ("spend", "花费", "currency"),
                    ("ctr", "点击率", "percent"),
                    ("roi", "投产比", "ratio"),
                    ("state", "状态", "text"),
                ),
                [
                    dict(
                        r,
                        channel={
                            "shop_channel_product_promotion": "商品推广",
                            "shop_channel_star_shop": "明星店铺",
                        }[r["code"]],
                        state=r["source"]["state"],
                    )
                    for r in data["promotion_channels"]
                ],
                params,
            )
        ]
    elif capability == "sales_shops":
        data = SalesService(engine).shops(query)["data"]
        items = data["items"]
        facts = [
            _shop_revenue_fact(
                "all_shops_revenue", "六店成交额合计", data["all_shops_revenue"], items
            )
        ]
        if query.shop_id:
            items = [r for r in items if r["shop_id"] == query.shop_id]
            (shop,) = items
            facts.insert(
                0,
                _shop_revenue_fact(
                    "shop_revenue", shop["shop_name"] + "成交额", shop["shop_revenue"], items
                ),
            )
        data["missing_shop_days"] = sum(len(r["coverage"]["missing_dates"]) for r in items)
        title = "店铺经营明细与销售贡献"
        columns = (
            ("shop_id", "店铺ID", "text"),
            ("shop_name", "店铺", "text"),
            ("rank", "排名", "number"),
            ("sales_share", "销售贡献", "percent"),
            *METRICS,
        )
        rows = [
            dict(r, metric_states="，".join(f"{k}:{v}" for k, v in r["field_states"].items()))
            for r in items
        ]
        tables = [_page(title, (*columns, ("metric_states", "指标状态", "text")), rows, params)]
        scope["sales_share_basis"] = "全部六店所选期间成交金额，筛选店铺不重算贡献分母"
    elif capability == "sales_trend":
        chosen = params.get("view") or "shop_revenue"
        metric = next((m for m in METRICS if m[0] == chosen), None)
        if metric is None or chosen in ("buyers", "orders"):
            raise BoardError("INVALID_SCOPE")
        data = read_performance_trend(engine, query)["data"]
        title = f"{metric[1]}趋势"
        series = [
            dict(
                key=chosen,
                label=metric[1],
                unit=metric[2],
                points=[dict(date=p["date"], **p["metrics"][chosen]) for p in data["points"]],
            )
        ]
        scope["metric"] = chosen
    elif capability == "sales_ratings":
        data = SalesDetails(engine).ratings(query, [query.shop_id] if query.shop_id else [])["data"]
        title = "店铺评分"
        columns = (
            ("shop_id", "店铺ID", "text"),
            ("date", "评分日期", "text"),
            ("score", "评分（百分制）", "number"),
            ("raw_score", "来源原值", "ratio"),
            ("state", "状态", "text"),
        )
        latest = [r for r in data["latest"] if not query.shop_id or r["shop_id"] == query.shop_id]
        tables = [
            _page("最新评分", columns, latest, params),
            _page(
                "期间评分",
                columns,
                sorted(data["points"], key=lambda r: (r["date"], r["shop_id"])),
                params,
            ),
        ]
        scope["latest_rating_date"] = data["latest_date"]
    else:
        product = capability == "sales_product_regions"
        data = RegionQueries(engine).regions(
            query,
            product=product,
            category=params.get("category") or "",
            product_key=params.get("product_key") or "",
        )["data"]
        title = "商品地区实发实退" if product else "店铺地区成交"
        metrics = PRODUCT_METRICS if product else REGION_METRICS
        states = {
            key: "not_connected"
            if data["source"]["state"] == "not_connected"
            else "no_records"
            if not data["region_count"]
            else "field_null"
            if data["totals"].get(key) is None
            else "observed"
            for key, _label, _unit in metrics
        }
        facts = _facts(dict(data["totals"], field_states=states), metrics)
        tables = [
            _page(
                title,
                (("region", "地区", "text"), *metrics, ("share", "金额占比", "percent")),
                data["items"],
                params,
            )
        ]
        if product:
            scope.update(category=params.get("category"), product_key=params.get("product_key"))
    return report(
        "sales",
        capability,
        title,
        SALES_SOURCE,
        version,
        period=period,
        scope=scope,
        facts=facts,
        tables=tables,
        series=series,
        notes=_coverage(data),
    )


def _options(engine, params, version):
    data = RegionQueries(engine).filters(
        version,
        params.get("category") or "",
        params.get("keyword") or "",
        params.get("offset", 0),
        params.get("limit", 10),
    )["data"]
    columns = (
        ("key", "产品选择键", "text"),
        ("category", "分类", "text"),
        ("code", "款号或货号", "text"),
        ("name", "名称", "text"),
    )
    return report(
        "sales",
        "sales_product_options",
        "销售产品候选",
        SALES_SOURCE,
        version,
        scope=dict(category=params.get("category"), keyword=params.get("keyword")),
        tables=[
            table("产品候选", columns, data["products"], total=data["total"], offset=data["offset"])
        ],
        notes=[
            "候选目录不按日期或店铺筛选，选择产品后再查询地区经营。",
            "来源状态：" + data["source"]["state"],
        ],
    )


def _targets(engine, params, version):
    month = params.get("month") or source_policy.calendar_through().strftime("%Y-%m")
    data = SalesDetails(engine).targets(month, version)["data"]
    members = data["target_coverage"]["member_shop_ids"]
    shop_id = params.get("shop_id")
    if shop_id and shop_id not in members:
        raise BoardError("INVALID_SCOPE")
    columns = (
        ("month_actual", "六店渠道月度完成额", "currency"),
        ("month_target", "六店渠道月度目标", "currency"),
        ("month_ratio", "六店渠道月度达成率", "percent"),
        ("gap", "六店渠道月度缺口", "currency"),
        ("annual_actual", "六店渠道财年完成额", "currency"),
        ("annual_target", "六店渠道财年目标", "currency"),
        ("annual_ratio", "六店渠道财年达成率", "percent"),
        ("month_missing_days", "六店渠道月度缺销售店铺日", "number"),
        ("annual_missing_days", "六店渠道财年缺销售店铺日", "number"),
    )
    facts = _facts(data, columns)
    scope = dict(
        month=month,
        shop_ids=members,
        target_basis=data["target_basis"],
        target_grain="channel",
        target_source_state=data["target_source"]["state"],
        shop_target_status="not_published",
        annual_window=data["annual_window"],
    )
    if shop_id:
        name = next(name for sid, name, _label in source_policy.SHOPS.values() if sid == shop_id)
        scope["requested_shop_id"] = shop_id
        facts = [
            fact(
                "shop_month_target",
                name + "月度目标",
                None,
                "currency",
                status="not_published",
                reason="目标台账只发布六店渠道目标。",
            ),
            fact(
                "shop_month_ratio",
                name + "月度达成率",
                None,
                "percent",
                status="unavailable",
                reason="单店目标未发布，无法计算达成率。",
            ),
            *facts,
        ]
    monthly = data["monthly_window"]
    return report(
        "sales",
        "sales_targets",
        "六店渠道销售目标进度",
        SALES_SOURCE,
        version,
        period=dict(date_from=monthly["start"], date_to=monthly["end"]),
        scope=scope,
        facts=facts,
        notes=[
            data["note"],
            "目标按所选月累计，与经营选店和自定义日期独立。单店目标及分配比例未发布，"
            "无法按成交占比分摊目标或计算单店达成率。",
        ],
    )


def _weather(engine, params, version):
    with weather_snapshot(engine, publication_id=params.get("view") or None, token=version) as repo:
        meta, publication = repo.meta, repo.data
        if not publication:
            return report(
                "weather",
                "weather",
                "全国天气",
                "已发布天气公报与中期预报",
                version,
                facts=[fact("status", "发布状态", meta["status"])],
                notes=["天气来源暂无已发布内容。"],
            )
        doc = publication["document"]
        rows = [
            dict(
                period="今日",
                topic=label,
                regions="、".join(doc[key]["regions"]),
                detail=doc[key]["detail"],
                status=doc[key]["status"],
            )
            for key, label in (("today_rain", "降雨"), ("today_temperature", "气温"))
        ]
        rows.extend(
            dict(
                period=p["period"],
                topic="未来过程",
                regions="、".join(p["regions"]),
                detail=p["detail"],
                status=p["status"],
            )
            for p in doc["next_7_days"]
        )
        day = publication["issue_date"]
        return report(
            "weather",
            "weather",
            doc["title"],
            "已发布天气公报与中期预报",
            version,
            period=dict(date_from=day, date_to=day),
            scope=dict(publication_id=publication["id"], revision_no=publication["revision_no"]),
            facts=[
                fact("status", "发布状态", meta["status"]),
                fact("last_attempt_status", "最近更新状态", meta.get("last_attempt_status")),
                fact("last_attempt_at", "最近尝试时间", meta.get("last_attempt_at")),
                fact("last_success_at", "本输入版最近成功", meta.get("last_success_at")),
                fact("error_code", "最近更新异常码", meta.get("error_code")),
                fact("issue_time", "发布时间", doc["issue_time"]),
                fact("focus_regions", "重点地区", "、".join(doc["focus_regions"])),
            ],
            tables=[
                _page(
                    "天气预报",
                    tuple(
                        (k, label, "text")
                        for k, label in (
                            ("period", "期间"),
                            ("topic", "天气"),
                            ("regions", "地区"),
                            ("detail", "预报内容"),
                            ("status", "识别状态"),
                        )
                    ),
                    rows,
                    params,
                )
            ],
            notes=[doc["note"], "未提及或未识别不等于没有相关天气。"],
        )


def _status(engine, params):
    from app.jobs.project_sync import read_schedule

    policy = read_schedule(Path(__file__).resolve().parents[5] / "config/sync.toml")
    value = read_status(
        engine,
        interval_seconds=policy.monitor_interval_seconds,
        enabled=policy.enabled and policy.monitor_enabled,
        source_schedule=policy,
    )
    data = value["data"]
    view = params.get("view") or "sources"
    if view == "sources":
        rows = [
            dict(
                id=r["id"],
                name=r["name"],
                platform_state=r["platform_state"],
                upstream_state=r["upstream"]["state"],
                checked_at=r["upstream"]["checked_at"],
                accepted_at=r["upstream"]["accepted_at"],
                error_code=r["upstream"]["error_code"],
            )
            for r in data["sources"]
        ]
        columns = (
            ("id", "来源ID", "text"),
            ("name", "来源", "text"),
            ("platform_state", "平台接收状态", "text"),
            ("upstream_state", "上游检查状态", "text"),
            ("checked_at", "检查时间", "text"),
            ("accepted_at", "接收时间", "text"),
            ("error_code", "异常码", "text"),
        )
    elif view in ("timeliness", "history", "reconciliation"):
        rows = []
        for source in data["sources"]:
            detail = source[view] or {}
            base = dict(id=source["id"], name=source["name"], **detail)
            if view == "history":
                groups = detail.get("groups", [])
                rows.extend(
                    dict(
                        base,
                        shop_id=g["shop_id"],
                        shop_name=g["shop_name"],
                        missing_dates="、".join(g["dates"]),
                    )
                    for g in groups
                )
                if not groups:
                    rows.append(base)
            else:
                rows.append(base)
        fields = {
            "timeliness": (
                ("state", "时效状态"),
                ("required_date", "应到业务日"),
                ("expected_by", "预计到数期限"),
                ("first_seen_at", "首次见数"),
                ("observed_at", "核对时间"),
                ("arrival_timing", "到数依据"),
            ),
            "history": (
                ("state", "历史覆盖状态"),
                ("window_start", "开始日"),
                ("window_end", "结束日"),
                ("checked_at", "核对时间"),
                ("shop_id", "店铺ID"),
                ("shop_name", "店铺"),
                ("missing_dates", "缺记录日期"),
            ),
            "reconciliation": (
                ("status", "对账状态"),
                ("cycle_at", "对账周期"),
                ("last_attempt_at", "最近尝试"),
                ("last_success_at", "最近成功"),
                ("next_retry_at", "下次重试"),
                ("next_scheduled_at", "下次计划"),
                ("error_code", "异常码"),
            ),
        }[view]
        columns = tuple(
            (k, label, "text") for k, label in (("id", "来源ID"), ("name", "来源"), *fields)
        )
        if view == "history":
            columns += (("missing_count", "缺记录店铺日数", "number"),)
    elif view in ("datasets", "processing"):
        rows = (
            data["processing"]
            if view == "processing"
            else [dict(d, source_name=r["name"]) for r in data["sources"] for d in r["datasets"]]
        )
        columns = tuple(
            (k, label, "text")
            for k, label in (
                ("code", "数据集ID"),
                ("name", "数据集"),
                ("source_name", "来源"),
                ("state", "处理状态"),
                ("date", "实际截至日"),
                ("version_id", "版本"),
                ("last_success_at", "最近成功"),
                ("last_attempt_at", "最近尝试"),
                ("attempt_status", "尝试状态"),
                ("error_code", "异常码"),
            )
        )
    else:
        raise BoardError("INVALID_SCOPE")
    return report(
        "data_status",
        "data_status",
        "数据更新状态",
        "运营中心本地接收与检查记录",
        None,
        scope=dict(view=view, generated_at=value["meta"]["generated_at"]),
        tables=[_page("数据更新状态", columns, rows, params)],
        notes=["检查成功不等于业务齐数；各来源日期与接收状态分别报告。"],
    )
