# 开发与运行

本文件维护可执行入口与环境边界；当前业务进度见[TODO](../TODO.md)，技术原理见[架构](architecture.md)，验收证据见[验证](validation.md)。所有命令从项目根目录运行。

## 环境与应用

安装与启动见[README](../README.md#本地运行)。可将 `apps/api/.env.example` 复制为同目录 `.env`；本机 Docker 初始化也会生成应用只读连接，已有配置不覆盖。数仓连接与应用数据库连接是不同资源。

- `npm run dev`：同时启动 Vite 和 API；也可用 `dev:web`、`dev:api` 单独启动。
- `/health/live` 检查进程；`/health/ready` 检查应用库可连接。未配置数据库时就绪 503 是预期，健康检查不等于经营验收。
- Node 与 Python 使用项目锁文件；后端从 `requirements-dev.in` 在独立环境解析更新 `requirements-dev.lock`，不只改输入文件。

## 本地 PostgreSQL

沿用现有 PG17 容器、`pdd_operations` 项目库和独立 v2 迁移链。API 使用只读 `pdd_app`，接收使用 `pdd_ingest`，结构迁移由 `pdd_admin` 执行。

```bash
npm run db:status  # 查看项目容器
npm run db:up      # 启动/初始化本地容器和角色，不迁移、不接数
```

连接为 `127.0.0.1:55432`。私密配置在 Git 忽略的 `var/local-db/docker.env`、`sync.env` 与 `apps/api/.env`，新建权限为 0600。容器项目为 `pdd-operations-platform`，持久卷为 `pdd-operations-platform_postgres_data`；重启保留数据，不执行 `down -v`。

本机 DBX 保留“拼多多运营平台 · 本地管理”连接及原有授权，连接 `pdd_operations → public → 表`。它是人工管理入口；正式结构演进仍通过 Alembic，API 不使用管理账号。既有数仓连接不改动。

### 迁移与升级

现行设计入口是[数据库字典](database-schema/README.md)和[迁移方案](database-schema/migration-plan.md)。已执行 revision 不改写，派生 SQL 只供审阅，不直接执行。

```bash
apps/api/.venv/bin/python scripts/db-v2.py verify
```

`verify` 新建、验证并清理一次性隔离 PG 库，项目库只读检查。`db-v2.py create` 仅用于已验证的初始空库，当前已有业务库不可重复创建。后续升级按对应专用设计使用 `upgrade-*-local.py`，先核对当前 revision、验证及备份条件，不批量运行全部升级脚本。

旧 M1 应用、专属测试及默认 Alembic 配置已移除；`apps/api/migrations/versions/` 仅保留旧 revision 历史，不是可运行迁移链。旧 `db:upgrade`、`profiles:m1`、`scores:m1` 已撤除。v2 迁移在线路径只接受显式受控连接，不读取通用 `DATABASE_URL`，服务启动不建表。公司环境的迁移入口和权限需另行验收。

## 同步与评分

配置唯一入口为 [config/sync.toml](../config/sync.toml)，时区为 Asia/Shanghai。独立 launchd 每分钟检查，每日 10:00、14:00、18:00 串行接收已接入来源；API 和页面刷新只查本地发布结果。

```bash
apps/api/.venv/bin/python scripts/sync-project.py --status
apps/api/.venv/bin/python scripts/sync-project.py --run-now
apps/api/.venv/bin/python scripts/sync-project.py --set-times 10:00 14:00 18:00
apps/api/.venv/bin/python scripts/install-sync-schedule.py
apps/api/.venv/bin/python scripts/install-sync-schedule.py --remove
```

前两条分别查看状态和按现有范围补跑，后三条调整钟点、安装/重载、移除本机定时器。只选择部分步骤时用 `--run-now --only`，合法值以脚本 `--help` 和项目步骤配置为准。不要同时开多套编排。

机器睡眠/关机不能按时运行；恢复后当日漏点合并补跑，跨日自动补跑未启用。来源失败保留旧成功版并显示原因；窗口外回补、来源完整性及公司调度不因本机运行而视为完成。页面每 45 秒及聚焦时检查本地元信息，默认日期推进、自选范围保留。

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
