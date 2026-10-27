# 拼多多运营平台

公司内部拼多多运营看板的独立重构项目。采用React/TypeScript前端、FastAPI后端和PostgreSQL查询库。当前模块进度与授权统一见[TODO](TODO.md)，实际验证见[验证记录](docs/validation.md)。

**数据库采用独立v2迁移链。** 新设计及[实施方案](docs/database-schema/migration-plan.md)覆盖30张业务表；实际执行进度见[TODO](TODO.md#当前任务与范围)，证据见[验证记录](docs/validation.md#database-v2-local-created)。旧M1模型/迁移保留为历史实现，旧接数、解析、评分、真实页面查询仍暂停，不运行旧`db:upgrade`或默认`alembic upgrade head`。任何结构变更先改migration并说明原因，schema.sql仅为派生审阅稿；已执行revision不改写，未来追加新revision。

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

| 入口         | 地址 / 行为                                                                              |
| ------------ | ---------------------------------------------------------------------------------------- |
| 前端         | http://127.0.0.1:5173                                                                    |
| API / 文档   | http://127.0.0.1:8000 / http://127.0.0.1:8000/docs                                       |
| M3店铺销售   | http://127.0.0.1:5173/sales ，`/store-sales`同页；首条真实店铺日链路，其他来源明确待接入 |
| 存活 / 就绪  | `/health/live` / `/health/ready`；数据库未配置时就绪503是预期，不能冒充业务正常          |
| M1真实商品页 | `/products`，代码保留；数据库重设计期间真实数据查询暂停                                  |
| M1开发样例   | `/products?preview=1`，仅DEV合成样例，持续标识非真实经营数据，不接数仓/数据库/AI         |

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

以下M1内容仅描述旧代码的实现和历史运行方式，不与当前v2结构兼容，不代表功能可用。

旧M1三来源手工接入入口（重新设计期间暂停）：

```bash
WAREHOUSE_HOST=<数仓主机> WAREHOUSE_PORT=9030 WAREHOUSE_USER=ro npm run sync:m1
```

终端隐藏输入数仓密码，本入口不保存；不要将密码写命令行、前端环境变量或仓库。任务仅取契约3.2批准的三表、六店day、最近60日及去年对应期和当前必要资料，单连接串行读取并复核投影。它在本地以独立`pdd_ingest`角色写库，私有随机密码保存在Git忽略的权限600文件`var/local-db/sync.env`；API账号仍只读。无定时任务、飞书重复接入、文件快照导出或自动删源；失败保留当前有效数据，缺旧键/身份迁移须对账后处理。完整日未知不会因入库变成已确认；M1经营GET已可查真实PG，前端`/products`已默认读取真实API。

旧M1商品资料解析与经营接口入口（重新设计期间暂停）：

```bash
npm run profiles:m1 # 仅解析已入PG资料并发布，不访问数仓
npm run scores:m1   # 使用已入库标准重算新品评分，不访问数仓
npm run dev:api     # 本机API及 http://127.0.0.1:8000/docs
```

已实现GET `/api/v1/products/filters`、`overview`、`trend`、`brief`、`alerts`、`diagnosis`、`days`。例如`/api/v1/products/overview?date_from=2026-09-23&date_to=2026-10-22`；真实返回`data/meta`，字段使用snake_case，金额和比率为十进制字符串。接口文档：[Swagger](http://127.0.0.1:8000/docs)、[ReDoc](http://127.0.0.1:8000/redoc)、[OpenAPI JSON](http://127.0.0.1:8000/openapi.json)。九个GET均有参数、成功响应模型（含十进制字符串/空值/评分分项）、统一错误及request_id；口径与范围仍以[契约第7节](docs/data-contract.md#7-前后端查询接口)为准。来源手工任务成功后会衔接本地资料解析；两阶段间无相容资料时查询明确503，不混用旧资料；版本改变返回409整组重取。没有在API请求中同步数仓。

新品评分GET为`/api/v1/products/scores`、`/api/v1/products/scores/{score_id}`，独立选项为`/api/v1/products/filters?scope=scores`。评分任务使用独立写入角色，从PG分别聚合商品和推广；记录模型/输入版本及十项证据。GET不重算；输入或标准变化时返回待计算，旧详情/旧评分版本返回409。标准存于PG的model_versions，不依赖旧页面分数或经营文件快照；新标准可经`npm run scores:m1 -- --standards-file <经核对的标准参数文件绝对路径> --import-only`追加，文件不能含凭据或经营快照且不提交。来源和适用范围必须核实，不能用本命令把试算升级成业务验收。

当前已固化的线上显示四线为原商品身份及原品类绑定的固定试算基线，精度与历史生成缺口明确保留；收藏定义、完整日、推广归因说明未补齐时不伪造完整分或正式放量建议。评分重算需要显式后台任务；原值同步后至相容评分重算完成前标pending。详细返回及限制见契约3.5.1与7.5；前端已接入真实API，定时调度后续实施。

只看商品前端可运行`npm run dev:web`。前端开发代理`/api`和`/health`，浏览器无需数据库地址。`/products`默认真实模式，失败明确报错并重试，不回退经营样例；开发模式可显式`?preview=1`或`?data=offline`。三套筛选独立，评分绑定自身结果版本；目录只取分页候选，搜索可定位未在前100项的商品。金额/比率保持原串，展示舍入用BigInt，只有图形坐标近似为Number，提示仍读原串。只读实机联调可显式执行`VITE_M1_LIVE_TEST=1 npm exec --workspace @pdd/web -- vitest run src/features/products/api/live.test.ts`，需本机API已启动；测试不写业务库。

## 检查与版本保存

M3首条本地链路的三GET为`/api/v1/sales/filters`、`performance`、`shops`，由前端固定版本读取，GET不会访问来源或启动同步。手工接收入口为`apps/api/.venv/bin/python scripts/sync-sales.py --from YYYY-MM-DD --to YYYY-MM-DD --verify-only`；正式发布显式改为`--publish`，必须是完整批准六店窗口，不能用局部窗口覆盖历史。数仓密码隐藏输入；API仍只读，独立任务使用既有本地写入身份。批准范围、单位与完整性限制见[契约](docs/data-contract.md#m3-source-access)，权限升级/回退见[迁移方案](docs/database-schema/migration-plan.md)，源投影→PG→API只读复核用`apps/api/.venv/bin/python scripts/verify-sales-local.py`，须先启动本机API。必要输入、生产者源码及SHA归档在Git忽略的`var/ingestion/shop_daily/`，不自动清理；重放不执行归档代码，解释器不相容时拒绝，需使用归档的对应解释版本单独复核。

代码检查入口：`npm run check`、`npm test`、`npm run build`、`npm run format:check`。仅文档改动按AGENTS检查文档，不重复声称历史测试是本轮通过。

v2本轮受控入口为`apps/api/.venv/bin/python scripts/db-v2.py verify`（仅新建/清理本轮隔离库并验证，项目只读）和`create`（仅允许已验证的初始空库；当前已创建后会拒绝重复覆盖）。入口不读取通用DATABASE_URL；不直接运行schema.sql，不开放旧M1接数或同步。后续结构变更追加Alembic revision及实施/验证说明。

Python和前端都有锁文件；后端依赖从`requirements-dev.in`在独立环境解析，不只改输入。正式数据表变更走Alembic，仅按已批准范围创建业务迁移；业务待决口径不在建表时默认确认。

目标GitHub仓库为私有`sunny-orb/pdd-operations-platform`；初始化推送是历史记录，当前本地提交与远端同步需分别实查。凭据、经营数据、备份、模型及日志不提交，生产发布需要明确授权。
