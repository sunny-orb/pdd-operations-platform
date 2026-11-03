const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** 仅格式化含明确时区的合法ISO时刻；无时区或不能识别的来源文字原样保留。 */
export function weatherTime(value: string) {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(
    value,
  );
  if (!match) return value;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const zone = match[3];
  const offset =
    zone === "Z"
      ? 0
      : (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4))) * (zone[0] === "+" ? 1 : -1);
  // 拒绝Date对2月30日等非法日期的自动归一化，避免展示推定日期。
  if (
    new Date(date.getTime() + offset * 60000).toISOString().slice(0, 19) !==
    `${match[1]}:${match[2] ?? "00"}`
  )
    return value;
  const p = Object.fromEntries(
    formatter.formatToParts(date).map((part) => [part.type, part.value]),
  );
  return `${p.year}年${p.month}月${p.day}日${p.hour}时${p.minute === "00" ? "" : `${p.minute}分`}${p.second === "00" ? "" : `${p.second}秒`}`;
}
