# 独立任务入口

任务与 API 独立，业务规则由 `app/modules` 拥有；来源读取由 `app/connectors` 拥有。API 启动和页面刷新不触发上游读取。

| 入口                     | 职责                               |
| ------------------------ | ---------------------------------- |
| `project_sync.py`        | 时间槽、跨进程互斥、去重和补跑策略 |
| `project_runtime.py`     | 装配整条执行链，供本机与平台共用   |
| `sync_product_board.py`  | 现行商品三来源接收与资料关系发布   |
| `score_product_board.py` | 只读相容本地输入，发布试行评分     |
| `sync_shop_daily.py`     | 店铺日来源接收与受管运行连接       |
| `sync_sales_sources.py`  | 销售续段来源接收                   |
| `sync_weather.py`        | 天气文档解析与独立归档发布         |

项目执行链还按依赖调用售后报告等领域服务；输入失败不推进有效版本或伪造评分/报告成功。操作命令统一见[开发与运行](../../../../docs/development.md)，平台接口见[接管说明](../../../../docs/implementation/sync-platform-handoff.md)。旧 `sync_m1`、`resolve_profiles`、`score_m1` 已移除。
