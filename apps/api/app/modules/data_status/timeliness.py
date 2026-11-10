"""日更新时效：只消费核对过的网页时间，不推断经营完整性或同步成败。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")
FORMAT = "%Y-%m-%d %H:%M:%S"
RULE = "previous-day-clock-plus-one-hour-v1"
GRACE = timedelta(hours=1)


def _evidence(raw, checked, now):
    """网页钟点按平台日历比较，保留原文；无时区核对/未来/非法时间不作依据。"""
    try:
        updated = datetime.strptime(raw, FORMAT)
        if updated.strftime(FORMAT) != raw:
            return None
        checked = datetime.fromisoformat(checked) if isinstance(checked, str) else checked
        if checked is None or checked.utcoffset() is None or checked > now:
            return None
        if updated > checked.astimezone(ZONE).replace(tzinfo=None):
            return None
        return {"updated_at": raw, "checked_at": checked.isoformat()}
    except (ValueError, TypeError, AttributeError):
        return None


def _days(checkpoint, now):
    saved = checkpoint.get("platform_snapshot", {}).get("update_timing", {})
    candidates = list(saved.get("days", {}).values()) if saved.get("rule") == RULE else []
    candidates.append(
        {
            "updated_at": checkpoint.get("web_updated_at"),
            "checked_at": checkpoint.get("web_checked_at"),
        }
    )
    today = now.astimezone(ZONE).date()
    dates = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    days = {}
    for candidate in candidates:
        entry = _evidence(candidate.get("updated_at"), candidate.get("checked_at"), now)
        if entry:
            day = entry["updated_at"][:10]
            if day in dates and entry["updated_at"] >= days.get(day, {}).get("updated_at", ""):
                days[day] = entry
    return days


def merge_update_timing(checkpoint, updated, checked):
    """同一来源的原/新证据合并为最多两天；调用者在原互斥锁、事务内保存。"""
    days = _days(checkpoint, checked)
    entry = _evidence(updated, checked, checked)
    if entry:
        day = updated[:10]
        today = checked.astimezone(ZONE).date()
        if day in {today.isoformat(), (today - timedelta(days=1)).isoformat()}:
            if updated >= days.get(day, {}).get("updated_at", ""):
                days[day] = entry
    return {"rule": RULE, "days": days}


def update_timeliness(source, checkpoint, now):
    """严格超一小时且有期限后成功核对才判延迟；没有昨日依据不推定期限。"""
    if source.kind != "daily":
        return {"state": "not_applicable"}
    local_now = now.astimezone(ZONE).replace(tzinfo=None)
    today = local_now.date()
    days = _days(checkpoint, now)
    baseline = days.get((today - timedelta(days=1)).isoformat())
    current = _evidence(checkpoint.get("web_updated_at"), checkpoint.get("web_checked_at"), now)
    result = {"state": "unknown"}
    if current:
        result["observed_at"] = current["checked_at"]
    deadline = None
    if baseline:
        deadline = datetime.strptime(baseline["updated_at"], FORMAT) + timedelta(days=1) + GRACE
        result.update(
            baseline_updated_at=baseline["updated_at"], expected_by=deadline.strftime(FORMAT)
        )
    confirmed = days.get(today.isoformat())
    if confirmed:
        return {
            **result,
            "state": "updated",
            "observed_at": confirmed["checked_at"],
            "confirmed_updated_at": confirmed["updated_at"],
        }
    if deadline is None or current is None:
        return result
    if local_now <= deadline:
        return {**result, "state": "waiting"}
    observed = datetime.fromisoformat(current["checked_at"]).astimezone(ZONE).replace(tzinfo=None)
    return {**result, "state": "delayed" if observed > deadline else "awaiting_check"}
