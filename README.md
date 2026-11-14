# 拼多多运营平台

公司内部运营看板的独立重构项目：React / TypeScript / Vite 前端，FastAPI 后端，PostgreSQL 查询库，独立同步与评分任务。前端只通过 `/api/v1` 读取已发布数据。

## 本地运行

只做前端的同事请看[前端协作手册](docs/frontend-collaboration.md)，从拉代码、启动页面到提交 PR，无需安装后端环境。

要求 Node.js 22.13+（推荐 24 LTS）、Python 3.12+，本机 PostgreSQL 使用 Docker Desktop。

```bash
npm ci
python3.12 -m venv apps/api/.venv
apps/api/.venv/bin/python -m pip install -r apps/api/requirements-dev.lock
npm run dev
```

Windows 用 `py -3.12 -m venv apps/api/.venv` 创建环境，Python 路径为 `apps/api/.venv/Scripts/python.exe`。已有环境无需重新创建；数据库配置、迁移、同步与排障见[开发与运行](docs/development.md)。

| 页面                   | 本地地址                                                      |
| ---------------------- | ------------------------------------------------------------- |
| 运营中心               | <http://127.0.0.1:5173/>                                      |
| 商品数据（含新品评分） | <http://127.0.0.1:5173/products>                              |
| 推广分析               | <http://127.0.0.1:5173/promotion>（商品模式 `?mode=product`） |
| 店铺销售               | <http://127.0.0.1:5173/sales>（兼容 `/store-sales`）          |
| 售后分析               | <http://127.0.0.1:5173/aftersales>                            |
| API 文档               | <http://127.0.0.1:8000/docs>                                  |

真实接入情况与剩余验收只见[TODO](TODO.md)和[问题台账](docs/issues.md)，页面可打开不等于业务已验收。

## 项目目录

```text
apps/
  web/src/          # app 路由、features 业务页面、shared 公共能力
  api/
    app/            # api 协议、modules 领域、connectors 来源、jobs 任务
    migrations_v2/  # 现行 Alembic 迁移及隔离验证
    migrations/     # 旧 revision 历史，只读追溯
    tests/          # 现行单元、API 和真实 PostgreSQL 集成测试
config/             # 可追溯的业务与调度配置
scripts/            # 开发、受控升级、同步和本地核验入口
runtime/m1-v6/      # 评分参照算法及离线工具，不放经营输入
docs/              # 需求、契约、架构、实施设计和验收证据
var/                # 本地归档、日志与私密配置，Git 忽略
```

目录与模块职责详见[架构基线](docs/architecture.md)，命令分组见[脚本目录](scripts/README.md)。

## 从这里阅读

| 要了解什么                   | 唯一入口                                                                        |
| ---------------------------- | ------------------------------------------------------------------------------- |
| 当前进度与下一步             | [TODO.md](TODO.md)                                                              |
| 文档职责与阅读顺序           | [文档目录](docs/README.md)                                                      |
| 本机安装、数据库、同步、检查 | [开发与运行](docs/development.md)                                               |
| 五页范围、原型及交互         | [需求](docs/requirements.md)                                                    |
| 来源、粒度、指标和接口       | [数据契约](docs/data-contract.md)                                               |
| 分层、目录和全局运行边界     | [架构](docs/architecture.md)                                                    |
| 数据库设计流程与字段         | [设计流程](docs/database-design.md)、[字段字典](docs/database-schema/README.md) |
| 模块设计与平台接管           | [实施设计目录](docs/implementation/README.md)                                   |
| 已知问题、关闭条件           | [问题台账](docs/issues.md)                                                      |
| 验收标准与按日期记录的证据   | [验证与验收](docs/validation.md)                                                |
| 调查依据、历史取证           | [来源证据](docs/source-evidence.md)                                             |
| 协作、验证与提交规则         | [AGENTS.md](AGENTS.md)                                                          |

<a id="refactor-principles"></a>

## 重构原则

先保证数字和权限正确，再改善交互与维护方式；保留已确认五页能力，修正已知旧错误。未决项只限制依赖它的结果，缺失、真实零、失败与旧成功结果分别表达。问题依据见[重构清单](docs/refactor-checklist.md)，不在 README 重复维护。

## 源码与重构目录

线上最新源码已获取，为 2026-10-23 备份中的 Sites 最新保存版本；保存版本与生产部署分别核对。

| 用途                 | 本机目录                                                                                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 最新源码（只读参考） | [/Users/sunny/Projects/operations-dashboards-source-backup-20261023-155031/sites-current/](/Users/sunny/Projects/operations-dashboards-source-backup-20261023-155031/sites-current/) |
| 实际开发             | [/Users/sunny/Projects/operations-dashboards/pdd-operations-platform/](/Users/sunny/Projects/operations-dashboards/pdd-operations-platform/)                                         |

只按[模块源码地图](docs/requirements.md#11-本地源码参考地图)读取必要参考，不回读同级旧备份。来源范围与切源规则以数据契约为准。

私有 GitHub 仓库为 `sunny-orb/pdd-operations-platform`。凭据、经营数据、归档、备份、模型输入和日志不提交；生产发布与推送分别按明确授权执行。
