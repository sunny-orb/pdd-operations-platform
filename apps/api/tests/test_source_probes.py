"""连接器边界测试：固定来源、限定时间/店铺、修订与元信息不冒充完整日。"""

from datetime import date


def test_probe_sql_is_scoped_and_uses_only_known_columns():
    from app.connectors.source_probes import warehouse_query

    query, params = warehouse_query("shop_daily", date(2026, 11, 2))
    assert "MAX(updated_at)" in query
    assert "dt>=%s AND dt<%s" in query
    assert "shop_id IN" in query
    assert date(2026, 11, 3) in params
    query, params = warehouse_query("jst_sales", date(2026, 11, 2))
    assert "channel=%s" in query and "order_status IN (%s,%s)" in query
    assert "已发货" in params and "取消" in params
    assert "MAX(updated_at)" not in query


def test_runtime_groups_only_refresh_dependencies_that_changed():
    from app.jobs.source_monitor_runtime import selected_steps

    assert selected_steps(("shop_regions",)) == ("shop_regions",)
    assert selected_steps(("products", "weather")) == ("products", "product_scores", "weather")
    assert selected_steps(("aftersales",)) == (
        "jst_returns",
        "jst_shipped_sales",
        "jst_cancelled_sales",
        "refund_targets",
        "aftersales_report",
    )
