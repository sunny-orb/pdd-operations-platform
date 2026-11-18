# 数据库离线保留字段设计

## 20：商品地区无用副本回收

2026-11-18追加 `region_retirements`，只记录一个地区版本的证明与回收进度，不保存经营事实或新备份。`version_id`为PK/FK，`replacement_version_id`为替代已发布共享版FK，均RESTRICT；`status`为verifying/retained/reclaiming/reclaimed/rebuilding/restored；`representation`为legacy/shared；`cursor`为来源键，或共享表示的JSON二元组[内容桶,来源键]；`checked_rows`、`deleted_rows`为非负累计逻辑行数；`original_rows`在完整证明前为空；`content_sha256`为有序全部业务行的滚动摘要；`transaction_id`绑定受控本事务；`reason_code`记录保留原因；`created_at`、`changed_at`为数据库时刻。索引覆盖替代版本，验证/回收活动记录最多一个；完整事务恢复可独立执行。摘要与游标均依赖版本主键，符合3NF，无事实宽表或缓存。

`dataset_storage.state`扩展为online/offline/reclaiming/reclaimed/rebuilding；仅offline必须绑定归档，原offline规则不变，pin仍只允许online。地区新增状态只能由受控DB函数推进，普通DELETE/UPDATE无法移除封版事实或直接改变驻留。验证候选保护当前、上一成功版、所有其他域版本的递归输入、保留钉与活动替代版；真实退出事件至少24小时。未知退出、draft和不同schema保留。

legacy按来源键每批最多1000行，与替代共享版全部业务字段严格核对，仅排除物理id和dataset_version_id；任何差异整版保留。共享版先按不可变块成员子集证明包含关系，再按[内容桶,来源键]每批最多1000实际业务行形成恢复摘要，不能只凭可由写入者提供的block_id认定恢复正确。全部证明完成后才进入reclaiming，分批移除旧行或成员；reclaimed不可发布。恢复用本次完整候选，在同一事务内核对原指纹、行结构修订、行数和全部业务行摘要，保留published身份，重建共享表示后online。恢复完整内容核验沿同步整轮预算；它不属于10秒GC小批事务。现有事实物理ID不属于地区API/入站FK消费者；发现新入站FK则拒绝清理。

审计、驻留转换及受控删除还验证PG_CONTEXT最后实际调用帧与固定入口签名一致，不把用户可写GUC当作授权。普通数据库所有者DML同样不可伪造证明；修改函数/禁用触发器等DDL仍属于数据库所有者权限边界。NULL批量预算在数据库直接拒绝。恢复后再次退出当前且超过24小时可以重新证明；保护条件解除或替代版变化时可重试已保留版本，不永久遗留可用副本。

所有写块事务先持维护共享锁；每一维护批次同时尝试project-source-sync及维护独占锁，拒绝运行任务。共享孤块须无任何成员引用（含draft/rebuilding），整块行及块头同事务删除，超过1000行的块保留。原始归档、coverage、version_inputs、published身份和业务指纹均不修改。现阶段不执行VACUUM FULL、repack或全表重写。

本地第一阶段；实施目标与保护规则见[专用方案](../implementation/database-retention.md)。不改变经营事实、report_id、输入及比较关系。五个运维对象与现有版本形成关系；全部FK使用RESTRICT，密钥和业务行不进入运维字段。生产维护入口本阶段硬关闭；18新增精确本机业务库管理员apply/restore，原随机隔离库用于恢复演练，具体边界见下节。

| 表                        | 字段及类型                                                                                                                                                                                                                                                           | 约束与关系                                                                                                                                                                                                                                                                                     |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| dataset_storage           | version_id text PK；state text NOT NULL 默认online；archive_id text NULL；keep_online boolean NOT NULL 默认false；changed_at timestamptz NOT NULL 默认clock_timestamp()                                                                                              | version_id→dataset_versions.id 1:1；archive_id→retention_archives.id N:1；state∈online/offline，offline必须有archive_id；保留钉keep_online阻止清理；CHECK要求keep_online为true时state必须为online，触发器对已离线版加钉返回RETENTION_PIN_REQUIRES_ONLINE，等待维护提交的并发加钉也不能伪称在线 |
| dataset_publications      | id text PK；dataset_code text NOT NULL；revision bigint NOT NULL；previous_version_id text NULL；version_id text NOT NULL；occurred_at timestamptz NOT NULL                                                                                                          | 两版本FK→dataset_versions；(dataset_code,revision) UQ，revision≥0；current真正变化后原子追加；同版重复发布不增加记录；索引previous_version_id、version_id；旧切换事件不猜测补造                                                                                                                |
| retention_archives        | id text PK；database_name text NOT NULL；schema_head text NOT NULL；format_revision integer NOT NULL；cipher_sha256 text NOT NULL；manifest_sha256 text NOT NULL；locations jsonb NOT NULL；verified_at timestamptz NOT NULL；restored_at timestamptz NOT NULL       | SHA256为64位小写十六进制；格式=1；locations为数组，仅无秘密的副本定位及故障域；登记不可改删，离线版本FK保护归档                                                                                                                                                                                |
| retention_operations      | id text PK；archive_id text NOT NULL；kind text NOT NULL；plan_sha256 text NOT NULL；policy_revision text NOT NULL；code_revision text NOT NULL；transaction_id bigint NOT NULL；status text NOT NULL；created_at timestamptz NOT NULL；finished_at timestamptz NULL | archive FK；kind∈offline/restore；status∈running/succeeded；事务ID绑定本事务，成功状态必须有结束时间；一版一事务，失败全回滚                                                                                                                                                                   |
| retention_operation_items | operation_id text；version_id text；table_name text；row_count bigint NOT NULL；content_sha256 text NOT NULL                                                                                                                                                         | 三字段联合PK；operation/version各FK；table白名单仅三表，行数≥0、SHA256格式约束；索引version_id                                                                                                                                                                                                 |

运维归档登记与操作明细只追加；结果由同一维护事务完成。普通运行角色只读运维表；版本登记与发布事件由固定search_path的数据库触发器写入，不向应用开放维护写权限。内容保护在新revision中扩展：普通sealed INSERT/UPDATE/DELETE仍拒绝；只有同事务登记操作、固定表/版、已验证归档与保护集合全部吻合的DELETE或恢复INSERT可通过。UPDATE事实永不放行。TRUNCATE保持拒绝。

保护集合包括当前及最近不同版、所有非白名单版本、显式保留钉及version_inputs闭包；报告、天气、标准、评分、资料和全部比较链因此保持在线。任一running任务使维护退出。未知schema/head、未知版本字段/关系、未知退出时间拒绝操作。版本、输入关系和current触发器阻止离线版再发布或被新派生依赖使用；Python统一入口提供明确恢复错误。

范式：状态只在dataset_storage维护；事件只记真实切换；归档只描述物理包；操作与版本的N:M关系通过明细表表达。业务粒度与金额类型完全不变。新增版本在同一事务自动登记online；已有版本迁移回填online。downgrade只有没有离线版本、归档及操作证据时可执行，否则保留schema并拒绝回退。

维护事务读取批准策略，SET LOCAL限定锁等待和语句时限；PostgreSQL 17的transaction_timeout另限制整笔事务总时长（与statement_timeout_ms同值，默认30秒，允许更短）。跨多条短SQL、应用处理和提交累计超时即终止该连接并回滚，释放事务锁；不调整共享库全局设置。

## 18：本地业务库受控保留入口

2026-11-14用户授权在本地完善和验证；新Alembic18替换既有两个保留保护函数，并仅向pdd_ingest增加alembic_version_v2.version_num列的SELECT权限，用于发布前核对结构版本；不开放结构写权限，pdd_app仍不可读迁移表。回退撤销该列授权。保留函数：head绑定18，操作明细guard在原随机测试库外增加精确pdd_operations且session_user=pdd_admin。不新增表/列/索引、不修改现有业务行或任何售后对象。Python另校验127.0.0.1:55432及实际数据库/会话身份，生产pdd_dashboard/songbing仍拒绝。

本地export→完整解密verify→随机隔离PG恢复drill→绑定计划apply→必要时restore流程保持；本地新增入口只扩大已授权库身份，不放松保护闭包、归档与回执、锁、24小时、单版本、30秒事务预算。清理不是永久丢弃数据，离线候选仍保留可验证归档；不自动启用定时删除。当前及所有历史报告、评分引用和全部共享版本始终保护。

17→18先受控维护锁/无运行任务、核对历史摘要后执行；18产生归档/离线证据后拒绝降级，须先按既有恢复路径处理，不静默丢弃证据。生产同步步骤必须同步到部署文档与schema.sql/schema-songbing.sql，虽然本轮不部署。
