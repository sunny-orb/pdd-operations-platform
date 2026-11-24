"""日更新时效：后台直接核对上游昨日业务日；网页更新时间单独留作来源变化证据。"""

from datetime import date, datetime, timedelta
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


def _local_stamp(value):
    """仅接受真实带时区核对时刻，统一按北京时间比较。"""
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(stamp, datetime) and stamp.utcoffset() is not None:
            return stamp.astimezone(ZONE)
    except (ValueError, OverflowError):
        pass
    return None


def direct_day_check(checkpoint, now):
    """成功检查时实际查询的业务日必须是核对当天的昨天，跨零点旧查询不采信。"""
    stamp = _local_stamp(checkpoint.get("observed_at"))
    day = checkpoint.get("observation", {}).get("upstream_day")
    if (
        checkpoint.get("check_status") == "succeeded"
        and stamp is not None
        and stamp <= now
        and isinstance(day, dict)
        and isinstance(day.get("present"), bool)
    ):
        required = stamp.date() - timedelta(days=1)
        if day.get("date") == required.isoformat():
            return required, day["present"], stamp
    return None


def _saved_stamps(saved, day):
    entry = saved.get(day.isoformat(), {}) if isinstance(saved, dict) else {}
    if not isinstance(entry, dict):
        return {}
    stamps = {}
    for key in ("first_seen_at", "last_absent_at"):
        stamp = _local_stamp(entry.get(key))
        if stamp and stamp.date() == day + timedelta(days=1):
            stamps[key] = stamp
    return stamps


def merge_daily_arrivals(saved, check):
    """按应到业务日保留首次见数和此前最近一次未见时刻，最多保留连续两日。"""
    required, present, stamp = check
    # 重放较早证据也只合并其所属日，不能让保留窗口倒退。
    newest = required
    for key in saved if isinstance(saved, dict) else ():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if day > newest and _saved_stamps(saved, day):
            newest = day
    result = {}
    for day in (newest - timedelta(days=1), newest):
        stamps = _saved_stamps(saved, day)
        if day == required:
            if present:
                stamps["first_seen_at"] = min(stamps.get("first_seen_at", stamp), stamp)
            elif "first_seen_at" not in stamps or stamp < stamps["first_seen_at"]:
                stamps["last_absent_at"] = max(stamps.get("last_absent_at", stamp), stamp)
        if "last_absent_at" in stamps and "first_seen_at" in stamps:
            if stamps["last_absent_at"] >= stamps["first_seen_at"]:
                del stamps["last_absent_at"]
        if stamps:
            result[day.isoformat()] = {key: value.isoformat() for key, value in stamps.items()}
    return result


def source_timeliness(source, checkpoint, now):
    """按昨日首次见到前一业务日记录的钟点加一小时，与今日同源核对证据比较。"""
    if source.kind != "daily":
        return {"state": "not_applicable"}
    required = now.astimezone(ZONE).date() - timedelta(days=1)
    saved = checkpoint.get("platform_snapshot", {}).get("daily_arrivals", {})
    current = _saved_stamps(saved, required)
    first = current.get("first_seen_at")
    absent = current.get("last_absent_at")
    baseline = _saved_stamps(saved, required - timedelta(days=1)).get("first_seen_at")
    result = {"basis": "upstream_business_day", "required_date": required.isoformat()}
    deadline = baseline + timedelta(days=1) + GRACE if baseline else None
    if deadline:
        result.update(baseline_first_seen_at=baseline.isoformat(), expected_by=deadline.isoformat())
    check = direct_day_check(checkpoint, now)
    if check and check[0] != required:
        check = None
    latest_attempt = _local_stamp(checkpoint.get("checked_at"))
    if absent and absent <= now and (latest_attempt is None or absent > latest_attempt):
        # 周对账实读业务日时，普通检查仍保留自己的最近尝试时刻。
        check = required, False, absent
    if check:
        result["observed_at"] = check[2].isoformat()
    if first and first <= now:
        result.update(state="updated", first_seen_at=first.isoformat(), arrival_timing="unknown")
        if deadline:
            if first <= deadline:
                result["arrival_timing"] = "within_deadline"
            elif absent and deadline < absent < first:
                result.update(arrival_timing="confirmed_late", last_absent_at=absent.isoformat())
            else:
                result["arrival_timing"] = "first_seen_after_deadline"
        return result
    if check and check[1]:
        # 升级前的单次观测可以证实已有记录，但无法还原当日最早一次。
        return {**result, "state": "updated", "arrival_timing": "unknown"}
    if not deadline:
        return {**result, "state": "unknown"}
    if now <= deadline:
        return {**result, "state": "waiting"}
    if check and check[2] > deadline:
        return {**result, "state": "delayed"}
    return {**result, "state": "awaiting_check"}
