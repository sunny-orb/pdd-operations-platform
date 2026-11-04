"""数仓可见网页读取边界；浏览器测试使用拦截的合成网页，不访问真实来源。"""

import json
import os

import pytest

TABLE = "ods_rpa_pdd_traffic_kanban_overview_f"
URL = "https://bi.kktree.cn/#/data_source?tableId=cAc3IVT4i3"


def text(time="2026-11-03 10:02:10"):
    return f"基础信息\n{TABLE}\n数据更新时间\n{time}\n当前任务状态\n-"


def test_visible_time_identity_and_date():
    from app.connectors.warehouse_page import parse_page

    result = parse_page("shop_daily", URL, text())
    assert result == dict(
        basis="warehouse_web_page", table_id="cAc3IVT4i3", updated_at="2026-11-03 10:02:10"
    )


@pytest.mark.parametrize(
    "url,body",
    [
        (URL.replace("cAc3IVT4i3", "wrong"), text()),
        (URL.replace("https://bi.kktree.cn", "https://example.org"), text()),
        (URL, text().replace(TABLE, "different_table")),
        (URL, text("2026-02-30 10:02:10")),
        (URL, text("")),
        (URL, text() + "\n数据更新时间\n2026-11-04 09:00:00"),
    ],
)
def test_rejects_wrong_table_missing_conflicting_or_invalid_time(url, body):
    from app.connectors.warehouse_page import parse_page

    with pytest.raises(ValueError):
        parse_page("shop_daily", url, body)


def test_login_redirect_and_scope_are_distinct():
    from app.connectors.warehouse_page import WarehousePageError, parse_page

    with pytest.raises(WarehousePageError, match="WAREHOUSE_WEB_LOGIN_REQUIRED"):
        parse_page("shop_daily", "https://bi.kktree.cn/#/login", "飞书登录")
    with pytest.raises(WarehousePageError, match="WAREHOUSE_WEB_SCOPE"):
        parse_page("jst_returns", URL, text())


def test_missing_or_public_state_does_not_launch_browser(tmp_path):
    from app.connectors.warehouse_page import WarehousePageReader

    path = tmp_path / "state.json"
    with WarehousePageReader(path) as reader:
        with pytest.raises(ValueError, match="WAREHOUSE_WEB_LOGIN_REQUIRED"):
            reader.start()
        assert reader.context is None
    path.write_text("{}")
    path.chmod(0o644)
    with WarehousePageReader(path) as reader:
        with pytest.raises(ValueError, match="WAREHOUSE_WEB_STATE_PERMISSIONS"):
            reader.start()
        assert reader.context is None


@pytest.fixture
def browser_reader(tmp_path):
    from app.connectors.warehouse_page import WarehousePageReader

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"cookies": [], "origins": []}))
    state.chmod(0o600)
    with WarehousePageReader(state) as reader:
        reader.start()
        yield reader
    assert reader.context is None


def test_real_browser_reload_and_domain_only_saved_state(browser_reader):
    reader = browser_reader
    updated = ["2026-11-03 10:02:10"]
    calls = []

    def route(request):
        calls.append(request.request.url)
        request.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body="<body>" + text(updated[0]).replace("\n", "<br>") + "</body>",
        )

    reader.context.route("**/*", route)
    reader.context.add_cookies(
        [
            dict(name="session", value="synthetic-test", domain="bi.kktree.cn", path="/"),
            dict(name="unrelated", value="excluded", domain="passport.feishu.cn", path="/"),
        ]
    )
    assert reader.read("shop_daily")["updated_at"] == updated[0]
    updated[0] = "2026-11-04 09:01:00"
    assert reader.read("shop_daily")["updated_at"] == updated[0]
    assert len(calls) == 2  # 同一URL也实际刷新，不复用旧页面时间
    saved = json.loads(reader.state_path.read_text())
    assert [x["name"] for x in saved["cookies"]] == ["session"]
    assert os.stat(reader.state_path).st_mode & 0o077 == 0


def test_login_expiry_is_not_retried_eight_times_and_old_state_survives(browser_reader):
    reader = browser_reader
    original = reader.state_path.read_bytes()
    calls = []

    def route(request):
        calls.append(request.request.url)
        request.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body='<script>location.hash="/login"</script><body>飞书登录</body>',
        )

    reader.context.route("**/*", route)
    for code in ("shop_daily", "product_daily"):
        with pytest.raises(ValueError, match="WAREHOUSE_WEB_LOGIN_REQUIRED"):
            reader.read(code)
    assert len(calls) == 1
    assert reader.state_path.read_bytes() == original


def test_profile_state_lock_prevents_second_reader(browser_reader):
    from app.connectors.warehouse_page import WarehousePageReader

    with WarehousePageReader(browser_reader.state_path) as other:
        with pytest.raises(ValueError, match="WAREHOUSE_WEB_IN_USE"):
            other.start()
        assert other.context is None


def test_cross_table_hash_navigation_does_not_accept_previous_detail(browser_reader):
    second_table = "bwd_rpa_pdd_product_detail_f"
    calls = []
    html = f"""<meta charset="utf-8"><body>{TABLE}<br>{second_table}<br>
      数据更新时间<br><span id="time"></span>
      <script>document.querySelector('#time').textContent =
        location.hash.includes('pvKk7pwVwt') ? '2026-11-04 10:40:30' : '2026-11-03 10:02:10';
      </script></body>"""

    def route(request):
        calls.append(request.request.url)
        request.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    browser_reader.context.route("**/*", route)
    assert browser_reader.read("shop_daily")["updated_at"] == "2026-11-03 10:02:10"
    assert browser_reader.read("product_daily")["updated_at"] == "2026-11-04 10:40:30"
    assert len(calls) == 2


def test_waits_for_async_time_after_detail_label(browser_reader):
    html = f"""<meta charset="utf-8"><body>{TABLE}<br>数据更新时间<br><span id="time">-</span>
      <script>setTimeout(() => document.querySelector('#time').textContent =
        '2026-11-04 10:02:10', 600);</script></body>"""
    browser_reader.context.route(
        "**/*",
        lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html),
    )
    assert browser_reader.read("shop_daily")["updated_at"] == "2026-11-04 10:02:10"
