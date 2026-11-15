"""同步失败的可公开类别；未知错误不透传原始文本或查询信息。"""

SYNC_FAILURE_CODES = frozenset(
    {
        "SYNC_FAILED",
        "ARCHIVE_CAPACITY",
        "INCREMENTAL_BASELINE_MISMATCH",
        "WAREHOUSE_CONCURRENCY_LIMIT",
    }
)


def safe_failure_code(code):
    """仅接受白名单字符串，供任务结果和状态仓储共享同一分类边界。"""
    return code if isinstance(code, str) and code in SYNC_FAILURE_CODES else "SYNC_FAILED"
