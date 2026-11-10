# 事实内容复用字段与事务设计

用户于2026-11-10批准第二阶段本地实现；沿用[保留方案](../implementation/database-retention.md)的历史报告及当前数据保护。只在随机本机PG库启用写入，不迁生产存量，不改变来源、接口、计算规则、同步时点或上游读取范围。

## 对象、关系与字段

PostgreSQL17；不新增缓存服务。旧五张事实表原样保留，新版本可选择共享内容表示；旧版本ID和事实ID不改写。业务对象/字段仍由原事实字典负责，内容表采用相同列类型、NULL、CHECK及实体外键，金额仍NUMERIC。

| 对象                                                                                                                                             | 字段及关系                                                                                                                                  | 约束、职责                                                                                                                                                                                                                                   |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| fact_content_blocks                                                                                                                              | id text PK；table_name text；dataset_code text；row_schema_revision text；bucket text；row_count bigint；state text；created_at timestamptz | 块ID为SHA256；所有字段NOT NULL，state默认building，created_at默认clock_timestamp()；table白名单五表，bucket为日期/期间与0—63后缀，长1—32，row_count>0；building→sealed后不可改删；来源/表/行结构语义隔离，不将producer或日期窗口混入内容身份 |
| fact_content_manifests                                                                                                                           | version_id text PK/FK→dataset_versions；table_name text；format_revision integer默认1；row_count bigint                                     | 全NOT NULL；每版唯一事实表示，0行允许；仅draft能插入，不可改删，禁止与旧表事实并存；版本→清单1:1                                                                                                                                             |
| fact_content_members                                                                                                                             | version_id text FK→manifest；bucket text；block_id text FK→blocks                                                                           | 全NOT NULL；PK(version_id,bucket)，索引block_id；版本→块N:M，每版直接保存完整成员集合，不形成无限差异链；仅draft能追加，成员不可改删                                                                                                         |
| shared_product_daily_facts、shared_product_promotion_records、shared_product_region_records、shared_product_sales_records、shared_return_records | 各自原事实表除id/dataset_version_id外的全部显式列，加block_id text FK→blocks                                                                | PK(block_id,source_row_key)，原业务列的类型、CHECK、NULL和shop/listing外键保留；块1:N类型明确事实，不把经营行塞JSON；只允许向building且类型/桶匹配的块插入，拒绝UPDATE/DELETE/TRUNCATE                                                       |
| read_前缀的五张事实视图                                                                                                                          | 旧表UNION ALL版本成员JOIN共享事实，按原事实表列顺序投影                                                                                     | 只读兼容面，不增加事实副本；原旧ID原样，新表示ID由版本ID和source_row_key可逆编码生成，全局无歧义且稳定；不改变业务对象ID、report_id或版本ID                                                                                                  |

所有FK RESTRICT，PK隐含NOT NULL。元数据枚举、摘要及非空字符串有CHECK。实体维度仍在原shops/listings维护；未知字段或错误类型拒绝写入。来源键在每版必须唯一；商品日事实另保留原表的非NULL `(listing_id,business_date)` 部分唯一语义，封版时跨整版全部块校验，未匹配身份或缺日期仍按原规则允许多行。先按business_date或period_start/period_end分组（缺失独立表示），再按source_row_key的MD5前32位模64分桶（仅用于稳定分桶，不用于内容安全校验），排序后以类型明确的全列规范内容SHA256寻址。NUMERIC保留既有小数位表示，不能将100.00与100的串行化差异误复用；数值仍精确，不按当前Decimal精度归一化。空桶不写。块复用跨发布批次/窗口，但不跨数据集、表或row_schema_revision；语义改变必须提升行格式修订，不靠恰好相同的数字混用。

这是存储表示变化，不改逻辑事实范式：版本N:M内容块，块1:N事实。每个版本读到的完整集合由成员清单确定；更正、增加或经原接收流程核实的删除只改变新清单，旧清单不变。普通app只SELECT，ingest能在受控触发器下INSERT及完成building→sealed，不能删除；owner主动篡改DDL不在误操作防护保证内。

## 发布、查询、失败与恢复

显式配置CONTENT_REUSE_ENABLED默认false；启用仍硬限定127.0.0.1:55432、pdd_admin及pdd_operations_v2_test_<12随机位>。写接口按完整规范候选接收，原始来源归档与producer追溯独立保留；内容未变不复制块。既有普通模式继续写原表。每版共享写入在同一事务内完成清单、缺失块及成员；重复写同版只允许完整一致输入，冲突拒绝；不存在半成功清单。沿原领域发布锁，块唯一键按摘要排序插入冲突等待，防并发重复构建与死锁；封版前核对块集合、namespace、行数及逐值重读，失败保留原current。

查询统一改读read_*视图，保留原SQL筛选、SUM/Decimal及API契约；旧版本与新版本可同时查询。同一版本只允许一种表示。新版本封版触发器校验完整清单；旧封版保护不放宽。新增schema用Alembic14，13及更早修订不改写。降级只有共享表为空且没有14归档证据才允许，否则拒绝，不能通过删块强行回退。

第一阶段离线清理保守保护全部共享版本及其输入；只继续处理旧表示候选。部分版本归档明确不包含其他版本的共享事实，不能冒充完整备份。完整备份必须包含新表、视图及函数；恢复时保留成员/块关系。未知格式、包head不相容或缺少恢复适配时拒绝。没有自动GC、没有存量转换；未来清理块须对全部版本/报告做可达性与备份证明。

## 验收

真实PG验证旧/新查询等价、历史报告/比较/评分及日期摘要不变；修改少量行只增加其桶、同内容跨producer复用、跨来源和语义不混用、删除修订保旧、重复键/不完整发布/并发/失败回滚/普通权限拒绝/精确Decimal与NULL及时区。记录合成多批次的物理空间、写入、整版及筛选查询、完整pg_dump恢复结果。代表性业务样本与生产负载未测时不承诺实际节省率或生产启用；不使用生产数据做本轮试验。
