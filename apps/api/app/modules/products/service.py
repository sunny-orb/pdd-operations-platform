"""商品经营领域：原值/比率、日历比较、榜单、规则诊断；不生成评分或AI。"""

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil

from app.db import tables as t
from app.modules.products.ingestion import digest
from app.modules.products.query import METRICS, QueryError
from app.modules.products.repository import days_between, year_date

LABELS = dict(
    product_revenue="交易金额",
    visitors="商品访客数",
    favorites="收藏数",
    buyers="支付买家数",
    units="支付件数",
    revenue_per_buyer="客单价",
    buyer_conversion_rate="支付转化率",
    views="浏览量",
)
RULE_VERSION = "m1-online-risk-v1"
D = Decimal


def ratio(a, b):
    """分母严格为正才求比率；缺失保留NULL，异常比率不截到100%。"""
    return D(a) / D(b) if a is not None and b is not None and b > 0 else None


def change(a, b):
    return ratio(a - b, b) if a is not None and b is not None else None


def value_for(row, field, days):
    if field == "favorites" and days != 1:
        return None
    if field in ("revenue_per_buyer", "buyer_conversion_rate") and any(
        row.get(f + "_count", 0) != row.get("rows", 0) for f in required_fields(field)
    ):
        return None
    if field == "revenue_per_buyer":
        return ratio(row.get("product_revenue"), row.get("buyers"))
    if field == "buyer_conversion_rate":
        return ratio(row.get("buyers"), row.get("visitors"))
    return row.get(field)


def required_fields(field):
    return {
        "revenue_per_buyer": ("product_revenue", "buyers"),
        "buyer_conversion_rate": ("buyers", "visitors"),
    }.get(field, (field,))


def safe_json(value):
    """所有精确小数以十进制字符串传输；超JS安全整数也不丢精度。"""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {k: safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if isinstance(value, (datetime,)) or hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, int) and abs(value) > 9007199254740991:
        return str(value)
    return value


class ProductService:
    def __init__(self, view, query):
        self.view = view
        self.query = view.normalized(query)
        self.current = days_between(self.query.date_from, self.query.date_to)
        self._period_cache = {}

    def period(self, dates):
        """复用同一请求内聚合，已观察合计与可比性分别表示。"""
        key = tuple(dates)
        if key not in self._period_cache:
            row = self.view.grouped(self.query, dates)[0]
            coverage = self.view.coverage(self.query, dates)
            self._period_cache[key] = (row, coverage)
        return self._period_cache[key]

    def evaluate(self, field, row, coverage):
        count = coverage["expected_days"]
        reasons = []
        if field == "favorites" and count != 1:
            reasons.append("definition_pending")
        if not row["rows"]:
            reasons.append("no_records_received")
        if coverage["not_imported_dates"]:
            reasons.append("outside_imported_range")
        if coverage["missing_record_dates"]:
            reasons.append("missing_record_dates")
        if coverage["missing_product_dates"]:
            reasons.append("missing_product_dates")
        nulls = max(
            (row["rows"] - row.get(f + "_count", 0) for f in required_fields(field)), default=0
        )
        if nulls:
            reasons.append("null_source_value")
        result = value_for(row, field, count)
        if result is None and field in ("buyer_conversion_rate", "revenue_per_buyer"):
            reasons.append("invalid_denominator")
        status = (
            "missing"
            if not row["rows"] or "definition_pending" in reasons
            else ("partial" if reasons else "valid")
        )
        return dict(
            value=result,
            status=status,
            missing_reasons=reasons,
            missing_value_count=nulls,
            observed_record_count=row["rows"],
        )

    def pair(self, field, current_dates, previous_dates):
        row, coverage = self.period(current_dates)
        now = self.evaluate(field, row, coverage)
        oldrow, oldcoverage = self.period(previous_dates)
        old = self.evaluate(field, oldrow, oldcoverage)
        growth = (
            change(now["value"], old["value"])
            if (
                now["status"] == old["status"] == "valid"
                and coverage["all_observed"]
                and oldcoverage["all_observed"]
            )
            else None
        )
        return now, old, growth

    @staticmethod
    def window(dates):
        return dict(start_date=min(dates), end_date=max(dates)) if dates else None

    def overview(self):
        n = len(self.current)
        previous = [d - timedelta(days=n) for d in self.current]
        year = [year_date(d) for d in self.current]
        valid_year = [d for d in year if d]
        metrics = []
        for field in METRICS:
            now, old, mom = self.pair(field, self.current, previous)
            _, last, yoy = self.pair(field, self.current, valid_year)
            if len(valid_year) != n:
                yoy = None
            # 金额/人数/率统一value字符串，量纲显式给出。
            now["value"] = str(now["value"]) if now["value"] is not None else None
            metrics.append(
                dict(
                    key=field,
                    label=LABELS[field],
                    **now,
                    previous=str(old["value"]) if old["value"] is not None else None,
                    year_ago=str(last["value"]) if last["value"] is not None else None,
                    mom_change=mom,
                    yoy_change=yoy,
                    previous_status=old["status"],
                    year_ago_status=last["status"] if len(valid_year) == n else "not_comparable",
                    unit="ratio"
                    if field == "buyer_conversion_rate"
                    else "yuan"
                    if field in ("product_revenue", "revenue_per_buyer")
                    else "count",
                )
            )
        periods = []
        end = self.query.date_to
        for length in (7, 14, 30):
            dates = days_between(end - timedelta(days=length - 1), end)
            prev = [d - timedelta(days=length) for d in dates]
            a, b, growth = self.pair(self.query.metric, dates, prev)
            periods.append(
                dict(
                    days=length,
                    metric_key=self.query.metric,
                    **a,
                    previous=b["value"],
                    mom_change=growth,
                    window=self.window(dates),
                    previous_window=self.window(prev),
                    coverage=self.period(dates)[1],
                    previous_coverage=self.period(prev)[1],
                )
            )
        row, cov = self.period(self.current)
        funnel = [
            dict(key=f, label=LABELS[f], **self.evaluate(f, row, cov))
            for f in ("views", "visitors", "favorites", "buyers", "units")
        ]
        data = dict(
            metrics=metrics,
            periods=periods,
            funnel=funnel,
            health=dict(
                status="insufficient_evidence",
                title="来源完整性待补充",
                description="当前显示已观察数据，暂不作完整增长健康度判断",
            ),
            windows=dict(
                current=self.window(self.current),
                previous=self.window(previous),
                year_ago=self.window(valid_year),
                year_ago_dates=year,
            ),
            coverage=dict(
                current=cov, previous=self.period(previous)[1], year_ago=self.period(valid_year)[1]
            ),
            matched_record_count=row["rows"],
        )
        return safe_json(data)

    def days(self):
        total, rows = self.view.details(self.query)
        for row in rows:
            row["buyer_conversion_rate"] = ratio(row.pop("buyers"), row["visitors"])
            row["key"] = ":".join(str(row[k]) for k in (*t.K, "business_date"))
            old_date = year_date(row["business_date"])
            # 复合键限定去年值，绝不按商品名/独立商品ID跨店补数。
            oldq = self.query.model_copy(
                update={"shop_id": row["shop_id"], "product_id": row["product_id"]}
            )
            old = self.view.grouped(oldq, [old_date] if old_date else [])[0]
            row["year_ago_revenue"] = old["product_revenue"]
            row["yoy_change"] = change(row["product_revenue"], old["product_revenue"])
            row["conversion_status"] = (
                "valid" if row["buyer_conversion_rate"] is not None else "not_computable"
            )
        return safe_json(
            dict(items=rows, total=total, page=self.query.page, page_size=self.query.page_size)
        )

    def filters(self):
        products = list(self.view.products(self.query, self.current).values())
        products.sort(key=lambda r: (r["shop_id"], r["product_id"]))
        offset = (self.query.page - 1) * self.query.page_size
        # 选项按上游维度级联；搜索/下游条件不能吞掉仍有效的上游选择。
        dimensions = {}
        for index, field in enumerate(("operator", "category", "level")):
            reset = dict.fromkeys(("operator", "category", "level")[index:])
            reset.update(keyword=None, product_key=[], product_id=None)
            candidates = self.view.products(self.query.model_copy(update=reset), self.current)
            dimensions[field] = sorted({r[field] for r in candidates.values() if r[field]})
        return safe_json(
            dict(
                shops=self.view.shops,
                operators=dimensions["operator"],
                categories=dimensions["category"],
                levels=dimensions["level"],
                products=products[offset : offset + self.query.page_size],
                product_total=len(products),
                page=self.query.page,
                page_size=self.query.page_size,
                recommended_window=dict(
                    start_date=self.view.latest - timedelta(days=29), end_date=self.view.latest
                ),
                profile_basis="current",
            )
        )

    def trend(self):
        query = self.query
        if query.comparison in ("products", "relative"):
            if not query.product_key and not query.product_id:
                raise QueryError(422, "PRODUCT_SELECTION_REQUIRED", "单品对比请选1至20个店铺商品")
            products = self.view.products(query, self.current)
            series = []
            for key, product in sorted(products.items()):
                q = query.model_copy(
                    update={"shop_id": key[1], "product_id": key[2], "product_key": [":".join(key)]}
                )
                rows = self.view.grouped(q, self.current, by=("business_date",))
                index = {r["business_date"]: r for r in rows}
                values = [value_for(index.get(d, {}), query.metric, 1) for d in self.current]
                base = next(
                    (
                        (d, v)
                        for d, v in zip(self.current, values, strict=True)
                        if v is not None and v > 0
                    ),
                    (None, None),
                )
                series.append(
                    dict(
                        product=product,
                        key=":".join(key),
                        baseline_date=base[0] if query.comparison == "relative" else None,
                        points=[
                            dict(
                                date=d,
                                value=ratio(v, base[1]) if query.comparison == "relative" else v,
                            )
                            for d, v in zip(self.current, values, strict=True)
                        ],
                    )
                )
            return safe_json(dict(mode=query.comparison, metric_key=query.metric, series=series))
        mapped = {d: year_date(d) for d in self.current}
        dates = sorted(set(self.current + [d for d in mapped.values() if d]))
        rows = {
            r["business_date"]: r for r in self.view.grouped(query, dates, by=("business_date",))
        }
        points = []
        for day in self.current:
            current = rows.get(day, {})
            prior = rows.get(mapped[day], {})
            points.append(
                dict(
                    date=day,
                    value=value_for(current, query.metric, 1),
                    year_ago=value_for(prior, query.metric, 1),
                    year_ago_date=mapped[day],
                    missing_value_count=max(
                        (
                            current.get("rows", 0) - current.get(f + "_count", 0)
                            for f in required_fields(query.metric)
                        ),
                        default=0,
                    ),
                )
            )
        return safe_json(
            dict(
                mode="year",
                metric_key=query.metric,
                points=points,
                note="实际日历点；空点不补零；金额和比率为精确十进制字符串",
            )
        )

    def alerts(self):
        end = self.query.date_to
        dates = days_between(end - timedelta(days=7), end)
        products = self.view.products(self.query, self.current)
        rows = self.view.grouped(self.query, dates, by=(*t.K, "business_date"))
        indexed = defaultdict(dict)
        for row in rows:
            indexed[tuple(row[k] for k in t.K)][row["business_date"]] = row
        totals = {
            tuple(r[k] for k in t.K): r for r in self.view.grouped(self.query, self.current, by=t.K)
        }
        eligible = {
            key: p
            for key, p in products.items()
            if p["level"] and p["level"] not in ("清仓", "未匹配")
        }
        ranking_supported = self.period(self.current)[1]["all_observed"] and all(
            totals[k]["product_revenue_count"] == totals[k]["rows"]
            and totals[k]["observed_days"] == len(self.current)
            for k in eligible
        )
        # 候选金额/期间不齐时不把未知当0来排位；保留流量现象，P0升级待核。
        rank = (
            sorted(eligible, key=lambda k: (-totals[k]["product_revenue"], k))
            if ranking_supported
            else []
        )
        top = set(rank[: ceil(len(rank) * 0.2)])
        filter_hash = digest(
            self.query.model_dump(
                exclude={"page", "page_size", "data_version", "metric", "comparison", "scope"}
            )
        )[:20]
        items, unassessed = [], []
        for key, product in products.items():
            if key not in eligible:
                unassessed.append(
                    dict(shop_id=key[1], product_id=key[2], reason="level_missing_or_excluded")
                )
                continue
            values = indexed[key]
            if any(
                d not in values
                or any(
                    values[d].get(f + "_count", 0) != values[d]["rows"] or values[d][f] < 0
                    for f in ("visitors", "orders")
                )
                for d in dates
            ):
                unassessed.append(
                    dict(shop_id=key[1], product_id=key[2], reason="risk_inputs_missing")
                )
                continue
            hit = assess_risk([values[d] for d in dates], key in top)
            if hit:
                hit.update(
                    product=product,
                    shop_id=key[1],
                    product_id=key[2],
                    id=f"{self.view.token}:{RULE_VERSION}:{filter_hash}:{key[1]}:{key[2]}:{hit['rule_id']}",
                    period_revenue=totals[key]["product_revenue"],
                    period_window=self.window(self.current),
                    observation_date=end,
                    baseline_window=self.window(dates[:-1]),
                    rule_version=RULE_VERSION,
                    due_at=None,
                    handling_status="not_enabled",
                    assessment_basis="observed_inputs",
                    priority_status="upgrade_not_verified"
                    if (hit["rule_id"] == "severe_traffic_drop" and not ranking_supported)
                    else "observed",
                )
                items.append(hit)
        items.sort(key=lambda r: (r["priority"], -r["risk_score"], r["shop_id"], r["product_id"]))
        offset = (self.query.page - 1) * self.query.page_size
        return safe_json(
            dict(
                items=items[offset : offset + self.query.page_size],
                total=len(items),
                unassessed_count=len(unassessed),
                unassessed=unassessed[offset : offset + self.query.page_size],
                page=self.query.page,
                page_size=self.query.page_size,
                ranking_status="observed_ready" if ranking_supported else "insufficient_evidence",
            )
        )

    def brief(self):
        end = self.query.date_to
        current = days_between(end - timedelta(days=6), end)
        previous = days_between(end - timedelta(days=13), end - timedelta(days=7))
        products = self.view.products(self.query, current)

        def totals(dates, by):
            return {
                tuple(r.get(k) for k in by): r for r in self.view.grouped(self.query, dates, by=by)
            }

        now, old = totals(current, t.K), totals(previous, t.K)
        ranks, rising = [], []
        for key, row in now.items():
            if row["product_revenue"] is None or row["product_revenue"] <= 0:
                continue
            before = old.get(key)
            comparable = (
                row["observed_days"] == 7
                and before
                and before["observed_days"] == 7
                and row["product_revenue_count"] == row["rows"]
                and before["product_revenue_count"] == before["rows"]
            )
            growth = (
                change(row["product_revenue"], before["product_revenue"]) if comparable else None
            )
            item = dict(
                product=products[key],
                product_revenue=row["product_revenue"],
                previous_revenue=before["product_revenue"] if before else None,
                change=growth,
                status="observed" if comparable else "partial",
                buyer_conversion_rate=value_for(row, "buyer_conversion_rate", 7),
                conversion_status="partial"
                if any(row[f + "_count"] != row["rows"] for f in ("buyers", "visitors"))
                else (
                    "valid"
                    if ratio(row["buyers"], row["visitors"]) is not None
                    else "not_computable"
                ),
            )
            ranks.append(item)
            if products[key]["level"] == "新品" and growth is not None and growth > 0:
                rising.append(item)
        ranks.sort(
            key=lambda r: (
                -r["product_revenue"],
                r["product"]["shop_id"],
                r["product"]["product_id"],
            )
        )
        rising.sort(
            key=lambda r: (
                -r["change"],
                -r["product_revenue"],
                r["product"]["shop_id"],
                r["product"]["product_id"],
            )
        )
        cats = totals(current, ("category",))
        categories = []
        unassigned = None
        known = sorted(
            (
                key
                for key, row in cats.items()
                if key[0] is not None
                and row["product_revenue"] is not None
                and row["product_revenue"] > 0
            ),
            key=lambda k: (-cats[k]["product_revenue"], k[0]),
        )[:3]
        for key in known + ([(None,)] if (None,) in cats else []):
            row = cats[key]
            category_query = self.query.model_copy(update={"category": key[0] or "__unassigned__"})
            service = ProductService(self.view, category_query)
            now_value, _, growth = service.pair("product_revenue", current, previous)
            item = dict(
                category=key[0],
                label=key[0] or "未分类/资料冲突",
                product_revenue=row["product_revenue"],
                change=growth,
                status="observed" if now_value["status"] == "valid" else "partial",
                missing_reasons=now_value["missing_reasons"],
            )
            if key[0] is None:
                unassigned = item
            else:
                categories.append(item)
        categories.sort(key=lambda r: (-r["product_revenue"], r["label"]))
        risk_query = self.query.model_copy(update={"page": 1, "page_size": 3})
        return safe_json(
            dict(
                categories=categories[:3],
                unassigned_category=unassigned,
                products=ranks[:3],
                rising=rising[:3],
                declining=ProductService(self.view, risk_query).alerts()["items"],
                actions=[],
                action_generation_status="not_connected",
                window=self.window(current),
                previous_window=self.window(previous),
                coverage=self.period(current)[1],
            )
        )

    def diagnosis(self):
        overview = self.overview()
        metric = next((m for m in overview["metrics"] if m["key"] == self.query.metric), None)
        if metric is None:
            row, cov = self.period(self.current)
            metric = safe_json(
                dict(
                    key=self.query.metric,
                    label=LABELS[self.query.metric],
                    **self.evaluate(self.query.metric, row, cov),
                )
            )
        evidence = []
        for m in overview["metrics"]:
            label = m["label"]
            if m["yoy_change"] is not None:
                evidence.append(
                    dict(
                        metric_key=m["key"],
                        label=label,
                        comparison="year_ago",
                        change=m["yoy_change"],
                        status="observed",
                    )
                )
            elif m["mom_change"] is not None:
                evidence.append(
                    dict(
                        metric_key=m["key"],
                        label=label,
                        comparison="previous",
                        change=m["mom_change"],
                        status="observed",
                    )
                )
        alerts = ProductService(
            self.view, self.query.model_copy(update={"page": 1, "page_size": 1})
        ).alerts()
        return dict(
            headline="已观察经营数据核查",
            selected_metric=metric,
            evidence=evidence,
            action=alerts["items"][0]["suggested_action"]
            if alerts["items"]
            else "先核对数据覆盖与缺项，再检查转化、客单价及流量变化。",
            missing_reasons=overview["metrics"][0]["missing_reasons"],
            generation_method="rules",
            generated_at=datetime.now(UTC).isoformat(),
            is_causal_explanation=False,
        )


def assess_risk(rows, is_top):
    """输入T前7日及T共8条有效指标；按线上分支先后返回首个命中。"""
    previous, today = rows[:-1], rows[-1]
    v, o = D(today["visitors"]), D(today["orders"])
    vb = sum(D(r["visitors"]) for r in previous)
    ob = sum(D(r["orders"]) for r in previous)
    a, c, cb = vb / 7, ratio(o, v), ratio(ob, vb)
    revenues_valid = all(
        r.get("product_revenue") is not None
        and r["product_revenue"] >= 0
        and r.get("product_revenue_count", r.get("rows", 1)) == r.get("rows", 1)
        for r in previous
    )
    gb = sum((r["product_revenue"] for r in previous), D(0)) if revenues_valid else None
    pb = ratio(gb, ob) if gb is not None and gb >= 0 else None
    traffic_drop = change(v, a)
    rule = None
    if v >= 100 and o == 0:
        rule = ("no_transaction", "有流量无成交", "P1", 100, o, ob / 7, "orders")
        impact = v * cb * pb if cb is not None and pb is not None else None
    elif v >= 100 and cb and c is not None and c <= D(".70") * cb:
        rule = ("severe_order_conversion_drop", "严重订单转化下滑", "P1", 90, c, cb, "conversion")
        impact = v * max(D(0), cb - c) * pb if pb is not None else None
    elif a >= 30 and traffic_drop <= D("-.4") and a - v >= 30:
        top = is_top and ob >= 10
        rule = (
            "severe_traffic_drop",
            "爆品严重流量下滑" if top else "严重流量下滑",
            "P0" if top else "P1",
            95 if top else 85,
            v,
            a,
            "visitors",
        )
        impact = (a - v) * cb * pb if cb is not None and pb is not None else None
    elif v >= 100 and cb and c is not None and c <= D(".85") * cb:
        rule = ("moderate_order_conversion_drop", "中度订单转化下滑", "P2", 70, c, cb, "conversion")
        impact = v * max(D(0), cb - c) * pb if pb is not None else None
    elif a >= 30 and traffic_drop <= D("-.2") and a - v >= 20:
        rule = ("moderate_traffic_drop", "中度流量下滑", "P2", 65, v, a, "visitors")
        impact = (a - v) * cb * pb if cb is not None and pb is not None else None
    if rule is None:
        return None
    rid, title, priority, score, current, baseline, field = rule
    action = (
        "检查活动状态、素材、推广计划与自然流量入口。"
        if field == "visitors"
        else ("核查价格、优惠、评价、详情页和商品可售状态。")
    )
    trend = [
        ratio(r["orders"], r["visitors"]) if field == "conversion" else r[field] for r in rows[-7:]
    ]
    return dict(
        rule_id=rid,
        risk_title=title,
        priority=priority,
        risk_score=score,
        current_value=current,
        baseline_value=baseline,
        change_rate=change(current, baseline),
        trend=trend,
        trend_metric=field,
        estimated_impact_amount=impact,
        impact_status="estimated" if impact is not None else "not_computable",
        impact_missing_reasons=[] if impact is not None else ["baseline_impact_inputs_invalid"],
        reason_tags=[title, "经营原因待核查"],
        reason_kind="hypothesis",
        suggested_action=action,
    )
