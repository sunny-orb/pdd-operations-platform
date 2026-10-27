# M3店铺日经营首条新版链路实施方案

> 供实施者：使用executing-plans逐项执行；若用户另选并行执行，再按subagent-driven-development安排。用户已授权在本地实施首条链路，包含必要来源投影留存、六表最小权限迁移、首批导入及前后端联调；不包含定时调度或生产发布。当前进度见TODO，实测结果见validation。

**目标：** 用户选择店铺和日期后，能在与新版原型一致的M3页面上查看可追溯的销售指标、全店贡献和销售明细。

**架构：** 数仓必要投影→独立接收任务→PostgreSQL v2不可变版本及短事务发布→FastAPI只读查询→React展示。业务规则在sales领域，查询不触发同步；不复用已放弃的旧M1实现链。

**技术栈：** 现有React/TypeScript/Vite、FastAPI/Python、SQLAlchemy/psycopg、PyMySQL、PostgreSQL 17、Alembic；前期无Redis。

**需求与契约：** [M3完整展示及首段约束](requirements.md#m3-first-chain-scope)、[当前来源及已核范围](data-contract.md#m3-source-access)、[查询契约7.7](data-contract.md#m3-daily-read-api)、[30表字典](database-schema/README.md)。来源证据只在[source-evidence](source-evidence.md#m3-source-verification-20261028)维护，进度只在[TODO](../TODO.md)维护。

## 全局约束

- 最终前端完整保留本地`sites-current/dashboard-portal/app/store-sales/`的原型字段、顺序、布局、样式与交互；不把首段范围当成删减整页授权。
- 前端只走`/api/v1`；金额Decimal/NUMERIC和API十进制字符串，ID字符串；真实零、缺字段、缺行、失败分别表达。
- 只使用本轮已核数仓入口和六店；连接位置、物理表及必要字段唯一维护于契约3.3.1—2。来源凭据通过进程环境/隐藏输入，不保存本次聊天密码；前端不含来源配置。
- 同一逻辑数据集只用一个生效来源；首段不接F01、目标、推广、评分、地区或ERP API，不运行旧M1或旧原型脚本。
- 新增结构/权限变更追加v2 Alembic revision并附回退/修复，不改20261028_01、不跑旧db:upgrade、不在启动时建表。现有事实字段足够，暂不提新增业务表或索引。
- 源码参考目录只读；不整包复制工程、依赖、快照或模型。对照渲染只使用M3必要渲染代码/样式与少量合成固定输入，不读取原型经营快照。
- 本段属于本地开发链路，尚不对公司用户共享；正式认证授权、HTTPS、预发布及切换仍按既有上线要求另验。

## 首段验收范围

真实数据覆盖页头销售日期/覆盖、八卡中的销售金额/店铺访客/转化率/客单价、六店贡献及底部十列中的销售相关列。完整页面结构同步保留，其他四卡、目标、评分和地区显示真实未接入状态。原型评分趋势位置保留，不新增成交趋势。商品/中心等其他域不随本段改写。

经营范围固定2026-02-01至执行时上海昨日、上限2027-01-31，与原型现行经营年一致；首批实际参数固定为2026-02-01至2026-10-27；手工执行须显式指定获准窗口，超范围拒绝；不暗取去年、其他店铺或全历史。

## 对象、关联与更新边界

| 既有表           | 本段职责及关系                                                                                                                            |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| shops            | 六个已核经营店铺身份，使用契约4.1的稳定映射；一个店铺对应多条日事实，FK在事实表；名称及展示别名不作为身份键                               |
| shop_daily_facts | 一版本内一条必要店铺日原行；引用shops和dataset_versions；版本内source_row_key唯一，店铺日重复由发布校验拒绝，有限样本不升级为永久数据库UQ |
| dataset_versions | shop_daily的不可变source版本，冻结字段解释、代码修订、必要原投影与摘要；缺上游批次/水位不补造                                             |
| coverage_records | 版本×店铺/字段×实际窗口的有证据覆盖；未证实完整日保持unknown/NULL，不缓存金额或伪造应有记录数                                             |
| task_runs        | 每次接收、重试和失败；同一shop_daily互斥键、租约、实际尝试与成功时间；失败不切有效版                                                      |
| current_datasets | shop_daily唯一当前有效入口，封版与入口更新在同一短事务完成                                                                                |

原始必要投影通过`retained_input_ref`指向Git忽略的`var/ingestion/shop_daily/<version_id>/input.ndjson`及同目录解释清单及producer-source.json源码快照，目录700、文件600，原JSON仅保留十键的原值和类型、其余六列原表示。它是本项目获准投影的审计输入，不是旧原型快照或全库拷贝；必要输入和生产者源码分别计算摘要，与版本绑定，原子完成后才可封版。权限/留存路径缺失或摘要不符即拒绝发布，自动清理关闭。恢复测试须验证原投影与PG金额、键及版本对应。

首段直接接收的来源版不需要派生version_inputs；未来重解析既有输入才生成derived版本并固定输入关系。shop_daily_facts十个度量字段及规范字段沿现行字典，不另存累计/贡献/率。未匹配原行和失败候选可保留受控证据，但首段正式发布只接受六店已匹配、有效day及核心四项合法的集合。不是任意选一条消歧。

首次无旧版时允许“已观察结果＋unknown完整性”的局部可用发布；后续来源行消失或修订产生新候选和差异，旧键消失未获解释时保留旧成功版。已确认修订按完整新投影重建版本，不原地覆盖已发布事实；不按未读到直接DELETE。FK保持RESTRICT、无级联删除，店铺停用不影响历史金额。版本内事实与覆盖依赖完整键，没有复制店名以外的规范实体属性、累计额或任务状态，沿现行范式边界。

**单一当前入口的范围保护。** 首段每次正式publish均重读配置中完整获准窗口与六店，分窗只用于传输，不逐窗切current_datasets。`--from/--to`若不等于该完整窗口，只允许verify-only，正式发布拒绝`PUBLICATION_SCOPE_MISMATCH`；与旧有效版比对全部历史键，不能把仅最近7天候选切成全局shop_daily而丢掉旧期。未来局部补跑必须先提出“保留原版未覆盖行＋替换已验证范围”的新完整版本设计并授权，不由本段隐含实现。窗口及日历依据冻结在解释清单，仍不冒充上游声明period_start/end。扩大获准窗口时先检查来源覆盖与旧键差异，历史行缺失未解释不切版。

接收器与API、迁移身份分开：API仍SELECT。任务仅授予所需六表的读/插入，以及task_runs/dataset_versions/current_datasets必要状态更新；shops只允许已核身份的幂等建立，不授经营字段维护能力，不授全库DELETE/CREATE。最终GRANT列/触发器兼容性须在隔离PG中逐项验证；通过追加revision 20261028_02落实授权；回退精确撤回本次权限，保留业务数据，入口见数据库迁移方案。

## 重点验证

1. 上游行数不变但金额改动、旧键消失或分页中途变动：不能误判稳定或删除，有限重试后保留旧版。
2. 缺行、真实0、null、非法JSON、不同类型数字、超过两位金额：缺失与错误分开，非法核心值不发布，必要原值可追溯。
3. 零分母、单店筛选及部分字段覆盖：不平均比率，贡献分母始终六店，未知不能变0。
4. 并行任务、租约过期后旧worker继续写、提交中断与同内容重试：不会双发布、越权或破坏旧版。
5. 两个API请求跨版、切筛选后旧响应晚到、未接入区块及窄屏：整组重取有上限，页面不混版或缩掉原型字段。

## 文件职责与步骤

以下为实现职责边界；不能恢复旧M1作为捷径。每项完成后更新TODO和本次validation，按完整已验证目标分步提交中文说明。

### 任务1：固定来源解释并完成独立只读连接器

**文件：** 新建`apps/api/app/modules/sales/contracts.py`、`source_policy.py`、`normalization.py`、`apps/api/app/connectors/shop_daily.py`；测试`apps/api/tests/test_sales_source.py`。source_policy集中六店ID/原名/别名、十键映射、批准窗口及预算，不在多个脚本重复维护。

**接口：** `ReadWindow(start: date, end: date, shop_ids: tuple[str, ...])`两端包含；`ShopDailyReader.read_stable(window: ReadWindow) -> ShopDailyCandidate`输出经双读验证的规范候选、必要原投影、内容摘要和来源时间观测。`normalize_shop_daily(rows, window: ReadWindow) -> ShopDailyCandidate`输出字典对应十度量、原键/时间/状态及覆盖证据；批次对象均在contracts.py定义，金额Decimal，原字符串/类型在保留投影中。

- 先写测试：未知店铺、非day、重复id/店铺日、非法核心值、超两位金额必须拒绝；0保留；无时区更新时间原文保留且规范值NULL；非核心缺项不填0。运行`apps/api/.venv/bin/python -m pytest apps/api/tests/test_sales_source.py -q`，新增接口不存在时应失败。
- 实现独立连接器：参数绑定、单连接串行；默认连接超时8秒/读取25秒，7日分窗、每页最多500条，整次行上限10000、最多完整重读2次。上述为首段技术初值，集中配置、不是上游配额承诺。页序固定dt/shop_id/id，所有调用在批准窗口内；COUNT只辅助，比较全部必要投影与解释的有序摘要（保留重复计数），不只比MAX或COUNT。
- 用无业务数据的stub transport固定测试“金额改但COUNT未变”“中途插删页”“超页上限”“第二遍仍不稳定”“超时”；拒绝无限重试，不用stub代替后续PG集成验收。命令同上应全部通过。
- 后续受权真实读取只运行新连接器的只读核验模式，输出脱敏行数/键/字段校验，不导出经营日志；与本轮限定证据对照，差异先解释。本任务不调用旧脚本或直连底层ETL。

### 任务2：独立接收任务、版本发布及最小权限

**文件：** 新建`apps/api/app/modules/sales/storage.py`、`apps/api/app/jobs/sync_shop_daily.py`、`apps/api/tests/test_sales_publication_pg.py`；新建`apps/api/migrations_v2/versions/20261028_02_sales_runtime_permissions.py`按当前本地授权追加。共用现有db/connection.py的连接建立能力，不使用旧ORM模型推断v2结构。迁移方案追加权限与精确回退说明。

**接口：** `ShopDailyStore.run(receive) -> PublishResult`；`PublishResult(run_id: str, version_id: str | None, reused: bool, status: str)`；`ShopDailyStore`负责任务开始/续租/封版/当前入口，SQLAlchemy Core局部定义与既有v2列一致。`python -m app.jobs.sync_shop_daily --from YYYY-MM-DD --to YYYY-MM-DD --verify-only`只核验，正式写入另需显式`--publish`；普通GET不调用此入口。

- 先写真实隔离PG测试：无写权限拒绝、相同内容重跑复用版本并新增尝试、上游改值生成新版本、旧键消失留候选/旧指针、失败/进程中断不切版；任务状态/租约/事务及受保护封版实测，不能用SQLite替代。使用独立随机`pdd_operations_v2_test_*`库，测试入口显式拒绝项目库。
- 追加权限revision：仅本任务所需对象/动作；升级后API不可写、任务不能改封版或DDL；回退先停该任务并精确撤回本revision新增授权，保留数据/版本，不重建业务表、不覆盖既有合法授权。新空库及既有30表库两条升级路径在隔离库演练。
- 实现跨进程PG锁＋task_runs running唯一键及续租：本地单源先串行，默认租约120秒、30秒续租；可配置且测试覆盖超时。锁内将过期旧尝试标abandoned后再启动；封版及写入检查当前run/worker/租约，旧worker不能越过接管者。持锁不等于长持有发布行事务。
- 原投影落受控位置并校验；短事务按版本ID取锁后验所有事实/覆盖/身份/摘要/租约，置封版、published、当前入口修订号及成功结果。无可靠来源水位则不填写。失败回滚、登记失败，旧成功查询可用；同摘要复用也记录新来源时间观测，不改不可变版本。
- 为上述真实PG测试新增`apps/api/tests/sales_pg_support.py`隔离fixture：使用现有本机管理员配置入口且不打印值，只创建/清理本轮随机库、应用独立v2链并在finally清理。运行`apps/api/.venv/bin/python -m pytest apps/api/tests/test_sales_publication_pg.py -q`须实际执行通过，禁止因未设环境而全跳过仍报完成；`scripts/db-v2.py verify`复验现行链和权限回归。

### 任务3：一致读经营接口与对账

**文件：** 新建`apps/api/app/api/sales.py`、`sales_schemas.py`、`apps/api/app/modules/sales/query.py`、`repository.py`，修改`apps/api/app/main.py`注册sales路由/独立错误处理；测试`apps/api/tests/test_sales_api.py`。错误不依赖旧products.QueryError。

**接口：** `SalesService.filters() -> SalesFiltersResponse`、`performance(query: SalesQuery) -> SalesPerformanceResponse`、`shops(query: SalesQuery) -> SalesShopsResponse`；Pydantic DTO字段精确按契约7.7，`SalesQuery(date_from,date_to,data_version,shop_id=None)`，shops端点不接受shop_id。仓储`read_current_sales(query, *, scope)`在同一REPEATABLE READ只读事务检查当前指针、查事实与真实尝试；不从多次read committed拼版本。

- 先写测试：金额精确、无数据/null/真实0分开、分母0、合计比率不是日率均值、单店卡与全店贡献分别作用、并列稳定排序、缺日覆盖、字段覆盖、无数据店不伪排名、API只读角色拒绝写库/未提供HTTP写路由不成功，以及422日期/店铺边界、409切版、503未接入/数据库失败；PG用隔离fixture，拒绝写项目库。
- 实现三GET及OpenAPI成功/错误模型，过滤和聚合只查当前固定本地版本。未接入字段保留并返回field_states，前端不二次求正式指标；首段无新增HTTP写入口，未授权写入返回拒绝路径而非成功。
- 在PG中构造可区分订单/买家分母与日率均值的合成反例；金额要求Decimal精确相等，比率按契约12位舍入比较。运行`apps/api/.venv/bin/python -m pytest apps/api/tests/test_sales_api.py -q`实际通过。
- 后续受权导入后，对同一固定输入按六店/单店、单日/跨月、零值/缺日分别复算，检查源必要投影→PG→API；只留脱敏差异摘要、范围和版本。上游发生修订须重取固定候选再比，不把不同时间的源与本地差异算成公式错误。

### 任务4：按完整原型还原M3并接入首段

**文件：** 新建`apps/web/src/features/sales/SalesPage.tsx`、`sales.css`、`api/{contracts,salesApi,queries}.ts`、`components/{ShopTable,PendingSections}.tsx`及相邻测试；修改`apps/web/src/app/App.tsx`注册同页路径，共享导航保持原样。新增`apps/web/src/features/sales/SalesPage.test.tsx`、`SalesPage.browser.test.ts`（项目无浏览器测试工具时使用CUA实际核验，不为文件名安装未选工具）。

**接口：** `getSalesFilters(signal)`、`getSalesPerformance(query,signal)`、`getSalesShops(query,signal)`按契约校验并保留字符串；`SalesPage`维护月份/经营范围/店铺与独立未接入区块控件，三请求由data_version协调。底部表与贡献共用同一shops响应，selected只控制高亮及卡片，不能客户端重算分母。

- 先写测试：九顺序区域、八卡标题、四折叠、两地区6/7列表头及底部10列表头不缺项；单店/再次点击恢复、切月带经营日期、自定义日期不改目标月份、旧响应不能覆盖新条件、连续409只自动恢复一次、接口失败不回退样例。未接入区块不生成假分、假目标、假补齐记录或可用曲线。
- 从指定M3必要源码提取渲染与样式规则，在本项目重写为API驱动展示；按原CSS最终级联、尺寸、颜色、字体保留，优先使用现有Ant Design成熟组件并局部还原样式，不用默认外观替代原型。共享样式不得污染商品页。`/sales`与`/store-sales`均脱离当前初始化宣传壳，进入同一个M3页面；跨页文案和参数遵循requirements 5.4。
- 使用与原型相同的格式规则展示：主销售卡到元、客单两位、经营率两位百分数，贡献及底表按原型万单位/份额格式；内部字符串/BigInt格式化，不能JS Number累计金额。仅图形坐标允许近似Number并保留原值提示。
- 建立只读源码必要渲染的隔离参考页，使用相同少量合成输入；在1440×900、1280×900、1024×768、768×1024、390×844及1000/800/700/600/420两侧逐项截图对照，固定缩放100%、DPR、字体及浏览器版本。截图包含完整页面和各折叠态，差异逐项解释；只看代码/CSS不算通过。
- 运行`npm run check`、`npm test`、`npm run build`并记录真实PG测试是否执行；浏览器核验日期/选择器/键盘/横向表格滚动/锚点/窄屏不整页溢出，检查共享能力受影响页面。尚无真实来源部分只验结构及状态，不勾整页业务验收。

## 实施前仍需收敛的边界

- 当前本地实施按用户后续授权执行；共享导航保留，成熟Ant Design优先。定时任务、生产部署和其余来源不包含在首段内。
- 首链单位沿既有人民币元与M3-01决定；仍需来源元/分说明或同条件后台证据补核。若不能确认，代码/合成PG可推进，受影响金额不能升级为正式真实数据验收。
- 上游完整标记、updated_at时区、修订删除语义及访问预算继续技术核查；未知完整日可按现有契约展示观察值，但不能宣称齐数或启用可靠增量。历史旧键消失先阻断替换，不强等全部业务12项确认。
- F01当前程序读取、目标表入口、推广归因及地区日期分别限制后续区块，不阻断首段基础销售代码。全部其他块完成后再按原型整页做数据/视觉/性能/业务四层验收。
- 本段正式负载目标尚未确定；开发环境单日、月、跨月及财年接口样本见validation，后续测SQL/首屏及并发，再确定性能目标和是否需要额外索引，不提前承诺延迟。
