# 来源检查状态表

专用设计见[来源监测](../implementation/project-sync-implementation.md#source-update-monitor)，实际来源清单与日期含义只在[数据契约](../data-contract.md#source-update-status)维护。此表由 Alembic `20261103_10` 追加，前序为 `20261031_09`；[离线SQL](source-checkpoints.sql)由该revision派生，只供审阅。

2026-11-04时间展示追加设计沿同一来源对象，详见[同步实施方案](../implementation/project-sync-implementation.md#2026-11-04-来源名称与时间展示实施方案)。Alembic `20261104_11` 添加下表3个时间字段，[派生SQL](source-update-times.sql)供审阅；不修改旧revision、事实或版本指针。网页时间与核对时刻成对，原文固定为`YYYY-MM-DD HH:mm:ss`，实际日期有效性由写入器校验。成功观测时间只在成功时推进，历史失败行不猜测补值。

## 对象、粒度与字段

一行表示一个实际来源的最近检查和已接收标记，`source_id`为固定清单中的字符串ID。物理来源与经营数据集是1:N关系，由应用清单映射，不重复存储来源配置或经营事实。无跨表外键；主键即唯一业务键，当前16行不加其他索引。

| 字段                 | 类型／约束                          | 含义                                                                     |
| -------------------- | ----------------------------------- | ------------------------------------------------------------------------ |
| source_id            | TEXT，PK，非空白                    | 实际来源ID；同表不同用途共用一行                                         |
| checked_at           | TIMESTAMPTZ，NOT NULL               | 本次检查开始／完成时间，与业务数据日期分开                               |
| check_status         | TEXT，NOT NULL                      | checking／succeeded／failed                                              |
| observation          | JSONB对象，默认空对象               | 最近成功观测的日期、记录数、来源修订等小型摘要；失败保留旧观测           |
| platform_snapshot    | JSONB对象，默认空对象               | 后台计算的各用途真实数据日期及对应已发布版本ID；版本不匹配时不能复用日期 |
| observed_fingerprint | TEXT，可空                          | 最近成功观测的确定性摘要；succeeded时必须存在                            |
| accepted_fingerprint | TEXT，可空                          | 来源同步成功且前后复查稳定时接受的摘要；不是CDC水位                      |
| accepted_at          | TIMESTAMPTZ，可空                   | 接受该摘要的时间；与accepted_fingerprint同时有值或同时为空               |
| error_code           | TEXT，可空                          | 固定错误码，不保存连接信息、原始异常或业务原行                           |
| duration_ms          | BIGINT，NOT NULL，≥0                | 最近一次检查耗时                                                         |
| observed_at          | TIMESTAMPTZ，可空；revision11追加   | 最近一次成功取得observation的时刻；失败不推进                            |
| web_updated_at       | TEXT，可空；revision11追加          | 数仓网页“数据更新时间”原文；网页时区未确认，不转换                       |
| web_checked_at       | TIMESTAMPTZ，可空；revision11追加   | 实际读取上述网页时间的时刻；与web_updated_at成对                         |
| scheduled_slot_at    | TIMESTAMPTZ，可空；revision12追加   | 最近开始的固定逻辑槽；跨日重新计算，不以当日成功停查                     |
| scheduled_attempts   | INTEGER，0—2，默认0；revision12追加 | 同槽恢复次数，正常只执行一次；仅中断且等待20分钟后允许第二次             |
| sync_started_at      | TIMESTAMPTZ，可空；revision12追加   | 本来源最近接收开始时刻，不以共享产品任务代替                             |
| sync_finished_at     | TIMESTAMPTZ，可空；revision12追加   | 接收完成时刻；running时为空，完成时不早于开始                            |
| sync_status          | TEXT，可空；revision12追加          | running/succeeded/failed；无记录时两接收时间也须为空                     |

普通字段依赖唯一来源键。两份JSON是来源观察和版本绑定日期的有界缓存，允许不同类型来源的摘要结构，不能扩成事实副本；经营事实的规范化与不可变版本约束沿原设计。平台事件时间带时区存储，前端按平台时区展示；网页原文不转换。资料修订、目标月份、天气发布时间不能混作经营日期。

## 事务、权限与恢复

监测器持既有`project-source-sync`会话级互斥锁，逐源以短事务提交checking和结果，不在网络读取期间持发布事务。来源同步仍通过原校验及原子发布流程；失败不推进accepted标记，接收中来源再次变化则保留待同步。只有新版本需要后台重算真实日期摘要；GET在只读一致快照中查询状态、版本和任务，不扫描经营事实。

`pdd_app`只授予SELECT；`pdd_ingest`授予SELECT／INSERT／UPDATE，不授予DELETE。不自动清理检查记录或业务输入，锁释放／任务租约过期后按下一个检查间隔恢复。来源完整性不能由指纹、最大日期或同步成功推定。

网页证据写入与后台同步共用项目互斥锁；每批仅含已核物理表、对应网页ID、原样更新时间和实际核对时刻，整批校验／提交，任一来源未初始化或核对时刻倒退则全部回滚。GET只读保存的时间，不自动访问网页。人工登记的网页证据不参与变化指纹，也不改变accepted标记。固定时点连接器验证表身份后取得的dataLastUpdateTime参与新策略摘要，稳定接收复核后才推进accepted标记。11降级前锁表；只要存在网页证据或成功观测时间就拒绝删列，回退应用而保留列；不为回退删除证据。

## 升级与回退

本版本要求数据库结构达到`20261104_12`。已到此版本的数据库直接使用，无须重复迁移；首次部署的新空库按[完整初始化流程](migration-plan.md#current-schema-delivery)执行`schema.sql`一次。仅已有库仍为09、10或11时才按以下步骤增量升级；关闭新固定计划也不能省略本版本读取的时间字段。先备份并通过真实隔离PostgreSQL集成测试，暂停调度，核对现有revision；使用已有`migrations_v2.local.local_url`取得授权loopback连接，在项目同步互斥锁和显式事务内调用`migrations_v2.local.migrate(connection)`，由Alembic依次执行尚未应用的10、11、12。成功后核对`alembic_version_v2`和只读权限，再启动本版本API及恢复原调度。API启动不自动迁移；公司环境仍须独立核迁移权限和调度。

迁移不写入任何实际网页时间。网页证据另按授权核对后登记，与后台同步共用项目互斥锁，不回写上游或修改经营事实。`source_schedule_enabled`保持false，只有后台认证和来源映射验收通过后才切换新计划。

业务回退将`monitor_enabled=false`并回退相关API／前端，保留原每日全量同步和该表。迁移降级先锁表：空表才允许删除，存在记录则拒绝，避免破坏运行证据；不得为降级自动清空表。修复优先追加revision，不改写已执行迁移。

Alembic12的专用设计见[固定调度实施](../implementation/project-sync-implementation.md#source-daily-schedule-proposal-20261104)，[派生SQL](source-schedule.sql)只供审阅。追加5列、不改事实/版本关系；同槽按源去重，检查失败保留上次观测，接收失败保留成功水位。回退应用保持列；只要存在固定槽或接收证据，降级拒绝删列。是否已升级与切换以TODO和本日验证记录为准。

2026-11-10派生摘要扩展：`platform_snapshot.history`保存按版本、规则、窗口绑定的历史记录覆盖结果；无DDL或业务事实变化。字段语义只见[历史覆盖契约](../data-contract.md#history-record-coverage)，事务/失败恢复见[实施设计](../implementation/m6-overview-implementation.md#近30天记录覆盖提醒2026-11-10本地实施)。

2026-11-10上游时效证据扩展：`platform_snapshot.update_timing`保存最多今天/昨天两条网页时间证据，原网页时间与摘要在同一既有事务提交；日期/覆盖摘要刷新必须保留该键。无DDL、索引、权限或事实关系变化。字段语义见[时效契约](../data-contract.md#upstream-update-timeliness)，专用设计见[M6实施](../implementation/m6-overview-implementation.md#上游每日更新时效2026-11-10确认)。
