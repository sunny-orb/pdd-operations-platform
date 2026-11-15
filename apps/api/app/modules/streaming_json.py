"""按历史规范JSON逐段编码；输入仍由领域校验，不构造整份字符串/UTF-8副本。"""

import hashlib
import io
import json


def canonical_chunks(value, *, default=str):
    """保持dumps排序/Unicode/类型转换字节一致，额外缓冲不随行数增长。"""
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=False, default=default, separators=(",", ":")
    )
    buffer = io.StringIO()
    for token in encoder.iterencode(value):
        buffer.write(token)
        if buffer.tell() >= 64 * 1024:
            yield buffer.getvalue().encode()
            buffer.seek(0)
            buffer.truncate(0)
    if buffer.tell():
        yield buffer.getvalue().encode()


def fingerprint(value, *, default=str):
    digest = hashlib.sha256()
    for chunk in canonical_chunks(value, default=default):
        digest.update(chunk)
    return digest.hexdigest()


def fingerprint_rows(rows):
    """有序行迭代器按历史JSON数组计算指纹，不物化数据库游标。"""
    digest = hashlib.sha256(b"[")
    separator = b""
    for row in rows:
        digest.update(separator)
        for chunk in canonical_chunks(row):
            digest.update(chunk)
        separator = b","
    digest.update(b"]")
    return digest.hexdigest()
