# 同步功能的平台接管与回退

本文件说明已实现的迁移接口及接管步骤，不表示平台已经接入或部署。全局职责见architecture，M5金额/分类定义见data-contract，验证记录见validation。平台负责触发和提供运行资源；连接器、校验、归档、版本发布及评分/报告继续使用同一应用模块。

## 稳定入口

```python
from pathlib import Path
from app.jobs.project_runtime import ProjectPaths, execute_project

result = execute_project(
    engine,  # 调用方创建/释放，使用原有最小运行权限
    batch_id,  # 同一次平台任务的重试必须沿用同一非空批次标识
    paths=ProjectPaths(
        ingestion_root=Path("/approved/runtime/ingestion"),
        run_archive_root=Path("/approved/runtime/project-sync"),
        scoring_archive_root=Path("/approved/runtime/product-scoring"),
        refund_targets_config=Path("/approved/config/aftersales-targets.toml"),
        feishu_root=Path("/approved/feishu-runtime"),
    ),
    warehouse_password=load_approved_warehouse_secret,  # 调用方提供零参延迟函数
    only=None,
    max_attempts=2,
)
```

示例中的engine、batch_id和取密函数由平台适配器提供；不把口令放在参数日志或任务输出中。五类路径必须为绝对路径且可独立挂载；评分归档显式传递，不再落回源码树。入口不读取本机sources.env/sync.toml、不依赖进程cwd、不创建或释放engine，也不安装平台SDK或启动调度器。

`only=None`运行现有全部已接来源与下游；售后完整功能子集为`jst_returns`、`jst_shipped_sales`、`jst_cancelled_sales`、`refund_targets`、`aftersales_report`。only沿用显式子集语义，不自动扩大来源或补跑未选步骤；单独重算报告依赖已有固定输入。完整步骤与依赖唯一维护在project_runtime/project_sync，不在平台再复制一套。

## 执行和结果约定

- 同一PG全局互斥保护本机/平台并发；同slot去重，保留租约、失败接管和1—3轮有限尝试预算。max_attempts默认2，与当前本机配置一致；平台不得给每次重试生成新批次ID绕过幂等和预算。
- 每个来源仍拥有自己的双读/范围/字段/键/Decimal校验及不可变发布。失败保留原有效入口；失败依赖对应的报告/评分跳过，独立来源可继续。整链不是把所有来源合成一个长数据库事务。
- 返回原结构化运行结果：整体status与各dataset状态、结果版本或安全错误；`failed`需由平台标记失败。`already_succeeded`、`already_running`、`retry_budget_exhausted`是去重/运行/预算结果，不能写成又完成了一次拉取。重试是否允许由同一运行记录决定。
- 仓库口令仅在实际接仓步骤取用；只执行评分、目标或天气等步骤不读取仓库口令。飞书仍使用已配置CLI身份及权限，本次没有新增或迁移身份授权。
- 当前本机CLI只负责配置指定的时槽、资源构造、JSON和退出码；普通页面GET继续只查本地已发布版本，不触发平台或数仓任务。

### 持续运行中的失败重试

上述有限尝试预算只约束同一个批次，不是定时系统的终身重试上限。当前调度器对检查、接收和周对账失败，在失败结束后冷却300秒，再为到期的重试创建受控运行；失败仍持续，成功则停止该次重试并回到正常计划。后台轮询与项目串行锁可能让实际开始晚于五分钟，不能同时启动多个Worker来抢跑。重启继续读取PG中持久状态，不清任务表、不改成功时间、不把旧成功数据当作本轮新接收。

2026-11-16用户确认数仓并发额度暂不能调整。`WAREHOUSE_CONCURRENCY_LIMIT`保留真实失败、最后成功日期及下一次检查/重试状态，继续按上述周期尝试；额度释放后自动恢复。接管人员无需手工反复补拉，也不得修改公司资源组、其他项目网络或全局配置。凭据失效、输入损坏、结构不兼容及容量上限等确定性阻断仍需处理根因，重试不能保证外部服务最终可用。当前实测与已关闭问题见[验证记录](../validation/2026-11-16.md)，长期归档规则见[容量治理](archive-retention.md)。

## 整功能迁移清单

1. 部署同版本应用模块、算法代码和锁定Python依赖；需要飞书的运行环境同时准备已有CLI所需Node依赖与批准身份。迁移目标配置、所有已接来源设置和必要环境变量，不复制个人口令文件或登录缓存到未经批准的环境。
2. 使用已完成相同Alembic版本和最小权限的PG；任务进程与API分别使用既有写入/只读职责。迁移数据库、版本/任务历史、原始输入、运行与评分归档及配置，不能只复制定时表达式或拉数脚本。
3. 现有数据库中的retained_input_ref/evidence_ref含绝对路径。已有归档必须保持原引用可访问，例如按原路径挂载；仅修改ProjectPaths只控制后续输出，不会重写历史引用。若要改引用规则，另做受控迁移和回放验收，不自动改封版记录。
4. 在隔离真实PG及批准的取数范围演练完整链：同批重跑、锁冲突、来源失败保旧、依赖跳过、目标及报告绑定、归档重放、API同版读取和新数据状态。平台触发器复用execute_project，不能重写金额、组合分类或发布规则。
5. 接管前停用旧触发器，保留应用、数据库和归档；确认没有执行中任务，再启平台触发器。同一运行环境只保留一个负责的计划触发，PG互斥继续兜底。平台时区、钟点和漏点策略应明确配置，不能假定调用接口就自动执行本机配置的时槽规则。

## 回退

先停平台触发并确认无运行中任务，再恢复旧触发器，继续使用同一有效数据与归档；不清库、不删除版本、不重跑旧迁移。若同时更换业务代码或映射，回退前验证其能读取已发布revision，不能只回退调度器后用旧解析覆盖新输入。重放或修订通过新版本发布，保留旧报告和来源证据。

### 固定来源调度入口（2026-11-04）

新入口 `app.jobs.source_monitor_runtime.execute_source_schedule(engine, *, paths, warehouse_password, warehouse_reader=None, warehouse_headers=None, warehouse_session=None, now=None, warehouse_times=None, feishu_times=None, source_ids=None, force=False, process_products=True)`。当前CLI注入`WarehousePageReader(state_path)`读取可见网页；调用方负责创建/关闭浏览器，read(source_id)只返回表ID、时间与basis的小型摘要。核心策略不依赖Playwright、Chrome路径或状态文件；未来平台可替换reader。网页登录由用户在专用浏览器完成，状态仅在批准运行环境的私密目录保存；迁移服务器须重新核准登录环境和验证，不自动复制个人浏览器资料。旧warehouse_headers/warehouse_session仅为兼容入口，CLI不再选择后台账号认证。

核心策略 `modules/data_status/schedule_policy.py`不访问数据库，`jobs/source_schedule.py`编排，`checkpoint_store.py`负责PG状态和锁，连接器负责读取。商品接收通过`execute_project(product_sources=...)`复用未变来源的不可变版本。

启用 `source_schedule_enabled`时CLI将8数仓和2飞书从旧完整任务和15分钟监控同时排除；聚水潭4来源、天气2来源及售后派生沿旧路径。新固定路径只重试商品评分，不能改变聚水潭报告触发时间。未来平台接管先停对应本机触发器，再调用同一入口；保留数据库状态/共享归档，不复制调度逻辑或启动API内定时器。独立浏览器会话读取和接收验证失败时不得启用替换开关，不能依靠人工看过网页推定自动采集可用。

source_ids只可缩小到已注册迁移范围；force=True用于明确手动补跑，复用锁/状态但不消费固定槽；process_products=False保留手动只接收不重算的选择。不要通过伪造now提前触发或伪造实际接收时刻。
