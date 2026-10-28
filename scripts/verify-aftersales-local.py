"""M5本地只读独立SQL/API核验；固定报告词典/目标，不调用被测分类函数或接上游。"""

import json
import sys
import time
from decimal import Decimal, localcontext
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from migrations_v2.local import local_url

SIZE_LABELS = {
    "larger": "尺码偏大",
    "smaller": "尺码偏小",
    "unspecified": "尺码不合适未说明",
}
QUALITY_LABELS = {
    "quality": "质量问题/污渍等",
    "workmanship": "做工瑕疵",
    "damage": "破损/破洞/开裂",
}
METRIC_KEYS = {
    "total_refund_rate": "total_rate",
    "aftersale_refund_rate": "aftersale_rate",
}


def decimal(value):
    return Decimal(value) if value is not None else None


def equal_number(actual, expected):
    assert decimal(actual) == expected, (actual, str(expected))


def ready_branch(package, code):
    branches = [b for b in (package or {}).get("branches", []) if b.get("code") == code]
    return len(branches) == 1 and all(
        (
            branches[0].get("enabled") is True,
            branches[0].get("confirmation_status") == "confirmed",
            branches[0].get("validation_status") == "verified_in_scope",
            bool(branches[0].get("evidence_ref")),
        )
    )


def fixed_dictionary(package, code, branch):
    """仅取本报告FK包中已启用的原原因词典，绝不加载当前Python默认规则。"""
    dictionaries = [
        d
        for d in (package or {}).get("dictionaries", [])
        if d.get("dictionary_code") == code
    ]
    if not ready_branch(package, branch) or len(dictionaries) != 1:
        return None
    value = dictionaries[0]
    if not (
        value.get("enabled") is True
        and value.get("validation_status") == "verified_in_scope"
        and value.get("field_codes") == ["source_return_reason"]
        and value.get("entries")
        and value.get("evidence_ref")
        and value.get("match_mode") in ("literal_substring", "exact_text")
    ):
        return None
    return value


def issue_reference_from_groups(groups, size_ready, quality_ready):
    """消费SQL的原原因组和独立匹配集合；标签可重叠，任一命中的组Q仅入总量一次。"""
    count = sum(g["rows"] for g in groups)
    quantities_complete = count == sum(g["nonnull_q"] for g in groups)
    text_count = sum(
        g["rows"]
        for g in groups
        if isinstance(g["reason"], str) and g["reason"].strip()
    )

    def qsum(selected):
        if not selected or any(g["nonnull_q"] != g["rows"] for g in selected):
            return None
        return sum((g["qty"] for g in selected), Decimal(0))

    denominator = qsum(groups)
    usable = quantities_complete and text_count > 0

    def observed(selected, active):
        if not usable or not active:
            return None
        return qsum(selected) if selected else Decimal(0)

    size_groups = {k: [] for k in SIZE_LABELS}
    quality_groups = {k: [] for k in QUALITY_LABELS}
    for group in groups:
        codes = set(group["size"])
        if codes:
            code = next(iter(codes)) if len(codes) == 1 else "unspecified"
            size_groups[code].append(group)
        for code in set(group["quality"]):
            quality_groups[code].append(group)
    return {
        "size_qty": observed([g for g in groups if g["size"]], size_ready),
        "quality_qty": observed([g for g in groups if g["quality"]], quality_ready),
        "size_rows": {
            label: observed(size_groups[code], size_ready)
            for code, label in SIZE_LABELS.items()
        },
        "quality_rows": {
            label: qsum(quality_groups[code])
            for code, label in QUALITY_LABELS.items()
            if quality_groups[code]
        },
        "denominator": denominator,
        "text_rows": text_count,
        "missing_reason_rows": count - text_count,
    }


def sql_issue_reference(c, ref, package):
    size = fixed_dictionary(package, "size_issue", "size_dictionary")
    quality = fixed_dictionary(package, "quality_issue", "quality_dictionary")

    def matched_array(code):
        return f"""ARRAY(SELECT DISTINCT entry->>'output_code'
          FROM jsonb_array_elements(CAST(:{code}_entries AS jsonb)) AS entry
          WHERE ((:{code}_mode='exact_text' AND g.reason=entry->>'term')
             OR (:{code}_mode='literal_substring' AND strpos(g.reason,entry->>'term')>0))
          AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(entry->'negative_terms') AS n(term)
                          WHERE strpos(g.reason,n.term)>0)) AS {code}"""

    rows = (
        c.execute(
            sa.text(
                """WITH g AS (SELECT source_return_reason AS reason,COUNT(*) AS rows,
            COUNT(actual_returned_qty) AS nonnull_q,SUM(actual_returned_qty) AS qty
            FROM return_records WHERE dataset_version_id=:v AND row_kind='detail'
            AND period_start>=:s AND period_end<=:e
            AND period_start IS NOT NULL AND period_end IS NOT NULL
            GROUP BY source_return_reason) SELECT g.*,"""
                + matched_array("size")
                + ","
                + matched_array("quality")
                + " FROM g"
            ),
            {
                "v": ref["input_version_id"],
                "s": ref["window_start"],
                "e": ref["window_end"],
                "size_entries": json.dumps(
                    size["entries"] if size else [], ensure_ascii=False
                ),
                "size_mode": size["match_mode"] if size else "literal_substring",
                "quality_entries": json.dumps(
                    quality["entries"] if quality else [], ensure_ascii=False
                ),
                "quality_mode": quality["match_mode"]
                if quality
                else "literal_substring",
            },
        )
        .mappings()
        .all()
    )
    return issue_reference_from_groups(
        rows, bool(size), bool(quality) and ready_branch(package, "quality_quantity")
    )


def verify_target_ranges(c, refs, package, overview):
    """逐项核报告绑定的真实版本/目标ID/两端界，不以当前目标指针替换历史。"""
    actual = overview["target_ranges"]
    ref = refs.get("targets")
    if not ref or not ready_branch(package, "target_application"):
        assert actual == []
        return {}
    stored = (
        c.execute(
            sa.text("""SELECT t.id,t.metric_code,t.lower_rate,t.upper_rate,t.target_rate,
        t.scope_kind,t.scope_status,t.application_policy_code
        FROM refund_targets t JOIN dataset_versions v ON v.id=t.dataset_version_id
        WHERE t.dataset_version_id=:v AND v.dataset_code='refund_targets' AND v.state='published'
        ORDER BY t.metric_code"""),
            {"v": ref["input_version_id"]},
        )
        .mappings()
        .all()
    )
    assert len(stored) == len(actual) == 2
    indexed = {r["metric_code"]: r for r in actual}
    assert set(indexed) == set(METRIC_KEYS)
    values = {m["key"]: m["value"] for m in overview["metrics"]}
    for row in stored:
        item = indexed[row["metric_code"]]
        assert item["target_id"] == row["id"]
        assert item["version_id"] == ref["input_version_id"]
        assert row["target_rate"] is None and row["scope_kind"] == "all"
        assert row["scope_status"] == "complete"
        assert row["application_policy_code"] == "rolling_30d_and_natural_month"
        lower, upper = row["lower_rate"], row["upper_rate"]
        assert Decimal(0) <= lower <= upper <= Decimal(1)
        equal_number(item["lower_rate"], lower)
        equal_number(item["upper_rate"], upper)
        value = decimal(values[METRIC_KEYS[row["metric_code"]]])
        equal_number(item["current"], value)
        equal_number(
            item["lower_gap_pp"], (value - lower) * 100 if value is not None else None
        )
        equal_number(
            item["upper_gap_pp"], (value - upper) * 100 if value is not None else None
        )
        assert item["state"] == (
            "metric_unavailable"
            if value is None
            else "below_range"
            if value < lower
            else "above_range"
            if value > upper
            else "within_range"
        )
    return indexed


def main():
    engine = sa.create_engine(local_url("pdd_operations"), hide_parameters=True)
    opener = build_opener(ProxyHandler({}))
    timings = []

    def get(path, **q):
        started = time.monotonic()
        with opener.open(
            "http://127.0.0.1:8000/api/v1/aftersales/" + path + "?" + urlencode(q),
            timeout=90,
        ) as response:
            result = json.load(response)
        timings.append(round(time.monotonic() - started, 3))
        return result

    try:
        with engine.connect() as c, c.begin(), localcontext() as ctx:
            ctx.prec = 50
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            before = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
            meta = get("meta")["data"]
            assert meta["status"] == "partial" and meta["observed_to"] is not None
            opt = get("options", data_version=meta["data_version"])["data"]
            assert opt["reports"]
            assert meta["observed_to"] == max(r["period_end"] for r in opt["reports"])
            logical = {
                (r["report_type"], r["period_start"], r["period_end"])
                for r in opt["reports"]
            }
            assert len(logical) == len(opt["reports"])
            groups, classification_reports, target_reports = 0, 0, 0
            for report in opt["reports"]:
                query = {
                    "report_id": report["id"],
                    "data_version": meta["data_version"],
                }
                overview = get("overview", **query)["data"]
                observed = {m["key"]: m["value"] for m in overview["metrics"]}
                role = (
                    c.execute(
                        sa.text("""SELECT i.input_role,i.input_version_id,i.window_start,i.window_end
                    FROM version_inputs i JOIN aftersales_reports r
                    ON r.dataset_version_id=i.output_version_id WHERE r.id=:r"""),
                        {"r": report["id"]},
                    )
                    .mappings()
                    .all()
                )
                refs = {x["input_role"]: x for x in role}
                package = c.execute(
                    sa.text("""SELECT s.parameters FROM aftersales_reports r
                    JOIN dataset_versions v ON v.id=r.dataset_version_id
                    LEFT JOIN rule_sets s ON s.id=v.rule_set_id
                    WHERE r.id=:r AND (s.id IS NULL OR s.domain='aftersales' AND s.schema_revision='v1')"""),
                    {"r": report["id"]},
                ).scalar_one()
                r = refs["j1_returns"]
                actual = c.execute(
                    sa.text("""SELECT count(*),sum(actual_returned_qty),count(actual_returned_qty),
                    sum(actual_inbound_amount) FROM return_records WHERE dataset_version_id=:v
                    AND row_kind='detail' AND period_start>=:s AND period_end<=:e"""),
                    {
                        "v": r["input_version_id"],
                        "s": r["window_start"],
                        "e": r["window_end"],
                    },
                ).one()
                assert observed["records"] == str(actual[0])
                if actual[0]:
                    equal_number(observed["returned_qty"], actual[1])
                    assert actual[0] == actual[2]
                else:
                    assert observed["returned_qty"] is None
                assert observed["inbound_amount"] is None and actual[3] is None
                r = refs["j2_shipped_sales"]
                nulls = c.execute(
                    sa.text("""SELECT count(*) FILTER (WHERE sales_amount IS NULL)
                    FROM product_sales_records WHERE dataset_version_id=:v AND row_kind='detail'
                    AND period_start>=:s AND period_end<=:e"""),
                    {
                        "v": r["input_version_id"],
                        "s": r["window_start"],
                        "e": r["window_end"],
                    },
                ).scalar_one()
                if nulls:
                    assert observed["gmv"] is None and observed["presale_rate"] is None
                assert (
                    observed["aftersale_rate"] is None
                    and observed["total_rate"] is None
                )
                reference = sql_issue_reference(c, refs["j1_returns"], package)
                for key in ("size_qty", "quality_qty"):
                    equal_number(observed[key], reference[key])
                for item in overview["size_breakdown"]:
                    value = reference["size_rows"][item["name"]]
                    equal_number(item["qty"], value)
                    denominator = reference["denominator"]
                    equal_number(
                        item["share"],
                        value / denominator
                        if value is not None and denominator
                        else None,
                    )
                classification_reports += reference["quality_qty"] is not None
                targets = verify_target_ranges(c, refs, package, overview)
                target_reports += bool(targets)
                for view, columns in [
                    ("category", 12),
                    ("style", 13),
                    ("mom", 7),
                    ("yoy", 7),
                    ("quality", 3),
                    ("history", 10),
                ]:
                    body = get("analysis", view=view, **query)["data"]
                    assert len(body["columns"]) == columns
                    if view == "quality":
                        actual_labels = {
                            row["cells"]["name"]["value"]: row["cells"]
                            for row in body["rows"]
                        }
                        assert set(actual_labels) == set(reference["quality_rows"])
                        for name, value in reference["quality_rows"].items():
                            equal_number(actual_labels[name]["qty"]["value"], value)
                            denominator = reference["denominator"]
                            equal_number(
                                actual_labels[name]["share"]["value"],
                                value / denominator
                                if value is not None and denominator
                                else None,
                            )
                    if body["total"] > 25:
                        second = get("analysis", view=view, page=2, **query)["data"]
                        assert not (
                            {x["id"] for x in body["rows"]}
                            & {x["id"] for x in second["rows"]}
                        )
                    groups += 1
            default = get("overview", data_version=meta["data_version"])["data"]
            assert len(default["trend"]) == min(8, len(logical))
            assert get("analysis", view="history", data_version=meta["data_version"])[
                "data"
            ]["total"] == len(logical)
            default_targets = {
                row["metric_code"]: row for row in default["target_ranges"]
            }
            assert set(default_targets) == set(METRIC_KEYS)
            # 仅核本轮默认新报告的已确认值；历史报告始终按各自FK的SQL原值核验。
            for code, lower, upper in [
                ("total_refund_rate", "0.40", "0.43"),
                ("aftersale_refund_rate", "0.32", "0.36"),
            ]:
                equal_number(default_targets[code]["lower_rate"], Decimal(lower))
                equal_number(default_targets[code]["upper_rate"], Decimal(upper))
            after = c.execute(sa.text("SELECT count(*) FROM task_runs")).scalar_one()
            assert before == after
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "reports": len(logical),
                        "analysis_groups": groups,
                        "classification_reports": classification_reports,
                        "target_reports": target_reports,
                        "source_date": meta["observed_to"],
                        "http_calls": len(timings),
                        "max_seconds": max(timings),
                        "get_created_tasks": after - before,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
