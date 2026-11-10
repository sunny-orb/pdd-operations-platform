# 开发与运行

本文件维护可执行入口与环境边界；当前业务进度见[TODO](../TODO.md)，技术原理见[架构](architecture.md)，验收证据见[验证](validation.md)。所有命令从项目根目录运行。

## 环境与应用

本机日常开发和验收统一使用README指定的实际开发目录及 `http://127.0.0.1:5173`，五个页面共用这一个入口。隔离目录可用于快照或临时验证；交付前须将本次改动合回实际开发目录、保留其他模块改动并验证原入口，再关闭本次临时服务，不能只交付另一个端口。

安装与启动见[README](../README.md#本地运行)。可将 `apps/api/.env.example` 复制为同目录 `.env`；本机 Docker 初始化也会生成应用只读连接，已有配置不覆盖。数仓连接与应用数据库连接是不同资源。

- `npm run dev`：同时启动 Vite 和 API；也可用 `dev:web`、`dev:api` 单独启动。
- `/health/live` 检查进程；`/health/ready` 检查应用库可连接。未配置数据库时就绪 503 是预期，健康检查不等于经营验收。
- Node 与 Python 使用项目锁文件；后端从 `requirements-dev.in` 在独立环境解析更新 `requirements-dev.lock`，不只改输入文件。

本地API核验脚本使用Vite中间件实例时，配置会自动使用按进程隔离的`node_modules/.vite-verification/<pid>`缓存，避免覆盖5173页面服务的预构建依赖；调用方已显式指定缓存时沿用其目录，该目录也必须与页面服务隔离。额外启动普通浏览器测试服务也应指定独立`cacheDir`。如旧会话因缓存混用出现`Invalid hook call`白屏，先停止共用缓存的核验实例，重启前端（必要时在项目根目录执行`npm run dev --workspace @pdd/web -- --force`）并刷新页面；不要删除经营归档或重装依赖来处理。验证见跳转白屏修复（2026-11-10本地验收记录）。

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

本版本的来源更新状态与监测要求数据库结构为revision `20261104_12`，包含10的状态表、11的时间字段和12的来源调度字段；已到此版本无须重复迁移，新空库执行完整初始化SQL，只有旧库需要增量升级；具体操作见[专用升级／回退说明](database-schema/source-checkpoints.md)。新增表只存检查和版本日期摘要，保留现有业务表。旧升级脚本的目标revision不可直接替代本次迁移，已有数据的状态表不允许自动降级删除。

旧 M1 应用、专属测试及默认 Alembic 配置已移除；`apps/api/migrations/versions/` 仅保留旧 revision 历史，不是可运行迁移链。旧 `db:upgrade`、`profiles:m1`、`scores:m1` 已撤除。v2 迁移在线路径只接受显式受控连接，不读取通用 `DATABASE_URL`，服务启动不建表。公司环境的迁移入口和权限需另行验收。

## 同步与评分

配置唯一入口为[config/sync.toml](../config/sync.toml)，时区Asia/Shanghai，独立launchd每分钟重新读取。source_schedule_enabled=true时，warehouse_times指定8个迁移数仓来源的7个时点，feishu_times指定两张飞书表的2个时点；同槽按源持久去重。原times和monitor_interval_seconds仅继续用于聚水潭4来源、天气2来源；API和页面刷新不访问上游。开关未启用时维持原全项目行为，实际启用进度见TODO。

固定策略使用独立网页读取连接器，需要本机安装Google Chrome及锁定Python依赖。先运行专用登录入口，在新浏览器窗口完成既有飞书登录；程序只保存该专用会话的数仓域登录状态，不读取个人Chrome资料，不记录密码：

```bash
apps/api/.venv/bin/python scripts/configure-warehouse-browser.py --login
apps/api/.venv/bin/python scripts/configure-warehouse-browser.py --check
```

状态文件为`var/local-db/warehouse-browser/state.json`（0600，父目录0700，Git忽略）。--check使用新的无界面浏览器逐表读取8个固定网页，成功不代表经营事实已齐；两命令都不启用计划、不下载经营数据。后台仅到期才启动浏览器，一轮复用、串行读取，结束后关闭；每次重新加载目标文档，不能复用hash切表前的旧时间。登录失效本轮停止重复登录，平台显示检查失败并保留旧数据；用户重跑--login后下个固定槽继续。不会自动回退到后台账号或复制个人浏览器Cookie。

启用前迁移到revision12，完成真实网页读取、实际接收及无变化不下载验证。`source_schedule_enabled=true`切换8数仓和2飞书，两个旧自动路径只保留聚水潭4来源和天气2来源。--set-times只改旧时点并保留新配置；手动--run-now/--only仍走同一来源状态机，不消费固定槽。Playwright和Chrome为网页适配器的运行依赖，未来平台可注入另一个warehouse_reader，不需改调度策略。

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
