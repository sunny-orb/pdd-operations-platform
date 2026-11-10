# 事实内容复用实施计划

> 执行方式：executing-plans，本会话连续完成本地实现与测试，一次最终独立审查。用户已批准方向与执行，技术细节沿数据库流程落地，不重复索要许可。

**Goal:** 五类大事实的未变化内容跨版本共享，保留当前/历史报告及其查询行为。
**Architecture:** 旧物理表不改动；新增不可变内容块、完整版本成员和类型化事实表，通过只读UNION视图兼容查询。新写入显式启用且只限随机本机PG；生产保持关闭。
**Tech Stack:** PostgreSQL17、Alembic、SQLAlchemy、现有Python/React工具链；无新增依赖服务。
**Spec:** [字段与事务设计](../database-schema/content-reuse.md)、[保留方案第五节](database-retention.md#五长期减少新增副本)。

## Global Constraints

- 仅本地实现、合成随机PG库；不接生产/上游、不删除线上数据、不推送/部署。
- 现有指标公式、接口字段、报告ID、比较绑定及来源接收规则不变；存量事实不转换。
- CONTENT_REUSE_ENABLED默认false，启用仍限定随机本机测试库。
- 原01—13迁移不可改写；新14及生成SQL必须一致。
- 保留现有工作区三份11/09审计修改，不混入本次提交；基线9475ab2，分支codex/database-content-reuse。

## Review Focus

1. 相同数字不同来源/类型/单位语义误合并：Task1 namespace、全列、Decimal/JSON/NULL规范化反例。
2. 新表示事实被旧查询遗漏或重复计算：Task2混合存储完整API与报告前后等价测试。
3. 并发块构建、晚到补行及格式混用：Task1真实PG行锁/不可变/封版拒绝测试。
4. 迁移/备份只识别旧表、离线清理误碰共享版：Task3目录检查、全量备份恢复、共享根排除测试。
5. 少量修改造成全块变化或大规模性能回退：Task3固定键桶多批次空间与读写基准；失败不启用生产。

### Task 1: 内容存储与数据库不变量

**Files:** 新增app/modules/fact_content.py、migrations_v2/versions/20261110_14_content_reuse.py及必要冻结辅助模块；tests/test_fact_content.py；config.py；字段设计。
**Interfaces:** write_version(c, table_name, version_id, rows)写完整规范候选，返回新块/复用块/行数统计；read_name(table_name)白名单映射read_*；read_table(c,name)按冻结字段定义构造逻辑视图查询；enabled(c)校验配置及本地边界。

- [x] 写真实PG失败测试：两版大部分相同、修改/删除/新增、精确值、namespace、重复键、已封版改删/补行、半完成发布、并发与回滚。
- [x] 运行node scripts/api.mjs test tests/test_fact_content.py，确认因新能力缺失失败。
- [x] 实现字段设计和事务接口，补普通角色与错误表示的数据库兜底。
- [x] 专项通过；生成schema并核对目录。
- [x] 保存已验证本地提交（可与Task2保持完整目标一并提交）。

### Task 2: 发布与全部消费者兼容

**Files:** product_board/storage/repository/read_queries、promotion/repository、sales/storage/region_queries、aftersales/publisher/repository、data_status/repository；tests/test_fact_content_api.py及发布专项。
**Consumes:** Task1写/读接口。
**Produces:** 三个发布器启用时写共享事实；同版复用/回退校验仍沿原流程，API字段与规则不变。

- [x] 构造旧/新表示同输入，测试当前四域与中心依赖、所有合成报告修订/比较、评分输入、版本日期摘要；正常与缺失值完全一致。
- [x] 运行专项，确认新表示未接入时失败；再替换读表接口及受控写入口。
- [x] 原始输入仍按原规则归档；发布器实际读回完整字段校验而非仅数行。
- [x] 专项与受影响原测试通过，记录无变更范围。

### Task 3: 维护兼容、容量与最终验收

**Files:** database_retention/database_archive、scripts/init-deployment-db.py、schema生成交付、tests/test_schema_delivery/test_deployment_database、tests/test_fact_content_restore.py、scripts/benchmark-content-reuse.py；负责文档与本机backup-support表清单。
**Consumes:** 新schema及逻辑视图。
**Produces:** 共享版本不进入第一阶段离线候选；完整备份/隔离恢复及可复跑基准。

- [x] 先写新目录、共享根保护、完整备份恢复的失败测试，再适配head/对象白名单/权限与旧包拒绝。
- [x] 在随机PG合成多批次对比旧快照与共享内容物理空间/写入/查询，不读取真实经营数据。
- [x] npm run check、完整前端/后端套件、npm run build；无交互变更不虚构浏览器功能验收。
- [ ] 一次fresh-context独立全范围审查；Important/Critical按RED→GREEN修复并最终全套通过。
- [ ] 验证、风险/限制写回负责文档，更新TODO，提交仅本轮文件；保留本地分支，不推送/部署。
