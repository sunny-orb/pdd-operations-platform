"""资料ID和逐字段共识的纯规则；不访问来源或数据库。"""

import re
from datetime import datetime

FIELDS = ("product_name", "category", "level", "operator", "listed_date")


def parse_ids(value):
    """只接受整串ASCII数字及已核分隔符，返回去重ID；不从混合文本猜取。"""
    if not isinstance(value, str):
        return []
    value = value.strip()
    if not re.fullmatch(r"[0-9]+(?:\s*[\n/;；]\s*[0-9]+)*", value):
        return []
    return sorted(set(re.split(r"\s*[\n/;；]\s*", value)))


def resolve_fields(records):
    """对同K候选逐字段求唯一非空值；日期有非法候选时保留无效状态。"""
    result = dict(field_statuses={}, field_candidates={}, field_evidence={})
    for field in FIELDS:
        source_field = "source_listed_date" if field == "listed_date" else field
        values = sorted(
            {
                str(r[source_field]).strip()
                for r in records
                if r.get(source_field) and str(r[source_field]).strip()
            }
        )
        result["field_candidates"][field] = values
        result["field_evidence"][field] = sorted(
            {r["source_profile_id"] for r in records if r.get(source_field)}
        )
        invalid = False
        if field == "listed_date":
            parsed = []
            for value in values:
                try:
                    parsed.append(datetime.fromisoformat(value).date())
                except ValueError:
                    invalid = True
            values = sorted(set(parsed))
        state = (
            "invalid"
            if invalid
            else ("missing" if not values else "matched" if len(values) == 1 else "conflict")
        )
        result[field] = values[0] if state == "matched" else None
        result["field_statuses"][field] = state
    states = set(result["field_statuses"].values())
    result["profile_match_status"] = (
        "conflict"
        if states.intersection({"conflict", "invalid"})
        else "matched"
        if states == {"matched"}
        else "unmatched"
        if states == {"missing"}
        else "partial"
    )
    result["field_evidence"]["style_codes"] = sorted(
        {
            r["source_style_code"].strip()
            for r in records
            if r.get("source_style_code") and r["source_style_code"].strip()
        }
    )
    return result
