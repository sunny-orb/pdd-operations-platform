"""业务模块变更必须推进来源producer或查询定义，避免同版本缓存旧语义。"""


def test_amount_module_change_invalidates_query_definition(monkeypatch):
    from pathlib import Path

    from app.modules.aftersales.repository import revision

    before = revision()
    read = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: read(path) + (b"\nchanged" if path.name == "amounts.py" else b""),
    )
    assert revision() != before


def test_bundle_module_change_invalidates_source_producer(monkeypatch):
    from pathlib import Path

    from app.modules.aftersales.publisher import producer

    before = producer()
    read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path: read(path) + ("\nchanged" if path.name == "bundle_categories.py" else ""),
    )
    assert producer() != before
