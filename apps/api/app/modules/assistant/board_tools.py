"""商品与推广看板的助手只读适配；所有指标、排序和规则复用领域查询。"""

from dataclasses import replace
from datetime import timedelta

from app.modules.assistant.business import fact, report, table, window
from app.modules.assistant.versions import report_versions
from app.modules.product_board import query as products
from app.modules.product_board.repository import BoardError, wire
from app.modules.product_board.repository import snapshot as product_snapshot
from app.modules.promotion import diagnosis as promotion_rules
from app.modules.promotion import query as promotion
from app.modules.promotion.metrics import SPECS
from app.modules.promotion.repository import snapshot as promotion_snapshot

PRODUCT_CAPABILITIES = (
    "products_overview",
    "products_risks",
    "products_scores",
    "products_days",
    "products_catalog",
)
PROMOTION_CAPABILITIES = (
    "promotion_overview",
    "promotion_days",
    "promotion_diagnosis",
    "promotion_objects",
    "promotion_catalog",
)
IDENTITY = (
    ("listing_id", "店铺商品标识", "text"),
    ("shop_id", "店铺ID", "text"),
    ("shop_name", "店铺", "text"),
    ("product_id", "商品ID", "text"),
    ("product_name", "商品", "text"),
    ("category", "品类", "text"),
    ("level", "等级", "text"),
    ("operator", "运营", "text"),
)
PRODUCT_METRICS = (
    ("visitors", "商品访客数", "number"),
    ("revenue", "交易金额", "currency"),
    ("conversion", "支付转化率", "percent"),
    ("yoy", "金额同比", "percent"),
)
AD_METRICS = tuple((key, *SPECS[key]) for key in (*promotion.MAIN_KEYS, "net_revenue", "net_roi"))


def _projection(item, columns):
    return {key: item.get(key) for key, _, _ in columns}


def _period(start, end):
    return dict(date_from=start, date_to=end)


def _limited_table(title, columns, items, limit, offset, *, total=None):
    return table(
        title,
        columns,
        [_projection(row, columns) for row in items[offset : offset + limit]],
        total=len(items) if total is None else total,
        offset=offset,
    )


def _page(loader, repo, query, limit, offset):
    """领域固定25条分页；任意offset最多读取相邻两页，不下载全量经营明细。"""
    first, skip = divmod(offset, 25)
    result = loader(repo, replace(query, page=first + 1, size=25))
    selected = result["items"][skip : skip + limit]
    if len(selected) < limit and offset + len(selected) < result["total"]:
        following = loader(repo, replace(query, page=first + 2, size=25))
        selected.extend(following["items"][: limit - len(selected)])
    return dict(result, items=selected)


def _validate(params, *, multi_dimensions=False):
    if not isinstance(params, dict):
        raise BoardError("INVALID_SCOPE")
    limit, offset, days = (
        params.get("limit", 10),
        params.get("offset", 0),
        params.get("recent_days", 7),
    )
    if (
        type(limit) is not int
        or not 1 <= limit <= 25
        or type(offset) is not int
        or not 0 <= offset <= 2499975
        or type(days) is not int
        or not 1 <= days <= 366
    ):
        raise BoardError("INVALID_SCOPE")
    token = params.get("data_version")
    if not isinstance(token, str) or not 1 <= len(token) <= 100:
        raise BoardError("INVALID_SCOPE")
    for key in ("shop_id", "category", "level", "operator", "keyword", "channel"):
        value = params.get(key, "")
        if (
            multi_dimensions
            and key in ("shop_id", "category", "level", "operator")
            and isinstance(value, (list, tuple))
        ):
            # 商品页同框取并集、跨框取交集；坏成员不能被丢弃后扩大经营范围。
            if not 1 <= len(value) <= 20 or any(
                not isinstance(item, str) or not 1 <= len(item) <= 120 or not item.strip()
                for item in value
            ):
                raise BoardError("INVALID_SCOPE")
            continue
        if not isinstance(value, str) or len(value) > 120:
            raise BoardError("INVALID_SCOPE")
    ids = params.get("listing_ids", [])
    if not isinstance(ids, list):
        raise BoardError("INVALID_SELECTED_IDS")
    promotion.validate_ids(ids)
    return limit, offset


def _query(repo, params, *, mode=None):
    try:
        start, end = window(params, repo.start, repo.end)
    except (ValueError, TypeError) as exc:
        raise BoardError("INVALID_SCOPE") from exc
    selections = {
        key: tuple(dict.fromkeys(value))
        if mode is None and isinstance(value, (list, tuple))
        else value
        for key in ("shop_id", "category", "level", "operator")
        for value in (params.get(key, ""),)
    }
    kwargs = dict(
        start=start,
        end=end,
        shop=selections["shop_id"],
        category=selections["category"],
        level=selections["level"],
        operator=selections["operator"],
        keyword=params.get("keyword", ""),
        products=tuple(dict.fromkeys(params.get("listing_ids", []))),
    )
    query = (
        products.BoardQuery(**kwargs)
        if mode is None
        else promotion.PromotionQuery(**kwargs, mode=mode, channel=params.get("channel", ""))
    )
    query.validate(repo)
    return query


def _scope(query, params):
    return dict(
        shop_id=query.shop,
        category=query.category,
        level=query.level,
        operator=query.operator,
        keyword=query.keyword,
        listing_ids=list(query.products),
        channel=getattr(query, "channel", ""),
        mode=getattr(query, "mode", "product"),
        limit=params.get("limit", 10),
        offset=params.get("offset", 0),
    )


def _short_options(data):
    """短目录只下发轻量身份，不包含领域原行或经营快照。"""
    fields = (
        "value",
        "label",
        "listing_id",
        "product_id",
        "product_name",
        "shop_id",
        "category",
        "level",
        "operator",
    )
    return {
        key: (
            [
                {f: item[f] for f in fields if f in item} if isinstance(item, dict) else item
                for item in value[:25]
            ]
            if isinstance(value, list)
            else value
        )
        for key, value in data.items()
        if key in ("shops", "channels", "operators", "categories", "levels", "products", "total")
    }


def describe(engine, domain="products", mode="product") -> dict:
    """返回已发布域版本、真实可选日期、最多25条候选；不读取上游或运行同步。"""
    if domain not in ("products", "promotion") or mode not in ("product", "store"):
        raise BoardError("INVALID_SCOPE")
    snapshot = (
        product_snapshot(engine) if domain == "products" else promotion_snapshot(engine, mode)
    )
    with snapshot as repo:
        if repo.start is None or repo.end is None:
            raise BoardError("ASSISTANT_DATA_UNAVAILABLE", 503)
        if domain == "products":
            q = products.BoardQuery(max(repo.start, repo.end - timedelta(days=6)), repo.end)
            options = products.options(repo, q)
            versions = report_versions(repo, from_promotion=False)
        else:
            q = promotion.PromotionQuery(
                max(repo.start, repo.end - timedelta(days=6)), repo.end, mode=mode
            )
            options = promotion.options(repo, q)
            versions = (
                report_versions(repo, from_promotion=True)
                if mode == "product"
                else {"promotion_store": repo.token}
            )
        return wire(
            dict(
                domain=domain if domain == "products" else f"promotion_{mode}",
                mode=mode,
                data_version=repo.token,
                versions=versions,
                date_from=repo.start,
                date_to=repo.end,
                complete_through=None,
                shops=[dict(id=row["value"], name=row["label"]) for row in options["shops"][:25]],
                options=_short_options(options),
                capabilities=list(
                    PRODUCT_CAPABILITIES if domain == "products" else PROMOTION_CAPABILITIES
                ),
            )
        )


def _metric_facts(metrics):
    return [
        fact(
            key,
            metric["label"],
            metric["value"],
            metric["type"],
            status=metric.get("status", "observed"),
            reason=metric.get("observation_note") or metric.get("reason"),
        )
        for key, metric in metrics.items()
    ]


def _trend(points, definitions):
    """只投影领域已计算的数列，日期缺值保留空点；至多四条和最近25日。"""
    return [
        dict(
            key=key,
            label=label,
            unit=unit,
            points=[
                dict(
                    date=point["date"],
                    value=point["values"].get(key),
                    state=point.get("states", {}).get(key)
                    or (
                        "missing"
                        if point["values"].get(key) is None
                        else "partial"
                        if key in point.get("partial", ())
                        else "observed"
                    ),
                )
                for point in points[-25:]
            ],
        )
        for key, label, unit in definitions[:4]
    ]


def _comparison_table(metrics, *, product=False):
    columns = (
        ("label", "指标", "text"),
        ("unit", "数值单位", "text"),
        ("current", "本期", "text"),
        ("previous", "前一期", "text"),
        ("change", "环比", "percent"),
        ("status", "比较状态", "text"),
        ("reason", "比较说明", "text"),
    )
    if product:
        columns += (("year_value", "去年同期", "text"), ("yoy", "同比", "percent"))
    rows = []
    for metric in metrics.values():
        comparison = metric.get("mom_comparison", {})
        rows.append(
            dict(
                label=metric["label"],
                unit=metric["type"],
                current=metric["value"],
                previous=metric.get("previous_period" if product else "previous"),
                change=metric.get("mom" if product else "change"),
                status=comparison.get("reason")
                or metric.get("comparison_status")
                or metric.get("status"),
                reason=comparison.get("message") or metric.get("reason"),
                year_value=metric.get("previous"),
                yoy=metric.get("yoy"),
            )
        )
    return table("经营比较", columns, [_projection(row, columns) for row in rows], total=len(rows))


def _product_catalog(repo, q):
    data = products.catalog(repo, q)
    identities, _ = products.labels(repo.identity_rows(q.start, q.end), repo.profiles())
    # 目录以轻量身份键回联，不从展示名称/标签拆ID，也不合并同名商品。
    return [
        identities[row["value"]]
        for row in data["items"]
        if products.matches(identities[row["value"]], q)
    ]


def _product_risks(data):
    return [
        dict(
            row,
            priority=row["alert"]["priority"],
            risk=row["alert"]["label"],
            current=row["alert"]["current"],
            baseline=row["alert"]["baseline"],
            change=row["alert"]["change"],
            unit=row["alert"]["type"],
            estimated_impact=row["alert"]["expected_loss"],
            reasons="；".join(row["alert"]["reasons"]),
            action=row["alert"]["action"],
        )
        for row in data
    ]


def _read_products(repo, capability, params, limit, offset):
    q = _query(repo, params)
    scope, period = _scope(q, params), _period(q.start, q.end)
    facts, tables, notes, series = [], [], ["商品历史按当前资料归属；缺记录与空值不视为零。"], []
    if capability == "products_overview":
        calculate = (
            products.filtered_overview
            if any((q.shop, q.category, q.level, q.operator, q.products, q.keyword))
            else products.overview
        )
        data = calculate(repo, q)
        facts = _metric_facts(data["metrics"]) + [
            fact("product_count", "经营商品数", data["product_count"], "number"),
            fact("observed_rows", "商品日记录数", data["observed_rows"], "number"),
            fact(
                "health", "同款增长健康度", data["health"]["title"], reason=data["health"]["detail"]
            ),
            fact("diagnosis_action", "规则诊断建议", data["diagnosis"]["action"]),
        ]
        tables = [_comparison_table(data["metrics"], product=True)]
        brief = data["brief"]
        cycle_rows = [
            dict(
                days=entry["days"],
                start=entry["start"],
                end=entry["end"],
                previous_start=entry["previous_start"],
                previous_end=entry["previous_end"],
                label=label,
                unit=unit,
                current=entry["metrics"][key]["value"],
                previous=entry["metrics"][key]["previous"],
                change=entry["metrics"][key]["change"],
                reason=entry["metrics"][key]["message"],
            )
            for entry in data["windows"]
            for key, label, unit in products.METRICS
        ]
        columns = (
            ("days", "周期天数", "number"),
            ("start", "本期开始", "text"),
            ("end", "本期结束", "text"),
            ("previous_start", "前期开始", "text"),
            ("previous_end", "前期结束", "text"),
            ("label", "指标", "text"),
            ("unit", "单位", "text"),
            ("current", "本期值", "text"),
            ("previous", "前期值", "text"),
            ("change", "较前等长期间", "percent"),
            ("reason", "比较说明", "text"),
        )
        tables.append(_limited_table("经营周期表现", columns, cycle_rows, limit, offset))
        tables.append(
            _limited_table(
                "浏览到成交",
                (
                    ("label", "指标", "text"),
                    ("value", "数量", "number"),
                    ("status", "状态", "text"),
                ),
                data["funnel"],
                limit,
                offset,
            )
        )
        for key, title in (("products", "近7天热卖商品Top3"), ("rising", "近7天新品起势Top3")):
            columns = IDENTITY + (
                ("revenue", "近7日交易金额", "currency"),
                ("previous_revenue", "前7日交易金额", "currency"),
                ("growth", "较前7日", "percent"),
            )
            tables.append(_limited_table(title, columns, brief[key], limit, offset))
        tables.append(
            _limited_table(
                "近7天热卖品类Top3",
                (
                    ("category", "品类", "text"),
                    ("revenue", "近7日交易金额", "currency"),
                    ("growth", "较前7日", "percent"),
                ),
                brief["categories"],
                limit,
                offset,
            )
        )
        scope["ranking_period"] = _period(brief["start"], brief["end"])
        scope["comparison_periods"] = data["comparison_periods"]
        notes.extend(("榜单沿看板固定近7日及Top3范围，汇总指标使用所选经营期。", data["basis"]))
        notes.extend(item["text"] for item in brief["actions"]["items"])
        series = _trend(
            data["series"],
            (
                ("revenue", "交易金额", "currency"),
                ("visitors", "商品访客数", "number"),
                ("buyers", "支付买家数", "number"),
                ("conversion", "支付转化率", "percent"),
            ),
        )
        if len(data["series"]) > 25:
            notes.append("趋势仅返回最后25日；汇总覆盖所选完整期间。")
    elif capability == "products_risks":
        data = products.risk_feed(repo, q)
        columns = IDENTITY + (
            ("priority", "优先级", "text"),
            ("risk", "关注项", "text"),
            ("period_revenue", "经营期交易金额", "currency"),
            ("current", "当前值", "text"),
            ("baseline", "基准值", "text"),
            ("unit", "指标单位", "text"),
            ("change", "变化", "percent"),
            ("estimated_impact", "估算影响金额", "currency"),
            ("reasons", "排查方向", "text"),
            ("action", "建议动作", "text"),
        )
        tables = [
            _limited_table(
                "商品经营关注清单",
                columns,
                _product_risks(data["risks"]),
                limit,
                offset,
                total=data["total"],
            )
        ]
        count_labels = {
            "selected": "所选商品",
            "evaluated": "已评估",
            "unevaluated": "未评估",
            "excluded": "不适用",
            "flagged": "命中关注",
            "unflagged": "未命中",
        }
        facts = [
            fact(key, count_labels[key], value, "number") for key, value in data["counts"].items()
        ]
        facts.append(fact("status", "评估状态", data["status"]))
        scope.update(
            rule_version=data["rule_version"],
            rule_period=_period(q.end, q.end),
            baseline_period=data["baseline_period"],
            evaluation_date=data["evaluation_date"],
        )
        notes.append("关注清单按领域优先级和规则分值排序；影响金额为规则估算。")
    elif capability in ("products_scores", "products_days"):
        scoring = capability == "products_scores"
        data = _page(products.score_cards if scoring else products.days, repo, q, limit, offset)
        if scoring:
            columns = IDENTITY + (
                ("score", "得分", "number"),
                ("pending_weight", "待验证权重", "number"),
                ("decision_label", "放量判断", "text"),
                ("revenue", "近7日交易金额", "currency"),
                ("visitors", "近7日访客", "number"),
                ("ad_roi", "推广投产比", "ratio"),
                ("reason", "评分说明", "text"),
                ("action", "建议动作", "text"),
            )
            period = _period(data["start"], data["end"])
            scope["requested_period"] = _period(q.start, q.end)
            facts = [
                fact("total", "筛选新品数", data["total"], "number"),
                fact("total_count", "全部新品数", data["total_count"], "number"),
                fact("scored_count", "已计算款数", data["scored_count"], "number"),
                fact("status", "评分状态", data["status"]),
            ]
            notes.append("新品评分使用已发布评分截至日的独立近7日；筛选日期不改变评分窗口。")
            title = "新品评分与放量建议"
        else:
            columns, title = (("date", "日期", "text"),) + IDENTITY + PRODUCT_METRICS, "商品日明细"
        tables = [
            table(
                title,
                columns,
                [_projection(item, columns) for item in data["items"]],
                total=data["total"],
                offset=offset,
            )
        ]
        if scoring and len(q.products) == 1 and data["items"]:
            signals = data["items"][0]["signals"]
            cols = (
                ("label", "评分指标", "text"),
                ("type", "单位", "text"),
                ("value", "当前值", "text"),
                ("weight", "权重", "number"),
                ("points", "得分", "number"),
                ("status", "状态", "text"),
                ("reason", "说明", "text"),
            )
            tables.append(_limited_table("单款评分依据", cols, signals, 25, 0))
    else:
        items = _product_catalog(repo, q)
        tables = [_limited_table("商品目录", IDENTITY, items, limit, offset)]
    return report(
        "products",
        capability,
        "商品数据",
        "商品数据看板已发布版本",
        repo.token,
        period=period,
        scope=scope,
        facts=facts,
        tables=tables,
        notes=notes,
        series=series,
        versions=report_versions(repo, from_promotion=False),
    )


def _diagnosis_rows(items):
    rows = []
    for item in items:
        row = dict(
            item,
            findings="；".join(f["rule"] + "：" + f["evidence"] for f in item["findings"]),
            actions="；".join(dict.fromkeys(f["action"] for f in item["findings"])),
            clues="；".join(
                clue["label"] + "：" + promotion_rules.clue_text(clue) for clue in item["clues"]
            ),
            notes="；".join(item["notes"]),
        )
        row.update({key: metric["value"] for key, metric in item["current"].items()})
        rows.append(row)
    return rows


def _read_promotion(repo, capability, params, limit, offset):
    q = _query(repo, params, mode=repo.mode)
    scope, period = _scope(q, params), _period(q.start, q.end)
    facts, tables, notes, series = (
        [],
        [],
        ["推广沿来源支付日；归因成交与商品经营成交分别统计。"],
        [],
    )
    if capability == "promotion_overview":
        data = promotion.overview(repo, q)
        metrics = {**data["metrics"], **data["net"]}
        facts = _metric_facts(metrics) + [
            fact("current_record_count", "本期推广记录数", data["current_record_count"], "number"),
            fact("missing_count", "缺记录对象日数", data["coverage"]["missing_count"], "number"),
        ]
        scope["comparison_windows"] = data["windows"]
        tables = [_comparison_table(metrics)]
        if repo.mode == "product":
            tables.append(
                _limited_table(
                    "投产比最低的5天",
                    (
                        ("date", "日期", "text"),
                        ("roi", "投产比", "ratio"),
                        ("spend", "花费", "currency"),
                        ("revenue", "归因成交额", "currency"),
                    ),
                    data["top5"],
                    limit,
                    offset,
                )
            )
            tables.append(
                _limited_table(
                    "推广漏斗",
                    (
                        ("label", "指标", "text"),
                        ("value", "数量", "number"),
                        ("status", "状态", "text"),
                    ),
                    data["funnel"],
                    limit,
                    offset,
                )
            )
        if data["channel_summaries"]:
            rows = [
                dict(
                    channel=item["channel"],
                    label=item["label"],
                    **{
                        key: value["value"]
                        for key, value in {**item["metrics"], **item["net"]}.items()
                    },
                )
                for item in data["channel_summaries"]
            ]
            tables.append(
                _limited_table(
                    "店铺推广渠道",
                    (("channel", "渠道标识", "text"), ("label", "渠道", "text")) + AD_METRICS,
                    rows,
                    limit,
                    offset,
                )
            )
        series = _trend(
            [
                dict(
                    date=point["date"],
                    values={key: metric["value"] for key, metric in point["metrics"].items()},
                    states={key: metric["status"] for key, metric in point["metrics"].items()},
                )
                for point in data["series"]
            ],
            tuple((key, *SPECS[key]) for key in ("spend", "revenue", "roi", "clicks")),
        )
        if len(data["series"]) > 25:
            notes.append("趋势仅返回最后25日；汇总覆盖所选完整期间。")
    elif capability == "promotion_days":
        data = _page(promotion.days, repo, q, limit, offset)
        columns = (
            (("date", "日期", "text"),)
            + (
                (
                    ("shop_id", "店铺ID", "text"),
                    ("shop_name", "店铺", "text"),
                    ("channel", "渠道", "text"),
                    ("source_label", "来源", "text"),
                )
                if repo.mode == "store"
                else ()
            )
            + AD_METRICS
        )
        tables = [
            table(
                "推广日明细",
                columns,
                [_projection(item, columns) for item in data["items"]],
                total=data["total"],
                offset=offset,
            )
        ]
        notes.append(
            "明细粒度：店铺×渠道×日。"
            if repo.mode == "store"
            else "明细按日汇总当前筛选商品及其全部推广方式。"
        )
    elif capability in ("promotion_diagnosis", "promotion_objects"):
        data = promotion_rules.diagnosis(
            repo, q, detail="summary" if capability == "promotion_diagnosis" else "full"
        )
        current, previous = data["current_window"], data["previous_window"]
        scope.update(
            rule_period=_period(current["start"], current["end"]),
            baseline_period=_period(previous["start"], previous["end"]),
            requested_period=period,
            rule_version=data["rule_version"],
        )
        period = scope["rule_period"]
        facts = [fact("status", "诊断状态", data["status"])]
        labels = {
            "products": "商品数",
            "flagged": "关注商品数",
            "focused": "重点商品数",
            "fully_assessed": "完整评估商品数",
            "limited": "评估受限商品数",
        }
        facts.extend(
            fact(key, label, data["counts"][key], "number")
            for key, label in labels.items()
            if key in data["counts"]
        )
        notes.append("诊断独立使用截至已收到日且不晚于昨日的相邻两个7日，起止见规则期间。")
        if data["overall"]:
            if capability == "promotion_diagnosis":
                facts.extend(_metric_facts(data["overall"]["current"]))
                columns = (
                    ("level", "优先级", "text"),
                    ("rule", "命中规则", "text"),
                    ("evidence", "依据", "text"),
                    ("action", "建议动作", "text"),
                )
                tables = [
                    _limited_table(
                        "推广规则诊断", columns, data["overall"]["findings"], limit, offset
                    ),
                    _comparison_table(data["overall"]["current"]),
                    _limited_table(
                        "规则可判断情况",
                        (
                            ("label", "规则", "text"),
                            ("state", "判断状态", "text"),
                            ("reason", "说明", "text"),
                        ),
                        data["overall"]["rule_results"],
                        limit,
                        offset,
                    ),
                    _limited_table(
                        "CTR/CPC/转化线索",
                        (
                            ("label", "线索", "text"),
                            ("evidence", "变化依据", "text"),
                            ("action", "核查方向", "text"),
                        ),
                        [
                            dict(clue, evidence=promotion_rules.clue_text(clue))
                            for clue in data["overall"]["clues"]
                        ],
                        limit,
                        offset,
                    ),
                ]
                notes.extend(data["overall"]["notes"])
            else:
                group = params.get("view", params.get("group", "category"))
                group = {
                    "category": "categories",
                    "product": "products",
                    "shop": "shops",
                    "flagged": "flagged_products",
                    "focused": "focused_products",
                }.get(group, group)
                if group not in promotion_rules.DIAGNOSIS_GROUPS:
                    raise BoardError("INVALID_SCOPE")
                scope["view"] = group
                columns = (
                    IDENTITY
                    + (
                        ("label", "对象", "text"),
                        ("priority", "优先级", "text"),
                        ("evaluation_state", "评估状态", "text"),
                        ("current_days", "本期有记录天数", "number"),
                        ("previous_days", "前期有记录天数", "number"),
                    )
                    + AD_METRICS
                    + (
                        ("findings", "命中依据", "text"),
                        ("actions", "建议动作", "text"),
                        ("clues", "CTR/CPC/转化线索", "text"),
                        ("notes", "限制说明", "text"),
                    )
                )
                tables = [
                    _limited_table(
                        "推广诊断对象", columns, _diagnosis_rows(data[group]), limit, offset
                    )
                ]
        else:
            tables = [table("推广诊断对象", IDENTITY, [], total=0, offset=offset)]
            notes.append(data.get("status_reason", "暂无可评估的推广对象。"))
    else:
        data = promotion.options(repo, q, catalog=True)
        # 目录API会清空筛选以供级联；在同一轻量目录上应用原领域匹配规则。
        identities = promotion.window_labels(repo, q.start, q.end) if repo.mode == "product" else {}
        items = [
            identities[item["listing_id"]]
            for item in data["products"]
            if promotion.matches(identities[item["listing_id"]], q)
        ]
        tables = [_limited_table("推广商品目录", IDENTITY, items, limit, offset)]
        if repo.mode == "store":
            tables.append(
                _limited_table(
                    "推广渠道目录",
                    (("value", "渠道标识", "text"), ("label", "渠道", "text")),
                    data["channels"],
                    limit,
                    offset,
                )
            )
    return report(
        f"promotion_{repo.mode}",
        capability,
        "推广分析",
        "推广分析看板已发布版本",
        repo.token,
        period=period,
        scope=scope,
        facts=facts,
        tables=tables,
        notes=notes,
        series=series,
        versions=report_versions(repo, from_promotion=True)
        if repo.mode == "product"
        else {"promotion_store": repo.token},
    )


def read(engine, capability, params: dict) -> dict:
    """按调用方固定版本读取完整筛选汇总；返回有界清单，不暴露SQL/写库/上游工具。"""
    limit, offset = _validate(params, multi_dimensions=capability in PRODUCT_CAPABILITIES)
    if capability in PRODUCT_CAPABILITIES:
        with product_snapshot(engine, params["data_version"]) as repo:
            return _read_products(repo, capability, params, limit, offset)
    if capability in PROMOTION_CAPABILITIES:
        mode = params.get("mode", "product")
        if mode not in ("store", "product"):
            raise BoardError("INVALID_SCOPE")
        with promotion_snapshot(engine, mode, params["data_version"]) as repo:
            return _read_promotion(repo, capability, params, limit, offset)
    raise BoardError("UNKNOWN_ASSISTANT_CAPABILITY")
