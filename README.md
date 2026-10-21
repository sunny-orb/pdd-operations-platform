# 拼多多运营平台

公司内部运营看板的独立重构项目。当前是可运行的前后端初始化骨架，尚未接入公司数据、认证或数据库实例。

## 入口

- [TODO.md](TODO.md)：阶段顺序、待核实问题和验收条件。
- [docs/architecture.md](docs/architecture.md)：技术栈、模块边界、同步发布、持久化与部署决定。
- [docs/data-contract.md](docs/data-contract.md)：字段和指标契约的填写模板。
- [AGENTS.md](AGENTS.md)：Codex 在本仓库中工作的长期约定。

## 本地启动

要求 Node.js 22.13+（推荐 24 LTS）、Python 3.12+。初始服务不要求 PostgreSQL 已启动；数据库未配置时 readiness 返回 503，页面明确显示未接入。

```bash
npm ci
python3.12 -m venv apps/api/.venv
apps/api/.venv/bin/python -m pip install -r apps/api/requirements-dev.lock
npm run dev
```

Windows 使用 `py -3.12 -m venv apps/api/.venv` 和 `apps/api/.venv/Scripts/python.exe`。如果本机 Python 命令不同，请替换创建环境的命令；根目录启动脚本会优先使用上述项目环境。

- 前端：http://127.0.0.1:5173
- API：http://127.0.0.1:8000
- 接口文档：http://127.0.0.1:8000/docs
- 存活检查：`/health/live`；数据库就绪检查：`/health/ready`。
- 开发前端通过 Vite 将 `/api` 和 `/health` 代理到 API；浏览器无需知道数据库地址。

可选配置：把 `apps/api/.env.example` 复制成同目录 `.env`，填写应用数据库连接。不要填写或提交生产凭据。公司数仓只读连接与应用 PostgreSQL 是不同资源；本次没有配置上游连接。

## 验证

```bash
npm run check
npm test
npm run build
npm run format:check
```

Python 与前端依赖均提供锁文件。更新后端依赖时，在独立环境由 `requirements-dev.in` 重新解析并更新锁文件，不能仅改输入文件。前端由 `package-lock.json` 锁定。

本次检查是初始化及接口契约验证；没有真实 PostgreSQL 或上游数据时，不得声称数据库、同步或业务指标验收通过。Alembic 已配置，但第一份业务迁移要等数据契约确认后创建。

## GitHub

目标仓库：`sunny-orb/pdd-operations-platform`，私有。本机已通过现有 SSH 身份验证并设置远程；推送状态以 `git status -sb` 和远程分支为准。

如尚未设置远程，创建空的私有仓库后执行：

```bash
git remote add origin git@github.com:sunny-orb/pdd-operations-platform.git
git push -u origin main
```

使用 GitHub 官方登录、SSH 或本机凭据管理器完成认证，不把令牌写进 URL、源码或文档。不复制旧项目的业务数据和模型文件到此仓库。

## 开新对话

在 Codex 中把这个目录作为项目打开后新建对话；仓库根目录的 `AGENTS.md` 会作为适用项目指令被发现。`TODO.md` 不会仅凭文件名自动执行，本项目已在 `AGENTS.md` 要求先读取它。新对话仍以本轮用户任务为执行范围。
