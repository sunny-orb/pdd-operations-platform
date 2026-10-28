"""天气专用任务入口；本模块不启动调度、不在导入时接收或入库。"""

from app.connectors.weather import receive_weather
from app.modules.weather.storage import publish


def sync_weather(engine, receive, archive_root):
    """受控调用：真实PG引擎、无参接收函数、私有归档目录→发布结果。"""
    return publish(engine, receive, archive_root)


def run_weather(engine, archive_root):
    """独立调度步骤；只读两份官方文档，失败交由调度预算处理。"""
    return sync_weather(engine, receive_weather, archive_root)
