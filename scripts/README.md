# 脚本入口

从项目根目录运行。安装、参数示例和安全边界统一见[开发与运行](../docs/development.md)，不要逐个执行所有脚本。

| 分组         | 入口                                                         | 用途                                                                                                                                          |
| ------------ | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| 应用         | `api.mjs`                                                    | npm 调用的跨平台 Python 适配器                                                                                                                |
| 本地环境     | `db-local.py`                                                | 启动/查看容器、执行现行测试；不执行旧迁移                                                                                                     |
| v2 基线      | `db-v2.py`                                                   | 隔离验证及初始空库创建；已有库不覆盖                                                                                                          |
| 字典审阅     | `render-db-schema.py`                                        | 从base到当前head生成完整空库SQL；npm run db:schema生成、check自动核对                                                                         |
| 公司初始化   | `init-deployment-db.py [--apply]`                            | 默认只读检查，显式apply初始化公司public并保留monitor，见[部署方案](../docs/implementation/deployment.md)                                      |
| 公司增量升级 | `upgrade-deployment-db.py [--apply]`                         | 批准起点12/16/17→18，默认只读，维护窗口内显式执行，见[部署方案](../docs/implementation/deployment.md)                                         |
| 生产复用策略 | `storage-policy.py [preflight/enable/disable]`               | 容器内固定范围入口；宿主统一调用`deploy/storage.py`，默认只读，见[启用与停用](../docs/implementation/deployment.md#production-storage-policy) |
| 项目同步     | `sync-project.py`、`install-sync-schedule.py`                | 同一执行链的状态、补跑、钟点及 launchd 安装                                                                                                   |
| 数仓网页采集 | `configure-warehouse-browser.py --login / --check`           | 专用浏览器飞书登录、无界面8表网页时间验证；不启用调度、不接收经营数据                                                                         |
| 历史推广补齐 | `import-promotion-history.py --input <私有JSON> [--publish]` | 默认只校验11条授权范围，显式发布走本机受控身份及全局锁                                                                                        |
| 销售独立接收 | `sync-sales.py`、`sync-sales-sources.py`                     | 现行销售来源 CLI 适配                                                                                                                         |
| 受控升级     | `upgrade-*-local.py`                                         | 既有库逐项升级；按对应设计、版本和备份条件选择                                                                                                |
| 资料关系重建 | `rebuild-product-profiles.py --publish`                      | 固定本地来源重建商品关联及相容评分，不接上游                                                                                                  |
| 业务核验     | `verify-*-local.py`、`verify-overview-local.mjs`             | 模块归档、PG、API 核对，依赖必要私有资料                                                                                                      |

脚本只做环境、参数和运行适配；业务编排在 `apps/api/app/jobs`，规则在 `app/modules`。项目调度调用 `app.jobs.project_runtime.execute_project`，新增平台适配不复制业务同步逻辑。旧 M1 的 `db:upgrade`、`profiles:m1`、`scores:m1` 已移除。

`sync-project.py --check-updates`检查到期的实际来源，发现变化后复用原接收流程；`--scheduled`按当前来源策略处理常规接收、变化检查与周对账，共用PG互斥锁。`--status`包含监测开关／间隔；部分失败非零退出。本版本要求数据库结构达到`20261114_18`，详见[开发说明](../docs/development.md#同步与评分)和[来源监测设计](../docs/implementation/project-sync-implementation.md#source-update-monitor)。

2026-11-02授权店铺推广同期补齐后，两推广来源的完整接收从2025-02-01开始，其他销售来源仍从2026-02-01开始。优先用`sync-project.py --run-now --only shop_channel_product_promotion shop_channel_star_shop`完成两来源受控更新。独立CLI指定推广来源发布时使用2025-02-01起点；`--dataset all --publish`保留原财年参数，各来源内部使用各自获准完整范围。验证模式仍只读明确请求窗口，不隐式扩展。

- `benchmark-api-local.py --output var/audits/performance-20261031/after.json --repeat 5`：本机只读29组看板接口性能测量，不触发同步；固定销售比较窗2026-10-01～10-29，其他模块按实际默认月窗。
