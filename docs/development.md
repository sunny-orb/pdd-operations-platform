# 开发与运行

本文件维护可执行入口与环境边界；当前业务进度见[TODO](../TODO.md)，技术原理见[架构](architecture.md)，验收证据见[验证](validation.md)。所有命令从项目根目录运行。

## 环境与应用

本机日常开发和验收统一使用README指定的实际开发目录及 `http://127.0.0.1:5173`，五个页面共用这一个入口。隔离目录可用于快照或临时验证；交付前须将本次改动合回实际开发目录、保留其他模块改动并验证原入口，再关闭本次临时服务，不能只交付另一个端口。

安装与启动见[README](../README.md#本地运行)。可将 `apps/api/.env.example` 复制为同目录 `.env`；本机 Docker 初始化也会生成应用只读连接，已有配置不覆盖。数仓连接与应用数据库连接是不同资源。

- `npm run dev`：同时启动 Vite 和 API；也可用 `dev:web`、`dev:api` 单独启动。
- `/health/live` 检查进程；`/health/ready` 检查应用库可连接。未配置数据库时就绪 503 是预期，健康检查不等于经营验收。
- Node 与 Python 使用项目锁文件；后端开发从 `requirements-dev.in` 解析 `requirements-dev.lock`；生产单独从 `requirements.in` 解析 `requirements.lock`，固定同版本公共依赖，不把浏览器或测试工具装入镜像。

本地API核验脚本使用Vite中间件实例时，配置会自动使用按进程隔离的`node_modules/.vite-verification/<pid>`缓存，避免覆盖5173页面服务的预构建依赖；调用方已显式指定缓存时沿用其目录，该目录也必须与页面服务隔离。额外启动普通浏览器测试服务也应指定独立`cacheDir`。如旧会话因缓存混用出现`Invalid hook call`白屏，先停止共用缓存的核验实例，重启前端（必要时在项目根目录执行`npm run dev --workspace @pdd/web -- --force`）并刷新页面；不要删除经营归档或重装依赖来处理。验证见跳转白屏修复（2026-11-10本地验收记录）。

### 只运行前端

默认不需要额外配置，`npm run dev` 会启动前后端，Vite 将 `/api` 转发到本机 `http://127.0.0.1:8000`。

只改前端的同事安装项目要求的 Node.js 后，在项目根目录执行 `npm ci`，再创建 `apps/web/.env.development.local`，填入已获准访问的生产 API 域名：

```dotenv
PDD_DEV_API_TARGET=https://dashboard.example.com
```

用实际的 HTTPS 域名替换示例。地址只填协议和域名（如有非默认端口也填上）；前端会自行请求 `/api/v1/...`。然后在项目根目录运行 `npm run dev:web`，打开 <http://127.0.0.1:5173>。不需要安装 Python、启动 FastAPI 或配置数据库。代理沿用线上 API 的访问控制。

该文件已被 Git 忽略；配置中只填写地址，不写账号或密码。修改配置后重启 Vite。此配置只影响开发服务器的 `/api` 代理，`/health` 仍指向本机后端，构建和部署不使用该地址。

<a id="ai-runtime"></a>

### 本机 AI 问答配置

本机和生产推荐使用 DeepSeek 官方快速模型，关闭深度思考。设置 `apps/api/.env` 后重启 API，右侧输入框可直接提问。[设置定义](../apps/api/app/core/config.py) · [无密钥样例](../apps/api/.env.example)

| 环境变量                                                          | 默认值／推荐配置                                      | 用途                                                 |
| ----------------------------------------------------------------- | ----------------------------------------------------- | ---------------------------------------------------- |
| `APP_ENV`、`CORS_ORIGINS`                                         | development，本机 localhost 和 127.0.0.1 的 5173 端口 | 本机开发访问。生产环境助手入口关闭                   |
| `ASSISTANT_MODEL`                                                 | `deepseek:deepseek-flash`（样例）                     | 官方快速型号。代码保留原百炼型号兼容，始终只启用一个 |
| `ASSISTANT_BUSINESS_DATA_ENABLED`、`ASSISTANT_BUSINESS_PROVIDERS` | false、`[]`                                           | 发送获准数据时分别设为 true 和 `["deepseek"]`        |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`                           | 空密钥，`https://api.deepseek.com`                    | DeepSeek 官方密钥和入口，仅由后端读取                |
| `DATABASE_URL`                                                    | 空                                                    | 经营数据来自已初始化的应用 PostgreSQL                |
| `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_CONNECT_TIMEOUT_SECONDS`   | 代码默认 8 / 4 / 5 秒，样例 4 / 0 / 5 秒              | 连接池、溢出连接和连接等待                           |
| `CONTENT_REUSE_ENABLED`                                           | false                                                 | 独立的数据库试验开关，问答沿用关闭值                 |

在仓库根目录运行 `npm run dev`。用 `GET /api/v1/assistant/models` 核对 DeepSeek 和业务数据开关，再用一条获准的问题验证。官方请求设 temperature=0.3、`thinking={"type":"disabled"}`，沿用供应商的输出长度默认值。兼容旧百炼配置时仍用 `BAILIAN_API_KEY` / `BAILIAN_BASE_URL`、供应商 `bailian` 和 `enable_thinking=false`，两家密钥不互相回退。切换后重启 API；旧密钥从运行配置移除。

按响应头 `X-Request-ID` 在 Uvicorn 日志关联 `assistant_model`（每次模型往返耗时）、`assistant_tool`（已执行的工具名和耗时）与 `assistant_result`（本轮模型请求／工具调用、领域读取次数和耗时、SQL 累计耗时、首条正文增量及总耗时）。`first_delta_ms` 从后端开始执行问答计时，浏览器绘制另测；JSON 请求这一项为空。日志仅记摘要。

每进程一个查询线程，数据库单语句期限 5 秒。生成预算和锁等待由代码设置，数值见[接口说明](api/ai-assistant.md)。Docker、HTTPS 和发布操作见[部署手册](implementation/deployment.md)。

生产沿用网站匿名访问决定。启用 `ASSISTANT_PRODUCTION_ENABLED=true`、`ASSISTANT_BUSINESS_DATA_ENABLED=true` 与获准供应商 `["deepseek"]`，在权限600的 `.env.runtime` 填入 `DEEPSEEK_API_KEY`。正式域名须在 `CORS_ORIGINS`。通过 `ASSISTANT_MAX_CONCURRENT=2`、`ASSISTANT_REQUESTS_PER_MINUTE=20` 限制请求；查询仍仅用一个后台线程，模型请求90秒超时。关闭生产开关即可停用模型问答。此方式适用现有公开访问范围，接入按账号区分的店铺权限需另行实施。

## 本地 PostgreSQL

沿用现有 PG17 容器、`pdd_operations` 项目库和独立 v2 迁移链。本机默认API 使用只读 `pdd_app`，接收使用 `pdd_ingest`，结构迁移由 `pdd_admin` 执行。公司部署的账号选择按[架构中的用户决定](architecture.md)：允许使用songbing及其现有读写权限；连接配置及独立初始化入口已按[部署方案](implementation/deployment.md)适配，本轮仅本地验证。

```bash
npm run db:status  # 查看项目容器
npm run db:up      # 启动/初始化本地容器和角色，不迁移、不接数
```

连接为 `127.0.0.1:55432`。私密配置在 Git 忽略的 `var/local-db/docker.env`、`sync.env` 与 `apps/api/.env`，新建权限为 0600。容器项目为 `pdd-operations-platform`，持久卷为 `pdd-operations-platform_postgres_data`；重启保留数据，不执行 `down -v`。

本机 DBX 保留“拼多多运营平台 · 本地管理”连接及原有授权，连接 `pdd_operations → public → 表`。它是人工管理入口；正式结构演进仍通过 Alembic，API 不使用管理账号。既有数仓连接不改动。

### 迁移与升级

现行设计入口是[数据库字典](database-schema/README.md)和[迁移方案](database-schema/migration-plan.md)。已执行 revision 不改写。`schema.sql`统一包含当前完整迁移，**未部署的新空库按[首次初始化](database-schema/migration-plan.md#current-schema-delivery)一次执行**；分段SQL仅供审阅，不重复拼接。`npm run db:schema`离线生成，`npm run check`自动核对是否与迁移一致。

```bash
npm run check:db-schema
# 仅在已配置本机PG17的开发环境执行；创建并清理随机隔离测试库
(cd apps/api && .venv/bin/python -m pytest -q tests/test_schema_delivery.py)
```

历史入口`db-v2.py`仍固定09，不用于当前12首次部署。`verify` 新建、验证并清理一次性隔离 PG 库，项目库只读检查。`db-v2.py create` 仅用于已验证的初始空库，当前已有业务库不可重复创建。后续升级按对应专用设计使用 `upgrade-*-local.py`，先核对当前 revision、验证及备份条件，不批量运行全部升级脚本。

来源更新状态沿用revision12的检查和接收字段。当前固定数仓调度需要revision `20261111_15` 的每周对账状态表。新空库执行完整初始化SQL，已有库按实际revision增量升级，详见[对账升级与回退说明](database-schema/weekly-reconciliation.md)。有运行记录的状态表拒绝降级删除。

旧 M1 应用、专属测试及默认 Alembic 配置已移除；`apps/api/migrations/versions/` 仅保留旧 revision 历史，不是可运行迁移链。旧 `db:upgrade`、`profiles:m1`、`scores:m1` 已撤除。v2 迁移在线路径只接受显式受控连接，不读取通用 `DATABASE_URL`，服务启动不建表。公司环境的迁移入口和权限需另行验收。

## 同步与评分

配置唯一入口为[config/sync.toml](../config/sync.toml)，时区Asia/Shanghai，独立launchd每分钟重新读取。source_schedule_enabled=true时，warehouse_times指定8个迁移数仓来源的7个时点，feishu_times指定两张飞书表的2个时点；同槽按源持久去重。每周日按 `reconciliation_time` 进行获准范围对账，失败源按 `reconciliation_retry_seconds` 从结束时刻等待后重试。原times和monitor_interval_seconds仅继续用于聚水潭4来源、天气2来源；API和页面刷新不访问上游。开关未启用时维持原全项目行为，实际启用进度见TODO。

固定策略直接通过HTTP读取数仓表的dataLastUpdateTime，后台和生产不需要浏览器。已有专用登录文件可直接复用。只有首次或登录失效时，在本机安装Google Chrome及开发依赖，通过专用窗口完成既有飞书登录；程序不读取个人Chrome资料、不记录密码：

```bash
apps/api/.venv/bin/python scripts/configure-warehouse-browser.py --login
apps/api/.venv/bin/python scripts/configure-warehouse-browser.py --check
```

状态文件为`var/local-db/warehouse-browser/state.json`（0600，父目录0700，Git忽略），沿用历史路径，不表示生产安装了浏览器。`--check`串行HTTP读取8表，完全不启动浏览器，不启用计划或接收经营数据。生产仅安装`requirements.lock`，Playwright只在开发依赖；浏览器登录模块也排除在生产镜像之外。认证失效保留旧观测和可用数据，按原5分钟规则重查；用户在本机续登后仅更新获准项目内的认证文件。不会自动回退SQL账号或复制个人浏览器Cookie。

启用前迁移到当前head，完成真实网页读取、实际接收及无变化不下载验证。`source_schedule_enabled=true`切换8数仓和2飞书，两个旧自动路径只保留聚水潭4来源和天气2来源。--set-times只改旧时点并保留新配置；手动--run-now/--only仍走同一来源状态机，不消费固定槽或每周对账周期。Playwright和Chrome为网页适配器的运行依赖，未来平台可注入另一个warehouse_reader，不需改调度策略。

```bash
apps/api/.venv/bin/python scripts/sync-project.py --status
apps/api/.venv/bin/python scripts/sync-project.py --run-now
apps/api/.venv/bin/python scripts/sync-project.py --check-updates
apps/api/.venv/bin/python scripts/sync-project.py --set-times 10:00 10:30 11:00
apps/api/.venv/bin/python scripts/install-sync-schedule.py
apps/api/.venv/bin/python scripts/install-sync-schedule.py --remove
```

以上依次查看状态、按现有范围补跑、检查到期来源并按需接收、调整钟点、安装/重载、移除本机定时器。`--check-updates`仍尊重检查间隔和项目锁，不是强制全量刷新；检查／接收／重算失败会非零退出。只选择部分步骤时用 `--run-now --only`，合法值以脚本 `--help` 和项目步骤配置为准。不要同时开多套编排。

修改钟点后下次分钟检查即生效，无需重启 API 或重新安装定时器；当日已过的多个时点只合并补跑最近一轮，不追补全部漏点。机器睡眠/关机不能按时运行，跨日自动补跑未启用。来源失败保留旧成功版并显示原因；调整时点不证明已修复来源读取错误。窗口外回补、来源完整性及公司调度不因本机运行而视为完成。页面每 45 秒及聚焦时检查本地元信息，默认日期推进、自选范围保留。

来源监测开关／间隔同样热读取。数据更新状态弹窗单独通过`GET /api/v1/data-status`每15秒读取平台摘要，仅打开且页面可见时运行；关闭停止。它不执行上游查询或同步命令。部署服务器需运行同一独立调度入口，不能仅启动API；运行约束见[监测设计](implementation/project-sync-implementation.md#source-update-monitor)。

商品独立入口：

```bash
npm run sync:m1 -- --verify-only
npm run sync:m1 -- --publish
PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.jobs.score_product_board
```

`sync:m1` 指向现行 `sync_product_board`，分别验证必要投影或受控发布；遵守既有三来源、六店、获准日期范围，不扩大数仓权限。评分只读本地相容输入，发布参考部分分；正式放量保持关闭。评分材料及限制见[模型说明](implementation/m1-model-reuse.md)。

仅修改资料解析规则、需要复算当前本地关系时运行：

```bash
apps/api/.venv/bin/python scripts/rebuild-product-profiles.py --publish
```

该入口固定三个当前来源版本，仅发布`profile_links`及相容试行评分，不访问上游、不重写事实或来源接收时间。与项目同步共用互斥；失败保留旧头，修复后重跑幂等。不是来源同步，也不替代来源完整性验证。

来源凭据延迟读取且不写命令行。报告、评分、归档与失败恢复共用现有执行链。平台适配调用 `app.jobs.project_runtime.execute_project`，接口、整功能迁移和回退见[平台接管](implementation/sync-platform-handoff.md)。

## 检查与验证

```bash
npm run check
npm test
npm run build
npm run format:check
```

后端现行集成测试自行新建/清理随机命名的 v2 隔离 PostgreSQL 库，需本机 Docker PG17 及既有受管配置；不会用 SQLite 替代。`npm run db:test` 是同一前后端测试套件的便捷入口，不再向旧 `pdd_operations_test` 注入旧迁移连接。迁移的独立检查另用 `db-v2.py verify`。

`scripts/verify-*-local.*` 是真实本地资料的受控业务核验工具，可能依赖私有归档和当前版本；按模块设计选择使用，不作为通用安装步骤，也不以健康检查替代对账。页面交互变化仍需浏览器核验，发布前按[验收规范](validation.md)检查。

## 本地文件保留

`var/`、`.env`、依赖环境、经营输入、模型材料、归档和日志保持 Git 忽略。本轮整理不删除业务库、持久卷、原始输入或运行凭据；保留策略批准前不启用自动清理。五页来源、金额、版本与未决口径统一见[数据契约](data-contract.md)，不在运行手册另维护一套指标定义。

### 定时任务资源保护

本机`scripts/sync-project.py --scheduled`与容器Worker共用`config/worker.toml`整轮监督：默认900秒，终止宽限10秒，按本轮新进程组回收。手工单次导入命令保持原行为；不得通过设置内部监督标记绕过本机定时入口。

归档自动维护在取数之后，参数见`config/archive.toml`；每小时一次、20秒、最多2文件、4MiB/s及500个新目录项/s。根内`.maintenance.json`只有冷却和目录栈，可跨重启继续；旧输入按原格式读取。维护budget表示保留输入并等待下次，不代表数据同步失败或回收成功。完整GC不能在预算内验证时保留块，绝不为腾空间降低历史保护。
