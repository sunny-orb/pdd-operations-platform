"""状态只读接口：不注册同步写入口，不加载上游连接器。"""

from pathlib import Path

from fastapi import APIRouter, Request

from app.jobs.project_sync import read_schedule
from app.modules.data_status.repository import read_status

router = APIRouter(prefix="/api/v1/data-status", tags=["数据更新状态"])


@router.get("")
def get_data_status(request: Request):
    policy = read_schedule(Path(__file__).resolve().parents[4] / "config/sync.toml")
    return read_status(
        request.app.state.engine,
        interval_seconds=policy.monitor_interval_seconds,
        enabled=policy.enabled and policy.monitor_enabled,
        source_schedule=policy,
    )
