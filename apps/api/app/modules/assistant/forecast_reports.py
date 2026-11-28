"""销售预测的看板报告。运营正文、模型引用和展开后的数据依据来自同一计算结果。"""

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import ProgrammingError

from app.modules.knowledge.forecast_rules import REPORT_ORDER
from app.modules.knowledge.passages import operational_terms, outside_source, select_passages
from app.modules.product_board.repository import BoardError

from .business import fact, report, table
from .forecast_answers import catalogue_question, revision_question

CAPABILITIES = ("sales_forecast", "business_knowledge")


def money(value):
    value = Decimal(value) / 10000
    return f"{value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP):,.2f} 万元"


def percent(value):
    return f"{(Decimal(value) * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP):f}%"


def citation(source):
    status = "（飞书连接暂未恢复，使用上次核验版本）" if source["state"] == "stale" else ""
    return f"[查看预测 SOP]({source['source_url']}){status}"


def format_forecast(result, source, coverage):
    month = f"{result['month'].year} 年 {result['month'].month} 月"
    cutoff = result["cutoff"]
    if not cutoff:
        return f"{month}六店尚无从月初起连续完整的日报，月末 GMV 待更新。\n\n{citation(source)}"
    texts = {}
    texts[REPORT_ORDER[0]] = (
        f"截至 **{cutoff:%Y年%m月%d日}**，拼多多六店累计 GMV **{money(result['actual'])}**"
        f"，覆盖 {result['complete_days']} 个完整销售日。"
    )
    target = result["target"]
    if target is None:
        texts[REPORT_ORDER[1]] = "当月渠道目标暂缺可核验的唯一值，达成率待定。"
    else:
        progress = f"目标 {money(target)}"
        if result["achievement_rate"] is not None:
            progress += f"，已完成 {percent(result['achievement_rate'])}"
        if result.get("needed_daily") is not None and result["gap"] > 0:
            progress += (
                f"，剩余 {result['remaining_days']} 天平均每天需卖 {money(result['needed_daily'])}"
            )
        elif result["gap"] < 0:
            progress += f"，已超过目标 {money(-result['gap'])}"
        elif result["status"] == "closed" and result["gap"] > 0:
            progress += f"，收官时距目标 {money(result['gap'])}"
        texts[REPORT_ORDER[1]] = progress + "。"

    if result["status"] == "closed":
        texts[REPORT_ORDER[2]] = f"本月已收官，实际 GMV **{money(result['actual'])}**。"
    elif result["center"] is None:
        texts[REPORT_ORDER[2]] = "完整销售日还不足以覆盖公式的两个窗口，暂缓给出月末中心估计。"
    else:
        forecast = f"**月末中心估计 {money(result['center'])}**"
        if result["forecast_achievement_rate"] is not None:
            forecast += f"，预计达成目标 {percent(result['forecast_achievement_rate'])}"
        scenarios = []
        for key, label in (("conservative", "保守"), ("baseline", "基准"), ("optimistic", "乐观")):
            scenario = result["scenarios"].get(key)
            if scenario and scenario["low"] is not None and scenario["high"] is not None:
                scenarios.append(f"{label} {money(scenario['low'])}～{money(scenario['high'])}")
        texts[REPORT_ORDER[2]] = (
            forecast + "。" + ("情景区间为" + "、".join(scenarios) + "。" if scenarios else "")
        )
    comparison = result.get("comparison") or {}
    change = comparison.get("center_change")
    if change is None:
        texts[REPORT_ORDER[3]] = "暂无可比的上次预测快照。"
    elif not change:
        texts[REPORT_ORDER[3]] = "与上次预测中心相同。"
    else:
        texts[REPORT_ORDER[3]] = f"较上次{'上调' if change > 0 else '下调'} {money(abs(change))}。"

    observations = []
    for w in result["windows"][:2]:
        if w.get("change_rate") is not None:
            value = w["change_rate"]
            direction = "下降" if value < 0 else "增长"
            observations.append(f"{w['label']}的日均销售额较前期{direction} {percent(abs(value))}")
    if not result["scenarios"].get("conservative") and result["status"] == "forecast":
        observations.append("活动可比窗口待核实，扩展情景暂缺")
    gaps = coverage.get("sales_gaps", [])
    if gaps:
        observations.append(f"{gaps[0]['date']} 的六店日报尚未齐全")
    if coverage.get("promotion_state") == "basis_unverified":
        observations.append("推广口径尚未对齐，投放影响待核实")
    if coverage.get("sales_source_state") == "failed_retained":
        observations.append("销售日报使用上次成功同步的数据")
    if coverage.get("target_state") == "failed_retained":
        observations.append("目标使用上次成功同步的版本")
    texts[REPORT_ORDER[4]] = (
        "。".join(observations) + "。" if observations else "现有依据尚未量化其他经营因素的影响。"
    )
    order = result.get("report_order") or list(REPORT_ORDER)
    return "\n\n".join([*(texts[key] for key in order if key in texts), citation(source)])


def _center_clauses(source):
    """引用已通过校验的公式；原文另行定义加权日均时一并给出权重。"""
    evidence = (source.get("rule") or {}).get("evidence", {})
    center = evidence.get("center")
    if not center:
        return []
    return [
        center,
        *(
            clause
            for key, clause in evidence.items()
            if key.startswith("weight_definition") and clause != center
        ),
    ]


def _source_unavailable(source):
    """新版不能计算时，正文展示本版的冲突原句；权限和时效失效时只给状态。"""
    state = source["state"]
    if state == "unsupported" and source.get("content"):
        issues = source.get("issues") or []
        if issues:
            issue = issues[0]
            quotes = [
                f"「{c['section']}」写的是“{c['quote']}”" for c in issue.get("citations", [])[:2]
            ]
            lines = ["这版 SOP 暂停出具月末预测。" + issue["message"], *quotes, citation(source)]
            return "\n\n".join(lines)
        return f"新版 SOP 含当前暂未支持的规则，月末估计待更新。\n\n{citation(source)}"
    if state == "pending" and source.get("revision") is not None:
        return "这次未能核验最新版预测 SOP，暂时无法给出正式金额。"
    return {
        "unavailable": "预测 SOP 原文当前不可访问，已暂停引用。",
        "expired": "本次未能确认飞书的当前版本，暂时无法引用。",
    }.get(state, "本次未能核对预测 SOP，暂时无法引用。")


def _knowledge(source, query):
    """返回本题答案和实际引用的章节名；未匹配原文时章节为 None。"""
    if outside_source(query):
        return "当前公共知识中仅接入六店月度 GMV 预测 SOP，未接入提到的这篇文档。", None
    if revision_question(query):
        return f"这次引用的飞书修订号是 {source['revision']}。\n\n{citation(source)}", []
    if catalogue_question(query):
        verified = source.get("verified_at") if "更新" in query else None
        at = (
            f"。最近核验于 {verified.astimezone(ZoneInfo('Asia/Shanghai')):%m月%d日 %H:%M}"
            if verified
            else ""
        )
        return f"当前已接入《{source['title']}》{at}。\n\n{citation(source)}", []
    if source.get("issues") and (
        re.search(r"冲突|矛盾|暂停|权重|公式|怎么算|计算方法", query)
        or ("预测" in query and not operational_terms(query))
    ):
        return _source_unavailable(source), None
    if re.search(
        r"权重|中心|怎么预测|如何预测|预测.{0,8}(?:怎么算|公式|依据)|模型|^公式", query, re.I
    ):
        clauses = _center_clauses(source)
        if clauses:
            formula = "。".join(dict.fromkeys(clause["quote"].rstrip("。") for clause in clauses))
            return f"六店合计按以下原文计算。\n\n{formula}。\n\n{citation(source)}", [
                clause["section"] for clause in clauses
            ]
    sections = select_passages(source["content"] or "", query)
    if not sections:
        if source["state"] == "unsupported":
            return f"新版预测 SOP 已同步，这项规则暂无法识别。\n\n{citation(source)}", None
        return "当前公共知识中没有找到这项 SOP 的对应章节。", None
    if operational_terms(query) and not re.search(r"权重|公式|算例|示例", query):
        excerpts = list(dict.fromkeys(body.split("\n\n", 1)[0][:600] for _, body in sections))
        matched = [title for title, _ in sections]
        return "\n\n".join([*excerpts, citation(source)]), matched
    heading, body = sections[0]
    paragraphs = re.split(r"\n\s*\n", body)
    selected = paragraphs[0]
    if len(paragraphs) > 1 and re.search(r"情景|区间|乐观|保守|基准", query):
        selected = next((p for p in paragraphs if "情景" in p), selected)
    if len(paragraphs) > 1 and re.search(r"达成|差额|目标", query):
        selected = next((p for p in paragraphs if "达成率" in p), selected)
    return f"{selected}\n\n{citation(source)}", [heading]


def _window_table(result):
    return table(
        "预测所用销售窗口",
        [
            ("label", "期间", "text"),
            ("start", "起始日", "text"),
            ("end", "截止日", "text"),
            ("sample_days", "完整天数", "number"),
            ("gmv", "GMV", "currency"),
            ("daily_average", "日均 GMV", "currency"),
            ("change_rate", "较前期日均", "percent"),
        ],
        [
            {
                key: window.get(key)
                for key in (
                    "label",
                    "start",
                    "end",
                    "sample_days",
                    "gmv",
                    "daily_average",
                    "change_rate",
                )
            }
            for window in result["windows"]
        ],
    )


def read(engine, capability, params):
    from app.modules.knowledge import forecast_access

    from .chat import Clarify

    if engine is None:
        raise BoardError("ASSISTANT_DATA_UNAVAILABLE", 503)
    verified = forecast_access.ensure_forecast_current(engine)
    usage = verified.get("review_usage") or {}
    try:
        result = _read_verified(engine, capability, params, verified)
    except Clarify as exc:
        exc.review_usage = usage
        raise
    return dict(result, review_usage=usage)


def _read_verified(engine, capability, params, verified):
    from app.modules.knowledge.interpretation import effective_forecast_sop
    from app.modules.knowledge.monthly_forecast import evaluate

    method = capability == "business_knowledge"
    domain, title = ("definitions", "公共预测 SOP") if method else ("sales", "六店月末 GMV 预测")
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            source = verified
            if source["state"] in {"active", "unsupported"}:
                # 取数前已远程核对；在经营快照里只接受同一次核对的修订。
                current = effective_forecast_sop(connection)
                if (
                    any(current[k] != verified[k] for k in ("document_id", "revision", "state"))
                    or current["verified_at"] is None
                    or current["checked_at"] != current["verified_at"]
                    or current["verified_at"] < verified["verified_at"]
                ):
                    raise BoardError("DATA_VERSION_CHANGED", 409)
                source = current
            revision = str(source["revision"]) if source["revision"] is not None else None
            base = dict(
                domain=domain,
                capability=capability,
                title=title,
                source=source["title"],
                version=revision,
                versions={"forecast_sop": revision},
            )
            if source["state"] != "active" and not (
                method and source["state"] == "unsupported" and source["content"]
            ):
                return dict(
                    report(
                        **base,
                        scope={key: params.get(key) for key in ("month", "cutoff")}
                        if not method
                        else {},
                    ),
                    answer=_source_unavailable(source),
                    forecast_status="unavailable",
                )
            if method:
                answer, headings = _knowledge(source, params["keyword"])
                if (
                    source["state"] == "unsupported"
                    and headings is not None
                    and not operational_terms(params["keyword"])
                    and re.search(r"公式|权重|预测|区间|计算", params["keyword"])
                ):
                    answer += "\n\n这是当前原文，其中的新规则尚不能计算月末金额。"
                notes = ["原文章节 · " + "、".join(dict.fromkeys(headings))] if headings else []
                return dict(
                    report(**base, notes=notes),
                    answer=answer,
                    forecast_status="method" if headings is not None else "unavailable",
                )
            month = date.fromisoformat(params["month"] + "-01")
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            cutoff = params.get("cutoff")
            as_of = cutoff + timedelta(days=1) if cutoff else today
            result, inputs, version = evaluate(connection, source, month, as_of)
            if weights := params.get("scenario_weights"):
                return _weight_scenario(
                    base, source, month, cutoff, weights, result, inputs, version
                )
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01":
            raise BoardError("ASSISTANT_DATA_UNAVAILABLE", 503) from exc
        raise
    coverage = inputs["coverage"]
    result_facts = [
        fact(
            key,
            name,
            result.get(key),
            "currency",
            status="estimated" if key == "center" else "observed",
        )
        for key, name in (
            ("actual", "累计 GMV"),
            ("target", "渠道月目标"),
            ("center", "月末中心估计"),
            ("needed_daily", "达标所需日均"),
        )
        if result.get(key) is not None
    ]
    base["version"] = version
    base["versions"] = {**base["versions"], "forecast_inputs": version}
    base["source"] = "六店销售日报、渠道月目标"
    extra_notes = []
    if result["status"] == "forecast" and result["center"] is not None:
        extra_notes.extend(clause["quote"] for clause in _center_clauses(source))
    if cutoff:
        extra_notes.append("按当前已发布的日报和生效 SOP 回算到指定日期，历史补数会反映在结果中。")
    if result["status"] == "forecast":
        for key, title in (("center_daily", "预测采用的剩余日均"), ("gap", "距月目标差额")):
            if result.get(key) is not None:
                result_facts.append(fact(key, title, result[key], "currency"))
    evidence = report(
        **base,
        period={"date_from": month, "date_to": result["cutoff"]} if result["cutoff"] else None,
        scope={"month": params["month"], "cutoff": cutoff},
        facts=result_facts,
        tables=[_window_table(result)],
        notes=[
            n for n in [*extra_notes, *result.get("notes", []), coverage.get("promotion_note")] if n
        ],
    )
    return dict(
        evidence, answer=format_forecast(result, source, coverage), forecast_status=result["status"]
    )


def _weight_scenario(base, source, month, cutoff, weights, official, inputs, version):
    """只替换本次调用的中心权重，在同一份输入上比较；读取路径不写快照。"""
    from app.modules.knowledge.forecast_calculation import calculate_forecast

    from .chat import Clarify

    terms = source["rule"]["terms"]
    if {n for n, _ in weights} != {t["days"] for t in terms}:
        windows = "、".join(f"近{t['days']}日" for t in terms)
        raise Clarify(f"这版 SOP 可对 {windows} 的权重做情景试算，请给出这两个窗口的比例。")
    if official["status"] != "forecast":
        raise Clarify("权重试算需要尚未收官的月份，以及足够覆盖两个窗口的完整六店日报。")
    rule = {**source["rule"], "terms": [{"days": n, "weight": w} for n, w in weights]}
    simulation = calculate_forecast(
        rule, month=month, cutoff=inputs["cutoff"], daily=inputs["daily"], target=inputs["target"]
    )
    assumptions = "、".join(f"近{n}日占{(Decimal(w) * 100).normalize():f}%" for n, w in weights)
    evidence = report(
        **{
            **base,
            "title": "六店 GMV 权重试算",
            "source": "六店销售日报与本次指定权重",
            "version": version,
            "versions": {**base["versions"], "forecast_inputs": version},
        },
        period={"date_from": month, "date_to": inputs["cutoff"]},
        scope={"month": month.strftime("%Y-%m"), "cutoff": cutoff},
        facts=[
            fact(
                "scenario_center", "情景试算", simulation["center"], "currency", status="estimated"
            ),
            fact("official_center", "正式预测", official["center"], "currency", status="estimated"),
        ],
        notes=[assumptions],
    )
    return dict(
        evidence,
        forecast_status="scenario",
        answer=(
            f"**情景试算 {money(simulation['center'])}**。截至 {inputs['cutoff']:%m月%d日}，"
            f"六店按{assumptions}测算。同批数据的正式预测为 **{money(official['center'])}**。"
            f"\n\n{citation(source)}"
        ),
    )
