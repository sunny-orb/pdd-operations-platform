# 脚本入口

从项目根目录运行。安装、参数示例和安全边界统一见[开发与运行](../docs/development.md)，不要逐个执行所有脚本。

| 分组         | 入口                                             | 用途                                           |
| ------------ | ------------------------------------------------ | ---------------------------------------------- |
| 应用         | `api.mjs`                                        | npm 调用的跨平台 Python 适配器                 |
| 本地环境     | `db-local.py`                                    | 启动/查看容器、执行现行测试；不执行旧迁移      |
| v2 基线      | `db-v2.py`                                       | 隔离验证及初始空库创建；已有库不覆盖           |
| 字典审阅     | `render-db-schema.py`                            | 从冻结基线生成或核对派生 SQL                   |
| 项目同步     | `sync-project.py`、`install-sync-schedule.py`    | 同一执行链的状态、补跑、钟点及 launchd 安装    |
| 销售独立接收 | `sync-sales.py`、`sync-sales-sources.py`         | 现行销售来源 CLI 适配                          |
| 受控升级     | `upgrade-*-local.py`                             | 既有库逐项升级；按对应设计、版本和备份条件选择 |
| 资料关系重建 | `rebuild-product-profiles.py --publish`          | 固定本地来源重建商品关联及相容评分，不接上游   |
| 业务核验     | `verify-*-local.py`、`verify-overview-local.mjs` | 模块归档、PG、API 核对，依赖必要私有资料       |

脚本只做环境、参数和运行适配；业务编排在 `apps/api/app/jobs`，规则在 `app/modules`。项目调度调用 `app.jobs.project_runtime.execute_project`，新增平台适配不复制业务同步逻辑。旧 M1 的 `db:upgrade`、`profiles:m1`、`scores:m1` 已移除。
