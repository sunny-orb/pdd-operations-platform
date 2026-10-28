# 拼多多运营平台

公司内部拼多多运营看板的独立重构项目。采用React/TypeScript前端、FastAPI后端和PostgreSQL查询库。当前模块进度与授权统一见[TODO](TODO.md)，实际验证见[验证记录](docs/validation.md)。

**数据库采用独立v2迁移链。** 新设计及[实施方案](docs/database-schema/migration-plan.md)覆盖30张经营业务表，天气在独立[专用设计](docs/m6-overview-implementation.md)中追加1张发布归档表；实际执行进度见[TODO](TODO.md#当前任务与范围)，证据见[验证记录](docs/validation.md#database-v2-local-created)。旧M1模型/迁移、旧解析及评分任务保留历史且继续停用；M1新版三来源、查询和前端已迁到v2，不运行旧`db:upgrade`或默认`alembic upgrade head`。任何结构变更先改migration并说明原因，schema.sql仅为派生审阅稿；已执行revision不改写，未来追加新revision。

<a id="refactor-principles"></a>

## 为什么重构，先保证什么

旧代码需要处理的问题包括：页面组件和样式重复维护、商品页加载全量数据、指标与关联规则分散、缺数和失败提示不清、评分与经营数据更新不同步，以及测试和运行维护不统一。已确认问题、待核实风险和源码依据见[重构清单](docs/refactor-checklist.md)，这里不另建一份问题列表。

重构时按以下原则取舍：

- 先保证数字和权限正确，再改善更新速度、页面操作和维护方式。核心指标出现未解释差异、越权或把失败显示成正常结果时，不发布受影响的结果。
- 保留已确认的五页能力、正确口径和操作习惯；允许调整组件、查询和内部结构。已知旧错误应修正并说明差异，不能为了“新旧一样”继续保留。
- 某项数据或评分缺失时，其他不依赖它的区域继续可用，缺项写清原因。同条件下保留旧成功结果时注明日期和刷新状态。
- 页面等多久、数据最多晚多久、故障后多久恢复，由业务影响和实测结果确定，不提前承诺。旧系统基线与验收办法见[验证文档](docs/validation.md#baseline-package)，实际待办只放在TODO。

## 源码与重构目录

**线上看板最新源码已获取**，对应2026-10-23备份的Sites最新保存版本。源码参考与重构开发分开：

| 用途                     | 本机地址                                                                                                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 线上最新源码（只读参考） | [/Users/sunny/Projects/operations-dashboards-source-backup-20261023-155031/sites-current/](/Users/sunny/Projects/operations-dashboards-source-backup-20261023-155031/sites-current/) |
| 当前重构代码（实际开发） | [/Users/sunny/Projects/operations-dashboards/pdd-operations-platform/](/Users/sunny/Projects/operations-dashboards/pdd-operations-platform/)                                         |

各看板源码入口见[模块源码地图](docs/requirements.md#11-本地源码参考地图)。后续重构只参考新版，所有开发改动放在当前重构项目。源码版本与模型运行资料缺口见[来源证据第12节](docs/source-evidence.md#12-源码备份完整性与引用迁移2026-10-27)；运行资料缺口不代表源码尚未取得。

## 从这里阅读

| 想了解什么                             | 唯一入口                                         |
| -------------------------------------- | ------------------------------------------------ |
| 旧看板哪里有问题、为什么要改           | [重构清单](docs/refactor-checklist.md)           |
| 页面交付后还有哪些问题                 | [统一问题台账](docs/issues.md)                   |
| 现在做到哪一步、接下来做什么           | [TODO.md](TODO.md)                               |
| 系统/每个页面要展示什么                | [需求与页面说明](docs/requirements.md)           |
| 数据来源、字段、指标、关联、依赖和接口 | [数据与接口契约](docs/data-contract.md)          |
| 全局架构、组件职责、同步及部署边界     | [架构基线](docs/architecture.md)                 |
| 数据库业务对象、关系及设计流程         | [数据库设计流程](docs/database-design.md)        |
| 30表完整字段、约束与展示覆盖           | [数据库字段设计](docs/database-schema/README.md) |
| 验收要求、实际验证及未验证范围         | [验证与验收](docs/validation.md)                 |
| 核查结论的证据与历史限制（按需）       | [来源核查附录](docs/source-evidence.md)          |
| 协作、安全、验证和提交规则             | [AGENTS.md](AGENTS.md)                           |

各文档按上表分工维护，同一口径、方案或进度只保留一个负责位置。旧调查按实际观察日期保留证据，不再维护上级旧文档作为另一套规范。讨论稿、已确认决定、样例交付和真实验收明确区分。

## 本地运行

要求Node.js 22.13+（推荐24 LTS）、Python 3.12+。安装与启动：

```bash
npm ci
python3.12 -m venv apps/api/.venv
apps/api/.venv/bin/python -m pip install -r apps/api/requirements-dev.lock
npm run dev
```

Windows使用`py -3.12 -m venv apps/api/.venv`和`apps/api/.venv/Scripts/python.exe`。可将`apps/api/.env.example`复制为同目录`.env`配置应用数据库；数仓与应用数据库是不同资源。只提交无凭据模板。

| 入口         | 地址 / 行为                                                                                                      |
| ------------ | ---------------------------------------------------------------------------------------------------------------- |
| 前端         | http://127.0.0.1:5173                                                                                            |
| API / 文档   | http://127.0.0.1:8000 / http://127.0.0.1:8000/docs                                                               |
| M3店铺销售   | http://127.0.0.1:5173/sales ，`/store-sales`同页；店铺日、退款、推广、评分及两类地区真实接入；目标入口当前不可用 |
| 存活 / 就绪  | `/health/live` / `/health/ready`；数据库未配置时就绪503是预期，不能冒充业务正常                                  |
| M6运营中心   | `/`，复用四域真实接口；全国天气独立归档，日期/失败/待核按来源显示                                                |
| M1真实商品页 | `/products`，新版三来源真实查询；新试行标准/参考部分分已接入，缺项及待验证原因保留                               |

本机数据库使用Docker Desktop，整个项目共用`pdd_operations`，使用独立v2结构，容器和数据库本身保留。以下迁移/测试入口是旧实现的运行说明，重新设计期间暂停执行：

```bash
npm run db:up       # 启动容器；首次建立项目库和独立测试库，不同步数仓
npm run db:upgrade  # 暂停：会重建旧M1表，待新设计/迁移方案确认
npm run db:status   # 查看容器状态
npm run db:test     # 前后端全套测试，PG合成数据仅进入pdd_operations_test
```

连接地址`127.0.0.1:55432`；数据库`pdd_operations`，应用用户`pdd_app`。本地随机凭据保存在Git忽略的`var/local-db/docker.env`，应用连接自动生成到`apps/api/.env`，已有配置不会覆盖；不要粘贴或提交其内容。Docker Desktop的Containers中显示`pdd-operations-platform`项目，数据位于命名卷`pdd-operations-platform_postgres_data`，重启容器会保留。关闭容器无需删除卷；不要执行`down -v`。测试入口会在独立测试库演练迁移回退，不能改为业务库。

本机DBX已添加“拼多多运营平台 · 本地管理”连接，使用现有`pdd_admin`管理账号，按用户要求支持手动查询、编辑数据和管理表结构，密码已获授权保存到DBX。该连接仍保留；`pdd_operations → public → 表`对应当前v2结构，M3事实按不可变版本及当前入口查询；这是本地人工管理入口，API继续使用只读`pdd_app`，既有数仓连接未改动。项目正式结构变更仍通过Alembic维护。

手工/非本地Docker部署仍可在`apps/api`使用具有迁移权限的`DATABASE_URL`执行`python -m alembic upgrade head`；服务启动不自动建表。该命令会按旧迁移重建旧结构；当前暂停，新链使用[独立v2方案](docs/database-schema/migration-plan.md)，不能混用。readiness成功只代表应用库可连接，不代表经营API或页面已完成对账。

## M1商品经营当前入口

`/products`已从零重建，旧本地前端未读取即移除，没有样例回退入口。React/Ant Design/Recharts保留原型六区与共享导航，真实数据只通过`/api/v1/products`查询。总览、评分、底表三套URL范围独立；金额与计数传输字符串，展示舍入用BigInt，只有图形坐标近似Number。

```bash
npm run sync:m1 -- --verify-only # 隐藏输入数仓密码，双读必要投影，不发布
npm run sync:m1 -- --publish     # 三来源与资料关系原子发布到本地v2
npm run dev                     # 本地前后端
```

接收仅用已批准三张数仓表、六店day、最近60日及去年对应期和当前必要资料；连接位置见[data-contract 3.2](docs/data-contract.md#32-推广字段)，本轮证据见[source-evidence](docs/source-evidence.md#m1-v2-rebuild-20261028)。凭据不写命令行/仓库。来源私有投影、解释及生产代码留在忽略的var中，API仍为只读pdd_app，接收为现有pdd_ingest。当前本机项目定时器已启用，运行入口见下；来源修订形成新版本，受控完整分页双读下的移除键留审计，既有原行/版本不删除。来源读取失败保留旧成功入口并明确状态，不回写来源。

当前GET为`meta`、`options`、`overview`、`days`、`scores`、`scores/{listing_id}`，固定data_version，不相容返回409；覆盖、成功和失败状态分别表达。四榜、健康度、七指标、流量链路、趋势和风险由本地聚合；评分区读取相容四输入及新试行标准/评分版本，返回十项原值、参考部分分、四线/样本和待验证原因；完整分与正式放量保持关闭。收藏新增定义接入、归因窗口与标准适用范围限制见[问题台账](docs/issues.md#m1-商品经营含新品评分)。接口以[data-contract 7.9](docs/data-contract.md#m1-v2-query-contract)为准，历史旧M1接口和model_versions等不再是当前物理结构。

旧`profiles:m1`、`scores:m1`、`db:upgrade`仍停用，不用旧任务重建已删除的数据库结构。04授既有M1表最小权限，05补评分父行FOR SHARE必需的UPDATE(id)权限（封版不可变仍有效）；真实PG验证后，受控本地升级脚本为`scripts/upgrade-products-local.py`，已升级不会覆盖数据。

v2本轮受控入口为`apps/api/.venv/bin/python scripts/db-v2.py verify`（仅新建/清理本轮隔离库并验证，项目只读）和`create`（仅允许已验证的初始空库；当前已创建后会拒绝重复覆盖）。入口不读取通用DATABASE_URL；不直接运行schema.sql，不开放旧M1接数或同步。后续结构变更追加Alembic revision及实施/验证说明。

Python和前端都有锁文件；后端依赖从`requirements-dev.in`在独立环境解析，不只改输入。正式数据表变更走Alembic，仅按已批准范围创建业务迁移；业务待决口径不在建表时默认确认。

目标GitHub仓库为私有`sunny-orb/pdd-operations-platform`；初始化推送是历史记录，当前本地提交与远端同步需分别实查。凭据、经营数据、备份、模型及日志不提交，生产发布需要明确授权。

## 项目自动同步与推广入口

推广页为`http://127.0.0.1:5173/promotion`，默认店铺推广；商品模式`?mode=product`。两模式真实数据/独立版本/来源状态、八指标/趋势、漏斗/Top5、SOP观察及分页已接入；跨渠道成交和未核口径限制见[统一问题台账](docs/issues.md#m2-推广分析)。

本机独立launchd检查已接入来源，每日Asia/Shanghai 10:00、14:00、18:00串行刷新；来源成功后自动重算相容试行评分。页面45秒及聚焦检查本地新版本，默认日期推进、自选范围保留。钟点唯一配置为[config/sync.toml](config/sync.toml)，也可调整：

```bash
apps/api/.venv/bin/python scripts/sync-project.py --status
apps/api/.venv/bin/python scripts/sync-project.py --set-times 10:00 14:00 18:00
apps/api/.venv/bin/python scripts/sync-project.py --run-now
apps/api/.venv/bin/python scripts/install-sync-schedule.py          # 本机安装/重载
apps/api/.venv/bin/python scripts/install-sync-schedule.py --remove # 只移除定时器
```

凭据独立保存在Git忽略的本机0600文件，前端及Git中没有密码。机器睡眠/关机不会按时执行；恢复后当日漏点合并补跑。当前同步只覆盖已接入来源及批准窗口，未开发页面不会冒充同步完成；窗口外历史回补仍由受控任务安排。数仓backend资源拒绝时总预算退避，失败保旧版/时间与原因；定时到点不保证上游已有新日或已齐数。运行/验收见[架构](docs/architecture.md#sync-trigger-policy)、[验证](docs/validation.md#project-sync-m2-20261028)。

新试行评分独立入口为`PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.jobs.score_product_board`，只读本地输入并发布参考评分，不连接上游。模型材料/算法及最小权限设计见[说明](docs/m1-model-reuse.md)；原参数与新试行参照分开追溯，不复用旧经营数字。

## M5售后分析入口

`/aftersales`使用共享顶部/左侧目录，原售后筛选移到正文。报告、九指标、折叠整改/阶段、两图和六分析表只经`/api/v1/aftersales`读取本地固定版本；数据来自允许的JST加工必要投影。当前观察量与未核金额分开，A/缺sfje依赖率、分类/词典及物流阶段有明确门禁，不以旧报告数字补值。详见[运行契约](docs/data-contract.md#m5-runtime-contract)、[统一问题](docs/issues.md#m5-售后分析)。

项目每日10/14/18点编排已包括`jst_returns`、`jst_shipped_sales`、`jst_cancelled_sales`及依赖成功后的`aftersales_report`；单次手动可用`sync-project.py --run-now --only`选择它们。报告固定当前/前一期滚动30日和覆盖完整的上个自然月，默认最新、自选保留；没有真实去年输入则同比不可比。06仅追加既有三表的最小运行权限，受控本机升级入口为`scripts/upgrade-aftersales-local.py`，先执行`db-v2.py verify`；无新增数据表或服务启动DDL。

## 运营中心与天气入口

中心`/`按sites-current正文保留摘要、四域卡片、预警、本日动作、天气和口径说明；共享顶栏统一五页入口及当前页状态，左侧继续使用共享目录。中心直接调用已接通的四域API，不复制业务计算或建立摘要快照。各域观察日、完整日、来源失败与查询失败分开展示；计数和规则缺口见[统一问题台账](docs/issues.md#m6-运营中心)。

天气为`/api/v1/weather/meta`及`/api/v1/weather/publication`，只读本地不可变发布。独立同步步骤`weather`只读NMC官网公报和中期预报，加入现有每日10/14/18入口；不读取群消息、不发送天气卡，不在页面刷新时拉上游。历史归档从本次真实接入起保存，两份源分别显示实际发布时间，未识别气温标待核。数据/接口唯一见[运行契约](docs/data-contract.md#m6-runtime-contract)，07结构/回退见[专用设计](docs/m6-overview-implementation.md)。本机06→07用`scripts/upgrade-weather-local.py`受控升级，不能用旧迁移重建项目库。
