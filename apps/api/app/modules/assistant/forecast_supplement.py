"""复合预测的补充正文。只引用本轮其他报告内的指标及明细。"""

import re
from decimal import Decimal


def _match(label, question):
    label, question = label.casefold(), question.casefold()
    if label and label in question:
        return len(label) + 1
    return sum(pair in question for pair in {label[i : i + 2] for i in range(len(label) - 1)})


def _value(value, unit):
    if value is None:
        return "暂无可核验值"
    if unit == "percent":
        return f"{Decimal(str(value)) * 100:.2f}%"
    if unit in {"currency", "ratio"}:
        number = f"{Decimal(str(value)):.2f}"
        return f"{number} {'元' if unit == 'currency' else '倍'}"
    return str(value)


def _table_excerpt(tables, question):
    candidates = []
    for table in tables:
        columns = [col for col in table["columns"] if col["key"] not in {"id", "data_version"}]
        matches = [col for col in columns if _match(col["label"], question)]
        score = _match(table["title"], question) + sum(
            _match(c["label"], question) for c in matches
        )
        if not score or not table["rows"]:
            continue
        # 把实际对象与同一行的指标放在一起；首行只表示查询返回的明细。
        identity = next(
            (
                col
                for col in columns
                if col["key"]
                in {
                    "name",
                    "title",
                    "shop_name",
                    "product_name",
                    "style_code",
                    "label",
                    "category",
                }
            ),
            None,
        )
        chosen = ([identity] if identity else []) + (matches or columns)
        fields = {col["key"]: col for col in chosen}
        row = table["rows"][0]
        cells = [
            f"{col['label']} {_value(row.get(key), col['unit'])}"
            for key, col in list(fields.items())[:3]
        ]
        if cells:
            candidates.append((score, f"{table['title']} · " + "，".join(cells)))
    return max(candidates, key=lambda item: item[0])[1] if candidates else ""


def supplement_text(reports, question, gaps):
    """保留补充域自己的时段与缺项，预测数值只在调用方的固定答案中出现。"""
    lines = []
    for report in reports:
        title = report.get("title") or (
            "商品经营" if report["kind"] == "product_performance" else "商品推广"
        )
        facts = report.get("facts", report.get("metrics", ()))
        ranked = sorted(facts, key=lambda item: _match(item["label"], question), reverse=True)
        relevant = [item for item in ranked if _match(item["label"], question)]
        evidence = []
        for item in relevant[:3]:
            value = item.get("value", item.get("current"))
            result = _value(value, item["unit"])
            if item.get("reason"):
                result += f"（{item['reason']}）"
            evidence.append(f"{item['label']} {result}")
        if not evidence or re.search(r"商品|哪款|单款|款号|店铺|排行|明细|原因|天气", question):
            excerpt = _table_excerpt(report.get("tables", ()), question)
            if excerpt:
                evidence.append(excerpt)
        if not evidence and _match(title, question):
            evidence.append("本次报告未提供可直接引用的对应指标")
        if evidence:
            period = report.get("period") or {}
            if period.get("date_from") and period.get("date_to"):
                title += f"（{period['date_from']} 至 {period['date_to']}）"
            lines.append(f"{title}，{'，'.join(evidence)}。")
            gap = next(
                (
                    note
                    for note in report.get("notes", ())
                    if re.search(r"缺|未收|未齐|不完整|暂无|尚未|无法|不足", note)
                ),
                None,
            )
            if gap:
                lines.append(gap)
    lines.extend(message for message in gaps if message)
    return "\n\n".join(dict.fromkeys(lines)) or "补充问题暂未取得可核验的数据。"
