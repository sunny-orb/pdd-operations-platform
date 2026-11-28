"""日经营来源的有界读取单位；行数预算不定义历史保留量。"""

from datetime import timedelta

READ_WINDOW_DAYS = 7


def read_spans(start, end):
    """将已批准的闭区间拆为最多七日的闭区间，不扩大来源范围。"""
    while start <= end:
        stop = min(start + timedelta(days=READ_WINDOW_DAYS - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)
