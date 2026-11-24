"""问答的三个只读工具。店铺商品身份、事实及规则均来自看板已发布版本。"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.modules.assistant.versions import report_versions
from app.modules.product_board.comparison import period_comparisons, summarize_period
from app.modules.product_board.filter_projection import matches_keyword
from app.modules.product_board.metrics import METRICS, aggregate, compare, risk, risk_window_ready
from app.modules.product_board.query import labels, top_revenue_listings
from app.modules.product_board.repository import BoardError, wire
from app.modules.product_board.repository import Repository as ProductRepository
from app.modules.product_board.repository import snapshot as product_snapshot
from app.modules.promotion.diagnosis import RULE_VERSION, assess
from app.modules.promotion.metrics import product_metrics
from app.modules.promotion.query import MAIN_KEYS, dates_between, period_change
from app.modules.promotion.repository import Repository as PromotionRepository
from app.modules.promotion.repository import snapshot as promotion_snapshot

PRODUCT_KEYS = ("revenue", "visitors", "buyers", "conversion")
MAX_DAYS = 31
MAX_CANDIDATES = 8


def _product(item):
    return dict(
        listing_id=item["listing_id"],
        product_id=item["product_id"],
        shop_id=item["shop_id"],
        name=item["product_name"] or item["product_id"],
        shop_name=item["shop_name"] or item["shop_id"],
    )


def search_products(engine, keyword: str, *, shop_id: str | None = None) -> list[dict]:
    """按本地已发布商品目录查名称、别名或 ID；跨店同名保留为独立候选。"""
    keyword = keyword.strip()
    if not keyword:
        return []
    if len(keyword) > 120:
        raise BoardError("INVALID_SCOPE")
    with product_snapshot(engine) as repo:
        ads = PromotionRepository(repo.c, "product", repo.versions, repo.token)
        if _listing_uuid(keyword) == keyword.lower():
            try:
                selection = _selected(repo, ads, keyword)
            except BoardError as exc:
                if exc.code == "UNKNOWN_LISTING_ID":
                    return []
                raise
            return [_product(selection)] if not shop_id or selection["shop_id"] == shop_id else []
        exact, partial = {}, {}
        for catalog in _catalogs(repo, ads):
            for lid, item in catalog.items():
                if shop_id and item["shop_id"] != shop_id:
                    continue
                if keyword in (lid, item["product_id"]):
                    exact.setdefault(lid, item)
                elif matches_keyword(item, keyword):
                    partial.setdefault(lid, item)
        matches = sorted((exact or partial).values(), key=lambda p: (p["shop_id"], p["product_id"]))
        return [_product(item) for item in matches[:MAX_CANDIDATES]]


def _range(products, ads, date_from, date_to, recent_days):
    """默认跟随经营已发布截至日；显式窗口允许来自经营或商品推广页。"""
    if type(recent_days) is not int or not 1 <= recent_days <= MAX_DAYS:
        raise BoardError("INVALID_SCOPE")
    if (date_from is None) != (date_to is None):
        raise BoardError("INVALID_SCOPE")
    end = products.end if date_to is None else date_to
    start = end - timedelta(days=recent_days - 1) if date_from is None else date_from
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if end < start or end > today or (end - start).days >= MAX_DAYS:
        raise BoardError("INVALID_SCOPE")
    if not products.start <= start <= end <= products.end and not (
        date_from is not None and ads.start and ads.end and ads.start <= start <= end <= ads.end
    ):
        raise BoardError("INVALID_SCOPE")
    return dict(
        date_from=start,
        date_to=end,
        previous_from=start - timedelta(days=(end - start).days + 1),
        previous_to=start - timedelta(days=1),
    )


def _listing_uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _catalogs(products, ads, listing_id=None):
    """同快照读取两个目录，按店铺和平台商品双键核验 UUID，可查询单个身份。"""
    ids = (listing_id,) if listing_id else None
    operating = (
        products.rows(products.start, products.end, listing_ids=ids, risk_only=True)
        if ids
        else products.identity_rows(products.start, products.end)
    )
    promotion = ads.identity_rows(listing_ids=ids)
    keys = {}
    for rows in (operating, promotion):
        for row in rows:
            key = (row["source_shop_id"], row["source_product_id"])
            if keys.setdefault(row["listing_id"], key) != key:
                raise BoardError("INCOMPATIBLE_INPUTS", 503)
    profiles = products.profiles()
    return (
        labels(operating, profiles)[0],
        labels((dict(row, business_date=row["date"]) for row in promotion), profiles)[0],
    )


def _selected(products, ads, listing_id):
    canonical = _listing_uuid(listing_id)
    if canonical is None:
        raise BoardError("UNKNOWN_LISTING_ID")
    operating, promotion = _catalogs(products, ads, canonical)
    info = operating.get(canonical) or promotion.get(canonical)
    if info is None:
        raise BoardError("UNKNOWN_LISTING_ID")
    return info


def _by_day(rows, day_key, identity):
    grouped = defaultdict(list)
    for row in rows:
        if (row["listing_id"], row["source_shop_id"], row["source_product_id"]) != (
            identity["listing_id"],
            identity["shop_id"],
            identity["product_id"],
        ):
            raise BoardError("INCOMPATIBLE_INPUTS", 503)
        grouped[row[day_key]].append(row)
    return grouped


def _period_rows(groups, start, end):
    return [row for day in dates_between(start, end) for row in groups.get(day, ())]


def _coverage(groups, start, end):
    days = dates_between(start, end)
    missing = [day for day in days if day not in groups]
    return dict(
        observed_days=len(days) - len(missing), expected_days=len(days), missing_dates=missing
    )


def _windows(groups, period):
    start, end = period["date_from"], period["date_to"]
    previous_start, previous_end = period["previous_from"], period["previous_to"]
    return (
        _period_rows(groups, start, end),
        _period_rows(groups, previous_start, previous_end),
        dict(
            current=_coverage(groups, start, end),
            previous=_coverage(groups, previous_start, previous_end),
            complete_through=None,
        ),
    )


def _series(groups, period, keys, calculate):
    points = []
    for day in dates_between(period["date_from"], period["date_to"]):
        metrics = calculate(groups.get(day, []))
        points.append(dict(date=day, values={key: metrics[key]["value"] for key in keys}))
    return points


def _product_rules(repo, identity, groups, period, current):
    """单款复用商品风险阈值和同店金额排名，估算及核查动作均出自生效规则。"""
    start, end = period["date_from"], period["date_to"]
    baseline_start = end - timedelta(days=7)
    rows = _period_rows(groups, max(baseline_start, repo.start), end)
    alert = None
    if not current:
        status = "empty"
    elif not identity["risk_eligible"]:
        status = "not_applicable"
    elif not risk_window_ready(rows, end):
        status = "insufficient_data"
    else:
        status = "evaluated"
        members = {
            lid
            for lid, sid in repo.dimension_identity_values(start, end)
            if sid == identity["shop_id"]
        }
        amounts = repo.period_revenue_by_listing(start, end)
        top = top_revenue_listings({lid: amounts[lid] for lid in members if lid in amounts})
        alert = risk(rows, end, identity["listing_id"] in top)
    return dict(
        version="product-risk-traffic-v2",
        ranking_basis="爆品按同店商品在所选期间的成交额排名，取前20%，边界同额均计入。",
        status=status,
        date_to=end,
        baseline_from=baseline_start,
        baseline_to=end - timedelta(days=1),
        findings=[alert] if alert else [],
    )


def product_performance(
    engine,
    listing_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    recent_days: int = 7,
) -> dict:
    """按一个店铺商品及相邻等长日历窗读取经营，比较复用商品看板期间口径。"""
    with product_snapshot(engine) as repo:
        ads = PromotionRepository(repo.c, "product", repo.versions, repo.token)
        period = _range(repo, ads, date_from, date_to, recent_days)
        identity = _selected(repo, ads, listing_id)
        start = min(period["previous_from"], period["date_to"] - timedelta(days=7))
        rows = repo.rows(start, period["date_to"], listing_ids=(identity["listing_id"],))
        groups = _by_day(rows, "business_date", identity)
        current, previous, coverage = _windows(groups, period)
        observed = aggregate(current, only=PRODUCT_KEYS)
        comparisons = period_comparisons(
            summarize_period(current), summarize_period(previous), "前期"
        )
        metrics = []
        for key, label, unit in METRICS:
            if key not in PRODUCT_KEYS:
                continue
            value = comparisons[key]
            status = value["reason"] or "observed"
            reason = value["message"]
            if observed[key]["status"] == "partial":
                status = "partial"
                reason = "部分字段缺失，显示可用行的小计。" + (reason or "")
            elif status == "observed" and (
                coverage["current"]["missing_dates"] or coverage["previous"]["missing_dates"]
            ):
                status, reason = "partial", "部分日期暂无经营记录，按已观察日期比较"
            if period["previous_from"] < repo.versions["product_daily"]["period_start"]:
                value = dict(value, previous=None, change=None)
                status, reason = "history_not_received", "前一期超出版本的已接收期间"
            metrics.append(
                dict(
                    key=key,
                    label=label,
                    unit=unit,
                    current=observed[key]["value"],
                    previous=value["previous"],
                    change=value["change"],
                    status=status,
                    reason=reason,
                )
            )
        return wire(
            dict(
                kind="product_performance",
                product=_product(identity),
                period=period,
                metrics=metrics,
                coverage=coverage,
                data_version=repo.token,
                versions=report_versions(repo, from_promotion=False),
                evidence=dict(
                    source="商品数据 · 商品日经营",
                    rules=_product_rules(repo, identity, groups, period, current),
                ),
                series=_series(
                    groups, period, PRODUCT_KEYS, lambda r: aggregate(r, only=PRODUCT_KEYS)
                ),
            )
        )


def _promotion_rules(repo, groups, end):
    if end is None:
        return dict(version=RULE_VERSION, status="insufficient_evidence", date_to=None, findings=[])
    current_start = end - timedelta(days=6)
    previous_start = current_start - timedelta(days=7)
    previous_end = current_start - timedelta(days=1)
    result = assess(
        _period_rows(groups, current_start, end),
        _period_rows(groups, previous_start, previous_end),
        current_available=repo.start is not None and repo.start <= current_start,
        previous_available=repo.start is not None and repo.start <= previous_start,
    )
    return dict(
        version=RULE_VERSION,
        status=result["evaluation_state"],
        priority=result["priority"],
        date_from=current_start,
        date_to=end,
        baseline_from=previous_start,
        baseline_to=previous_end,
        findings=result["findings"],
        checks=result["rule_results"],
        current_days=result["current_days"],
        previous_days=result["previous_days"],
    )


def _promotion_comparison_state(current, previous, *, history, covered):
    """以两期实际状态解释不可比或仅供参考的数字，保留当期可用值。"""
    issues = []
    for label, metric in (("本期", current), ("前期", previous)):
        state = metric["status"]
        if label == "前期" and not history:
            issues.append(("history_not_received", "前一期超出推广版本的已接收期间"))
        elif state != "observed":
            reason = {
                "no_records": "没有推广记录",
                "missing": "缺少计算所需字段",
                "zero_denominator": "分母为零，不计算比率",
                "partial": "部分字段缺失，仅显示已知值",
            }.get(state, metric["reason"] or "数据不足")
            issues.append((state, label + reason))
    if current["value"] is not None and previous["value"] == 0 and history:
        reason = (
            "前期为0，不计算变化率"
            if previous["status"] == "observed"
            else "前期已知值为0，不计算变化率"
        )
        issues.append(("zero_baseline", reason))
    if not covered and history:
        issues.append(("partial", "部分日期暂无推广记录"))
    if issues:
        return issues[0][0], "；".join(message for _, message in issues)
    return current["status"], current["reason"]


def product_promotion(
    engine,
    listing_id: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    recent_days: int = 7,
) -> dict:
    """同一事务解析商品和推广，推广缺日不改变已选经营期间；SOP 独立使用相邻七日。"""
    with promotion_snapshot(engine, "product") as repo:
        products = ProductRepository(repo.c, repo.versions, repo.token)
        period = _range(products, repo, date_from, date_to, recent_days)
        identity = _selected(products, repo, listing_id)
        yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
        rules_end = min(period["date_to"], repo.end, yesterday) if repo.end else None
        windows = [(period["previous_from"], period["date_to"])]
        if rules_end:
            windows.append((rules_end - timedelta(days=13), rules_end))
        rows = repo.rows_for_windows(windows, listing_ids=(identity["listing_id"],))
        groups = _by_day(rows, "date", identity)
        current, previous, coverage = _windows(groups, period)
        now, before = product_metrics(current), product_metrics(previous)
        covered = not (
            coverage["current"]["missing_dates"] or coverage["previous"]["missing_dates"]
        )
        history = repo.start is not None and period["previous_from"] >= repo.start
        metrics = []
        for key in MAIN_KEYS:
            m, p = now[key], before[key]
            # 三项累计卡按已有行比较，其他卡沿用推广总览的两期日期和字段门控。
            if key in ("spend", "impressions", "clicks"):
                period_change(m, p, history_available=history)
            elif history and covered and m["status"] == p["status"] == "observed":
                m["change"] = compare(m["value"], p["value"])
            status, reason = _promotion_comparison_state(m, p, history=history, covered=covered)
            metrics.append(
                dict(
                    key=key,
                    label="推广归因成交额" if key == "revenue" else m["label"],
                    unit=m["type"],
                    current=m["value"],
                    previous=p["value"] if history else None,
                    change=m["change"] if history else None,
                    status=status,
                    reason=reason,
                )
            )
        return wire(
            dict(
                kind="product_promotion",
                product=_product(identity),
                period=period,
                metrics=metrics,
                coverage=coverage,
                data_version=repo.token,
                versions=report_versions(repo, from_promotion=True),
                evidence=dict(
                    source="推广分析 · 商品推广", rules=_promotion_rules(repo, groups, rules_end)
                ),
                series=_series(groups, period, MAIN_KEYS, product_metrics),
            )
        )
