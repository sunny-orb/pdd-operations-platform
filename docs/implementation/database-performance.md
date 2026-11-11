# 全库查询索引与性能核验

日期：2026-10-31。本轮已核31张表、83个既有业务索引及38类代表查询，追加两条窄索引后共85个业务索引。用户本轮授权核查整个项目数据库与全部看板查询性能，并实现必要索引；不读取上游或执行生产部署。事实、来源、指标口径和关系不变。

## 专用设计与实施顺序

本次对象为现行31张业务表及其已用在线查询。数据库仍为 PostgreSQL 17；不新增业务表、字段、关系、缓存或物化结果。既有主键、唯一性、外键、删除限制、NUMERIC与不可变发布事务全部保留，范式不变。

1. 以只读 `pdd_app` 固定发布版本，盘点31表、索引及实际查询；执行 `EXPLAIN (ANALYZE, BUFFERS)`，记录真实行数和扫描范围。
2. 针对已确认的日期索引不匹配，追加09迁移；不改写01～08。`product_promotion_records` 与 `product_region_records` 新增 `(dataset_version_id, period_start)` 普通B-tree，分别覆盖商品推广日期元信息/筛选以及产品地区日期元信息/筛选。现有索引前缀是 `(dataset_version_id, source_granularity, period_end, ...)`，不能有效定位这些仅按 `period_start` 筛选的查询。
3. 在随机隔离真实PG库检验新索引定义、升降级、业务行/约束/权限保持与失败回滚；离线派生 `query-performance.sql` 供审阅。
4. 受控本机升级入口只接受08→09：取得现有项目同步锁、核对本机集群/结构、保存schema备份和校验摘要，事务内建索引。使用普通 `CREATE INDEX`，DDL在单事务内可回滚；锁等待有超时，繁忙即失败重试，不阻断同步长期等待。Docker/psql身份核验最多10秒，schema-only备份进程最多30秒，pg_dump自身锁等待最多10秒；外部进程超时抛出受控错误，数据库事务退出释放同步锁/结构锁，不继续迁移。
5. 新连接确认提交，按相同冻结版本重跑查询；记录索引体积与限制。回退只DROP本次两条普通索引，不删事实、不拨回发布指针。

当前数据约百万至数百万行；索引构建会增加写入与磁盘成本。因同一同步锁排他覆盖项目写入，本机采用事务性建索引；将来共享环境需依据停写窗口重新决定并发建索引方案。不得直接沿用本机计时作为生产SLO。

## 基线证据

原始表目录、冻结版本、查询与JSON计划保存在 Git 忽略的 `var/database-performance/`。计划是本机连续3次中位数；不包含网络/API序列化/浏览器。同步可能并发增加新版本，前后查询参数保持冻结版本一致。

已发现两类真实缺口：商品推广按period_start取最大/最小日期需要扫描33,027行当前版本；产品地区日期元信息需要遍历238,291行当前版本。产品地区全表已超过160万行；原计划出现并行顺序扫描。其他表的既有索引与是否补充的逐表证据在完成核验后补入。

## 31表覆盖审计

表行数为同一只读RR快照，后续同步增量另行增长。大小含表/TOAST/索引；不等于新增索引大小。小查询单次执行计划计时仅用于排除明显索引缺口，不能作为P95。技术表`alembic_version_v2`只有版本PK，不承载经营查询。

| 表                             |      行数 |   总MiB | 既有索引数 | 查询路径与结论                                                                                               |
| ------------------------------ | --------: | ------: | ---------: | ------------------------------------------------------------------------------------------------------------ |
| `aftersales_reports`           |        21 |    0.12 |          4 | 版本UQ、报告类型/期间/修订索引；目录查询约0.08ms。                                                           |
| `coverage_records`             |       401 |    0.29 |          2 | 版本前缀范围UQ，覆盖查询约0.21ms。                                                                           |
| `current_datasets`             |        21 |    0.03 |          1 | 数据集PK，21当前指针；顺序扫描合理。                                                                         |
| `dataset_versions`             |       133 |    0.24 |          2 | PK及数据集—内容UQ，当前入口联查约0.11ms。                                                                    |
| `listings`                     |     2,873 |    1.26 |          4 | PK及店铺/平台商品复合索引覆盖身份查找；当前2873链接。                                                        |
| `product_daily_facts`          |   611,662 |  570.73 |          4 | 既有版本—business_date索引；30日约14.2ms，min/max约0.03ms，不补。                                            |
| `product_promotion_records`    |   164,410 |  182.40 |          4 | 新增版本—period_start索引，覆盖真实日期筛选/元信息；保留原粒度/期末索引。                                    |
| `product_region_records`       | 1,608,371 | 1046.91 |          3 | 新增版本—period_start索引；原日期元信息约240ms，范围约190ms。                                                |
| `product_sales_records`        | 2,655,910 | 1320.39 |          3 | 版本—款码索引或版本—原行UQ；J2汇总约84.6ms、J3约12.7ms。报告需要大部分当前版本行，无选择性字段不机械加索引。 |
| `product_score_items`          |     9,770 |    2.87 |          1 | 评分—指标复合PK，连接相容标准分项；不为每项状态加低选择性索引。                                              |
| `product_scores`               |       977 |    0.69 |          2 | 版本—链接UQ及PK覆盖固定结果版和分项关系。                                                                    |
| `profile_listing_links`        |    16,900 |   10.88 |          3 | 版本—链接—原行索引覆盖共识分组；资料联查约3.1ms。                                                            |
| `profile_source_records`       |     2,904 |    1.27 |          2 | 版本—原行UQ及PK覆盖固定资料版和关系联查。                                                                    |
| `refund_target_categories`     |         0 |    0.03 |          3 | 当前0行且未用该成员链；已有PK和两种条件UQ，不为未知负载建索引。                                              |
| `refund_target_styles`         |         0 |    0.02 |          2 | 当前0行且未用该成员链；已有目标前缀PK/UQ。                                                                   |
| `refund_targets`               |         2 |    0.05 |          2 | 版本—原行UQ，固定版本读取约0.04ms。                                                                          |
| `return_records`               | 1,015,469 |  590.30 |          3 | 版本—款码索引或版本—原行UQ；J1汇总约27.7ms。明细需返回8.8万行，索引不能消除传输/计算。                       |
| `rule_sets`                    |         5 |    0.09 |          2 | PK及规则包修订UQ，固定规则读取约0.03ms。                                                                     |
| `sales_target_shops`           |        72 |    0.08 |          2 | 复合PK及店铺反查索引，当前72行。                                                                             |
| `sales_targets`                |        12 |    0.05 |          2 | 版本—原行UQ；固定版本目标及成员联查约0.09ms。                                                                |
| `score_standard_items`         |     9,770 |    5.41 |          1 | 标准—指标复合PK；评分分项联查约11ms，已覆盖连接键。                                                          |
| `score_standards`              |       977 |    1.01 |          3 | PK、版本—链接索引、版本—标准码UQ，评分头联查约0.72ms。                                                       |
| `shop_channel_promotion_daily` |    12,602 |    8.02 |          3 | 版本—business_date索引；30日约0.06ms，历史独立版本仍有版本UQ前缀。                                           |
| `shop_daily_facts`             |     9,706 |    6.20 |          3 | 版本—business_date索引；30日约0.04ms。                                                                       |
| `shop_rating_daily`            |     7,954 |    4.43 |          3 | 现有版本—店铺—rating_date索引；日期元信息0.26ms，30日0.13ms，小表无需重复日期索引。                          |
| `shop_refund_records`          |     8,088 |    5.15 |          3 | 版本—business_date索引；30日约0.03ms。                                                                       |
| `shop_region_daily`            |    64,470 |   38.09 |          3 | 版本—business_date索引；30日约0.44ms。                                                                       |
| `shops`                        |        12 |    0.03 |          1 | PK定位；仅12身份，顺序扫描合理，不为status加单列索引。                                                       |
| `task_runs`                    |       339 |    0.49 |          6 | 任务代码/时间、结果版/状态/时间及运行/排队条件索引；最近尝试约0.03ms。                                       |
| `version_inputs`               |       166 |    0.14 |          2 | 输出版本—角色PK及输入版本反查索引；固定输入查找约0.02ms。                                                    |
| `weather_publications`         |         6 |    0.15 |          4 | 历史日期/修订索引及PK/UQ；历史目录约0.06ms。                                                                 |

两个新增索引之外不追加全字段索引或宽INCLUDE索引：本次大型售后查询需要读取固定版本中大量事实，执行计划已经利用版本索引；优先减少重复SQL/Python汇总及重复装载。地区目录需要对当前版本全部候选做正式分类与分组，日期索引不替代其业务计算；SQL优化由业务模块负责。低选择性状态、未知反向FK删除路径和空成员表均不构成新增索引依据。

## 本机升级与固定版本复测

62项独立真实PG/离线检查通过；其中09专项包含派生SQL一致性、两条非唯一索引精确定义、降级/重升保留数据/全部约束/ACL、事务失败恢复08以及备份超时释放两把advisory锁。曾先看到“缺少索引”的真实失败后才添加09；外部备份超时边界也先验证失败，再实现限制。既有数据库约束全套反例继续通过。原01派生schema.sql一致，未改写历史revision。

本机于2026-10-31 10:14（Asia/Shanghai）取得项目同步锁并完成08→09，事务DDL耗时0.701秒，提交后用独立连接核对revision、索引定义和可用状态。新增推广索引1,277,952字节，地区索引13,852,672字节，合计约14.43MiB。迁移前后表/列ACL保持一致；项目原有pdd_app/pdd_ingest的TEMP权限此次仅记录，未扩大或撤回运行权限。索引升级与经营数据同步、正式口径验收分别判断。

同一查询参数/冻结版本，每项连续3次`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，表内为中位数。比较采用第二轮基线；第一次在同步期间波动更大（推广单日158.469ms，地区日期元信息240.403ms），均保留原记录，不把最高值作为唯一改进基线。第二轮基线仍有同步背景，升级后已无同步任务；缓存/系统负载差异会影响毫秒数，执行计划扫描范围的变化可直接确认。

| 固定查询              |    前ms |   后ms | 前共享块访问 | 后共享块访问 | 计划变化                                                        |
| --------------------- | ------: | -----: | -----------: | -----------: | --------------------------------------------------------------- |
| 商品推广日期min/max   |  11.174 |  0.013 |       11,036 |            8 | 并行顺序扫描→两次Limit/Index Only Scan                          |
| 商品推广30日13,637行  |   6.299 |  1.451 |        2,724 |        1,155 | 原行UQ扫描33,027行后再过滤→日期索引直接定位13,637行             |
| 商品推广单日472行     |  12.416 |  0.401 |       11,110 |           42 | 并行顺序扫描→日期Bitmap扫描后排序                               |
| 产品地区日期min/max   |  73.801 |  0.010 |       78,698 |            8 | 并行顺序扫描→两次Limit/Index Only Scan                          |
| 产品地区30日121,785行 | 159.246 | 12.387 |       78,698 |        5,889 | 全表并行顺序扫描→日期Bitmap扫描                                 |
| 产品地区分类目录      | 140.127 | 43.852 |       78,714 |       10,433 | 全表顺序扫描→窄索引版本前缀Bitmap扫描；仍按正式分类遍历当前版本 |

前共享块=计划第三次运行的shared hit+read，后同样；不是唯一块计数。未改索引的查询也因负载/缓存不同变快，不能把所有提升归因于09。此轮没有正式并发容量、同步写入吞吐或p95验收，也未验证生产环境；新增索引写放大和长期体积在实际同步运行中继续观察，不承诺无容量风险。API级优化及整页样本由本轮主验收记录维护。

证据均在Git忽略的本机目录：

- `var/database-performance/baseline-inventory.json`：31表真实计数、83索引定义、冻结版本。
- `var/database-performance/query-suite.json`、`plans-before.json`、`plans-before-repeat.json`、`plans-after.json`：25类代表SQL的固定参数及三次执行计划；`small-query-plans.json`另外13类关联/元信息查询。实际经营行未输出到文档。
- `var/database-v2/20261031T021435515980Z-tests.log`：62项隔离验证。
- `var/database-v2/20261031T021444741758Z-before-query-indexes.sql`：迁移前schema-only备份，权限0600；同时间戳`query-indexes-upgrade.json`记录集群/结构身份、验证摘要、DDL耗时、索引体积和提交后快照。

复跑与回退：先`apps/api/.venv/bin/python scripts/db-v2.py verify`，再使用`apps/api/.venv/bin/python scripts/upgrade-query-indexes-local.py upgrade`；已是09且定义正确只读退出。需要回退时以同一入口显式`downgrade`，仅删除两条新增普通索引，保留全部事实、发布指针、字段及约束；回退已在隔离PG实测，本机经营库未为验收而来回升降。同步忙或进程/锁等待超时均安全退出，不自动中断同步、不自动重试或清理数据。

<a id="return-summary-index-16"></a>

## 16：售后历史汇总期间索引设计与实施计划（2026-11-11）

> For agentic workers：本节设计与本机随机PG实现已获准；独立代码审查后由root另行授权固定恢复副本DDL。实施按 executing-plans 逐项执行；本节不授权生产、提交或修改既有发布证据。2026-10-31的计时和“不再补索引”判断保留为当时证据，本节处理本次已实际复现的新性能缺口。

**Goal：** 追加`20261111_16`，只为legacy `public.return_records`的固定版本/期间汇总增加一条非唯一部分期间索引，使原旧API和新read视图的legacy分支均可受益，不改变查询结果和5秒SQL预算。

**Architecture：** 沿PostgreSQL 17、SQLAlchemy/Alembic及既有受控升级入口；只增加物理读取路径，不新增业务对象、字段、关系、缓存或汇总结果。事实和`dataset_versions`的1:N、报告经`version_inputs`固定来源关系、PK/UQ/FK、NUMERIC/NULL语义、封版/删除限制均不变。原索引保留；未测的shared分支不增加索引。保留/内容复用生产写入硬关闭不变。

**Tech Stack：** PostgreSQL 17、SQLAlchemy 2、Alembic，沿现有Python和测试工具，不增加依赖。

**Spec：** [真实经营事实字典](../database-schema/02-business-facts.md)、[查询索引字典](../database-schema/README.md#五页查询与索引设计)、[数据库设计流程](../database-design.md#database-design-process)。代码head现为16，固定恢复副本及生产尚未执行该索引迁移。

### 证据、边界及索引契约

固定备份run `20261111T005837Z-14ae09`的断网副本已完成内容对账；在PG 1 CPU/1536MiB、head15、旧API镜像下，售后meta→options→overview定向诊断复现SQLSTATE57014：请求16.929秒，失败语句5.0079秒。实际SQL为`Repository.source_summary`对历史J1输入的COUNT/MIN/MAX/SUM，服务器日志SHA `3956d6dae0b51f25d4ba1c0dd28241fd75e5ea79599b0389af3837dd0d7cd4ed`，与原完整fallback失败语句一致。失败前prepared计数custom=1/generic=0，不支持改prepare_threshold/force_custom_plan解决此例。

原索引`ix_return_records_query_14`从旧head12即存在，仅`(dataset_version_id, source_style_code)`。失败同参计划为Bitmap Index→Bitmap Heap→Aggregate；随后一次热态`EXPLAIN (ANALYZE, BUFFERS)`0.349秒，索引候选169161行，heap8936页，保留106335行、过滤62826行，shared hit7955/read1141，无临时块。热态快不抵消冷态超时。表约370万行，relallvisible194129/relpages194184，已autovacuum且n_ins_since_vacuum=0；不能把缺少可见性图当主因。诊断区间实际读盘约745MB、CPU限速约5.39秒，未新增内存上限事件；这些区间值不能精确归到失败单条SQL。

新增索引唯一契约如下（设计文本，不是执行授权）：

```sql
CREATE INDEX ix_return_records_summary_window
ON public.return_records (dataset_version_id, period_start, period_end)
WHERE row_kind = 'detail'
  AND period_start IS NOT NULL AND period_end IS NOT NULL;
```

版本等值后按起日范围定位，终日条件在索引层继续筛选；不承诺第三列提供独立范围跳转。部分谓词与现SQL逐项相同，减少需要进入heap的候选；数量/金额仍从heap读取，不宣称Index Only Scan。索引不去重事实，不改变COUNT对NULL的处理、全NULL金额、真实零、NUMERIC精度或空结果。它不覆盖主明细全部身份列，也不保证所有售后请求都小于5秒。旧款码索引仍服务原用途，不删除、替换或重建。

独立复核已取消原INCLUDE候选：真实PG证明原字段合法的超大NUMERIC会触发B-tree tuple上限54000，属于不可接受的写入语义回归。最终索引不携带这两列，不截断金额、不增加业务数值限制；废弃设计反例单独留证。仅版本/日期键更窄，也不因INCLUDE关闭B-tree去重；每次符合谓词的事实写入仍多维护一个索引。需要实测`pg_relation_size`、构建WAL/临时磁盘、固定批量导入耗时及新增空间，不以估算冒充容量批准。过滤掉约37%候选行不等于同幅度减少heap页；同页混有保留/排除行时仍可能读取原页。需测量空间、块数及完整前缀，不能预判5秒门禁通过。[PG17索引说明](https://www.postgresql.org/docs/17/sql-createindex.html)

### 事务、预算与失败处理

现`upgrade-deployment-db.py`使用单事务、`lock_timeout=5s`、`statement_timeout=60s`。60秒是**单条语句预算，不是整笔事务墙钟上限**；多条SQL和前后核验可累计超过60秒。新增CREATE INDEX必须在原60秒内完成，另记完整迁移及持锁墙钟；不能用0.349秒热查询推断370万行索引构建耗时。[PG17超时定义](https://www.postgresql.org/docs/17/runtime-config-client.html#GUC-STATEMENT-TIMEOUT)

先保留原60秒及5秒锁等待，在获准固定副本测量实际索引构建。若超时或空间不足，停止并保留完整回滚证据；不得自动重试、延长API预算、调整全局参数或改CONCURRENTLY。后续若确需维护DDL专用有界预算，只能依据构建/锁测量另行设计审查，明确其仅适用维护而非API，本计划不预先增加额度。

采用普通CREATE INDEX，持写冲突锁但允许正常读取；沿现有项目写入口/advisory锁和停写窗口，RPA及公司对象不在目标关系内。CREATE INDEX及Alembic版本更新在同一事务内，12路径的13—16全部变更、或隔离15路径的16变更，在错误/取消时整体回滚，不能留下版本16或半成品索引。CONCURRENTLY不能放在该原子事务内，本轮不用。[PG17建索引事务限制](https://www.postgresql.org/docs/17/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY)

### 必要的保留回执head一致性适配

本机完整后端首轮实测1003通过、11失败（243.86秒），失败均为`pdd_retention_item_guard`拒绝16回执的`RETENTION_RECEIPT_REQUIRED`。原因是应用HEAD、归档manifest及两数据库guard共同绑定结构版本，仅改Python HEAD会让15冻结函数继续要求15回执，破坏既有离线/恢复功能。独立复核已批准以下必要适配；不做归档格式与数据库head解耦重构。

16迁移复用冻结14中的`context(head)`及`item_guard(head, PROTECTED_SQL)`，仅将两函数的结构绑定替换为16，其余保护SQL、生产硬关闭、权限、函数身份均不变。升级先取得原`project-source-sync`事务锁，拒绝任何`dataset_storage.state='offline'`；索引、两函数及版本更新同事务提交/回滚。15旧回执原样保留，在16拒用，不修改其head或重新签发。需要旧回执的离线数据须在15先完成恢复。

降级也在同一项目锁内，拒绝任何offline状态，以及任何已登记`schema_head='20261111_16'`回执（包括事实已恢复online的回执）；允许时只删除新索引并将两冻结函数还原15。不得调用15.downgrade，不删除或修改`source_reconciliations`及旧回执。目录16的relations与functions摘要因此均会变化，12/15既有指纹及旧迁移保持原样。

真实PG验收增加：原11回归恢复；15已有offline时升级拒绝且无索引/函数/head残留；16回执完整离线/恢复而旧15回执拒用；16offline及online16回执均拒降级；无阻断条件时精确还原两函数，保留非空对账与旧回执；12/15索引超时或错误时索引、函数、版本全部回滚；生产硬关闭及ACL保护仍通过。首轮失败日志单独留存，最终全套另存，不覆盖失败证据。

### 文件职责与实施步骤

不引入通用升级框架。实施仅涉及：

| 文件                                                                   | 必要变更                                                                                                                                                              |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/api/migrations_v2/versions/20261111_16_return_summary_index.py`  | 新revision，down_revision=20261111_15；upgrade建唯一新增索引并将两保留guard绑定16；downgrade删除该索引并还原两guard到15，不使用IF NOT EXISTS掩盖同名漂移              |
| `scripts/render-db-schema.py`                                          | 目标16；`render_upgrade_sql(start_revision=START_REVISION)`仅接受12或15的已审定离线区间，分别绑定原始SQL摘要与授权剥离计数（12为既有47条、15为0），不开放任意起止版本 |
| `apps/api/migrations_v2/company_db_objects.py`                         | 保留12及15已审目录指纹，增加真实PG取得的16指纹；验证新索引定义/valid/ready，继续严格保护RPA、monitor/扩展、ACL、触发器等边界                                          |
| `scripts/upgrade-deployment-db.py`                                     | 公司CLI仅接受12→16及16精确只读确认，仍拒绝13/14/15等其他生产起点；身份、锁、原事务及60秒预算保持                                                                      |
| `apps/api/app/modules/database_retention.py`                           | 仅同步现行head契约到16；不改变生产apply硬关闭和维护权限                                                                                                               |
| `docs/database-schema/schema.sql`、`schema-songbing.sql`               | 由迁移生成，不手工编辑；空库交付包含16，已用库禁止重放                                                                                                                |
| `apps/api/tests/test_return_summary_index.py`                          | 随机真实PG的索引定义、结果保持、升降级/失败测试                                                                                                                       |
| `apps/api/tests/test_deployment_upgrade.py`、`test_schema_delivery.py` | 直接12→16原子路径、16目录/空库等价和既有外部保护回归；适用head断言随目标更新                                                                                          |

- [ ] **先写真实PG反例。** `test_revision16_index_contract_and_roundtrip`验证旧15无新索引，16键序、无INCLUDE、谓词、非唯一、valid/ready精确符合契约；16→15删除新索引并精确还原两guard到15，事实、旧索引、ACL、RPA/monitor均保留。
- [ ] **验证结果不变。** `test_summary_index_preserves_null_decimal_and_period_boundaries`覆盖detail/subtotal、空日期、边界日期、空版本、真实零、金额/数量NULL、Decimal和重复事实；相同SQL/参数升级前后全部聚合值一致，不能以缩小历史/日期范围通过。
- [ ] **实现最小revision和受控适配。** 按上表更新离线渲染/目录契约/目标检查并生成schema；原01—15迁移不改写。生产CLI不新增15入口，已15副本只使用审定15:16增量作为独立验证场景。
- [ ] **验证直接12→16原子性。** 随机真实PG从12、带历史报告/原事实/RPA/公司哨兵开始；完整升级后仅预期新对象及索引变化，当前指针/历史引用、数据摘要、ACL和外部对象不变。16二次调用只读；同名异构索引、起点/目录漂移均拒绝。失败测试让第二连接仅持return_records的ROW EXCLUSIVE锁（允许前置视图读取，但阻塞CREATE INDEX所需锁），在测试事务内缩短statement_timeout制造真实57014，确认整笔迁移回到12、无新索引及13—16残留；相同取消/锁失败测试在15增量回到15。测试缩短预算不进入生产逻辑。
- [ ] **固定副本对照另行授权。** 精确核对原run、完整容器ID、PG17/head15、1536MiB/CPU1/断网无端口/挂载及34原表事实摘要；以原60秒实际执行15:16普通事务DDL，记录构建/锁墙钟、WAL/临时磁盘、索引字节数和提交后目录。失败完整回滚为15并留证。
- [ ] **性能及成本验收。** 新索引有效后，用已捕获失败参数SHA279675ad…、当前及大/小历史固定版本分别比较原SQL结果摘要、shared hit/read、heap fetch/heap blocks、计划、时间；保留冷暖/前序负载条件，不清理共享主机缓存或预热后当正式门禁。目标是明显降低heap读取且在原单SQL5秒内通过完整售后前缀；若仍超时停止分析，不扩资源/缩范围。随机本机真实PG另用同一固定合成发布批次（包括15原有超大NUMERIC和16新增合法大数，均要求原值/聚合不变）做有/无该索引的写入耗时、WAL及索引空间对照，不向备份副本写业务行。
- [ ] **适用检查与独立审查。** 运行新增索引、受控升级、schema交付真实PG测试及`npm run check:db-schema`、后端检查；无前端业务修改不重复本地前端全套，CI沿现有全套。独立审查设计/迁移/生成SQL/保护边界与实测证据后，再由root决定提交、候选镜像和发布；本计划不提交。

### 发布链、回退和证据衔接

生产实际12，最终路径必须由随机真实PG证明**直接12→16单事务**；之后仍执行发布前实际head核验。固定恢复副本已经15，可追加精确15→16隔离证据；原12→15通过记录只证明原步骤，不能冒称它已重跑12→16。保留旧old/new/upgrade和三次fallback失败文件；新DDL、索引性能与完整业务门禁绑定新candidate/build、原备份、原容器及独立attempt，私有发布工具适配由root维护，不覆盖既有回执。

旧API在16可以继续读原表并受益于新索引，应用回退优先保留16结构。若必须回退数据库，只允许经精确目录核对、无offline版本且无任何已登记16归档回执的16→15移除此一索引并还原两guard到15，保持所有事实/历史/RPA/公司对象；这会失去性能收益，不能视为服务可用性自动恢复。不能自动继续退15→12、删除复用/保留表、回拨发布指针或手动stamp。结构回退、旧API相容及性能门禁分别验证。

**审查重点：** 谓词与NULL/期间语义是否相同；新索引是否真的降低候选及heap访问；同名漂移/invalid索引是否被拒绝；60秒构建失败是否整体回滚；新旧证据是否明确区分。本地回归与固定副本构建/空间实测见[本轮验收](../validation/2026-11-11.md#16迁移及本地回归)；完整接口性能及生产容量仍待验，不预先标通过。诊断安全摘要在私有`.superpowers/sdd/deployment/task-3-fallback-filter-diagnosis.md`及本批combined-release证据中；业务参数/原行不进入Git。

### 16本机有限合成成本对照

最终索引不含INCLUDE。两个独立随机PG17库各预置2000行，再发布相同6000行合成明细（含NULL、真实零和高精度值），有/无索引的聚合摘要完全相同。无索引发布0.369465秒、区间WAL3081656字节；有索引0.390913秒、区间WAL3627440字节。新增索引2000行时32768字节、8000行时73728字节；2000行构建0.005266秒。此为单组有限测量，WAL区间可能含集群后台活动，不能外推370万行构建预算、生产吞吐或P95。

另有真实PG回归要求：15中已有约1.2万位非重复十进制合法NUMERIC能够升级16；16新增更大合法NUMERIC后仍逐值/聚合精确，NULL、期间边界、subtotal排除均保持。废弃INCLUDE版本曾因6216字节tuple超过2704字节上限产生54000，这份红例独立保存，不能当作允许拒收大数的业务预期。最终期间索引不携带两NUMERIC列；没有新数值限制或截断。固定副本的真实候选/heap块、完整前缀及60秒构建可行性仍待后续授权实测。
