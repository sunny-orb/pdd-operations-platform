# 香草的前端协作手册

香草在自己电脑上运行前端，通过生产 API 查看真实数据。松饼 负责后端、数据、审核合并和上线。两人共用一个私有仓库，各自用自己的 GitHub 账号和开发分支。

## 目录

1. [分工与修改范围](#roles)
2. [首次拉代码并启动](#setup)
3. [页面用哪些 API](#api)
4. [日常同步与开发](#daily)
5. [验证、提交和提 PR](#pr)
6. [直接给 Codex 的话](#prompt)

<a id="roles"></a>

## 1. 分工与修改范围

| 谁处理             | 具体范围                                                                                                                                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **香草可以直接改** | 当前需求对应页面的排版、间距、颜色、响应式布局、图表样式、表格展示、提示和交互，以及相关前端测试。调用已有接口，沿用已有筛选含义和指标口径。                                                                                   |
| **香草先问 松饼**  | 公共导航、主题或多个页面共用的组件；新增页面或数据需求；默认日期、筛选含义、统计范围、金额与比率的含义；API 参数或响应契约、刷新频率、缓存和重试；安装依赖、修改 Vite 或构建配置。说明想达到的效果、涉及页面和缺少的数据即可。 |
| **松饼 负责**      | API、业务公式、数据库、迁移、上游接数、Worker、评分和报告计算、服务器和访问权限、CI/CD、PR 合并与发布。负责核对接口和数据，验收页面效果。                                                                                      |

香草的日常修改范围是 `apps/web/src/features/` 对应页面和相关前端测试。公共代码位于 `apps/web/src/shared/`，跨页面修改先对齐。后端代码可以阅读，改动交给 松饼。前端代码使用真实 API 时，请求会产生线上负载，沿用现有分页、取消请求及刷新机制，禁止批量拉全库或压力测试。

个人配置、密码、令牌、真实数据、接口响应、日志、数据库备份、`node_modules/` 和 `dist/` 都不提交。PR 截图先遮盖敏感业务信息。仓库写权限保留，这些约定需要香草和 Codex 共同遵守，文档本身不会强制限制权限。

<a id="setup"></a>

## 2. 首次拉代码并启动

**准备**：接受[仓库邀请并确认能访问项目](https://github.com/sunny-orb/pdd-operations-platform)，安装 Git、Node.js 24 LTS 和 Codex。可让 Codex 检查 `git --version`、`node --version`、`npm --version` 并协助安装。GitHub Desktop 和额外编辑器可选；这套前端流程无需 Python、Docker 或数据库。

**拉代码**：在准备存项目的文件夹打开终端执行。GitHub 认证使用自己的账号，按登录提示完成，令牌和密码不贴到聊天中。

```bash
git clone --branch codex/ytsn-dev https://github.com/sunny-orb/pdd-operations-platform.git
cd pdd-operations-platform
git fetch origin
git merge origin/main
npm ci
```

已有项目目录就直接打开它，按第 4 节同步。首次克隆后，在 Codex 中打开 `pdd-operations-platform` 文件夹。用 `git branch --show-current` 确认本地分支为 `codex/ytsn-dev`。合并遇到冲突时，让 Codex 列出冲突文件并找 松饼 处理。

首次提交前，在项目目录设置自己的提交身份，把示例替换为本人信息，邮箱可用 GitHub 提供的 noreply 邮箱。

```bash
git config user.name "香草"
git config user.email "你的 GitHub 提交邮箱"
```

**配置生产 API**：创建 `apps/web/.env.development.local`，内容如下。域名末尾不加 `/api/v1`，文件由 Git 忽略，只保存在个人电脑上。

```dotenv
PDD_DEV_API_TARGET=https://pdd-operations.kktree.cn
```

**启动**：在项目根目录执行，保持终端运行。

```bash
npm run dev:web
```

打开 <http://127.0.0.1:5173>。以后每次启动都用这条命令，修改代理配置后重启。松饼 本机默认走本地后端，香草用上述个人配置选择生产 API。

<a id="api"></a>

## 3. 页面用哪些 API

浏览器请求 **本地的 `/api/v1/...`**，Vite 转发到 **`https://pdd-operations.kktree.cn/api/v1/...`**。React 代码保留相对路径，生产域名只写在个人配置里。所有下列接口均为 `GET`，直接复用现有请求函数和数据 hooks。

| 页面或能力   | 接口前缀                     | 主要接口与用途                                                                                                                                                                                                              | 现有前端入口（相对 `apps/web/src/`）                         |
| ------------ | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| 商品数据     | `/api/v1/products`           | `/meta` 版本与可用范围；`/options` 筛选；`/catalog` 商品目录；`/overview` 经营总览；`/risks` 风险；`/days` 明细；`/scores` 评分；`/scores/{listing_id}` 评分详情                                                            | `features/products/api.ts`、`data.ts`                        |
| 推广分析     | `/api/v1/promotion`          | `/meta` 版本；`/options` 筛选；`/options/selected` 已选商品；`/overview` 总览；`/days` 明细；`/diagnosis` 诊断；`/diagnosis/objects` 诊断对象                                                                               | `features/promotion/api.ts`、`data.ts`                       |
| 店铺销售     | `/api/v1/sales`              | `/filters` 日期、店铺与版本；`/performance` 指标；`/performance/trend` 趋势；`/shops` 店铺明细；`/targets` 目标；`/ratings` 评分；`/regions` 地区；`/product-regions/filters` 商品地区筛选；`/product-regions` 商品地区数据 | `features/sales/api/salesApi.ts`、`details.ts`、`queries.ts` |
| 售后分析     | `/api/v1/aftersales`         | `/meta` 报告与版本；`/options` 筛选；`/overview` 总览；`/analysis` 分析                                                                                                                                                     | `features/aftersales/api.ts`、`data.ts`                      |
| 运营中心     | 复用上述业务接口             | 汇总各域经营卡片，按每个域自己的版本和日期取数                                                                                                                                                                              | `features/overview/adapters.ts`、`data.ts`                   |
| 天气         | `/api/v1/weather`            | `/meta` 发布信息；`/publication` 天气正文                                                                                                                                                                                   | `features/overview/weather.ts`                               |
| 数据更新状态 | `/api/v1/data-status`        | 完整路径就是此前缀，读取来源更新状态                                                                                                                                                                                        | `features/overview/sourceStatusApi.ts`                       |
| 发布版本     | `/api/v1/published-versions` | 完整路径就是此前缀，供现有页面识别数据换版                                                                                                                                                                                  | `shared/api/publishedVersions.ts`                            |

表内的接口前缀与后缀拼起来就是完整路径，例如 `/api/v1/products/overview`。这是索引，实际参数和字段以对应页面的请求代码、`contracts.ts` 和[数据契约第 7 节](data-contract.md#7-前后端查询接口)为准。

**接数据时遵守四点**：

- 先读当前页面的 `meta` 或 `filters`，取得有效日期、版本和选项，再请求业务数据。经营查询通常带 `date_from`、`date_to`、`data_version`，版本与 ID 均使用接口返回值。
- 推广区分 `mode=store` 和 `mode=product`；商品评分沿用已有代码的独立评分版本；售后按 `report_id` 选择报告；天气按 `publication_id` 读正文。各页参数不能互套。
- 沿用 `shared/api/client.ts` 和页面数据 hooks 的校验、取消请求、错误及换版处理。不要删掉版本参数、关闭响应校验或无限重试。金额与比率按已有格式化方法展示，缺失值保留缺失含义。
- 接口缺字段、返回 401/403/404、持续失败或缺数据时，向 松饼 提供页面、操作、接口路径和状态码。新增字段或接口由 松饼 处理。

**首次连通检查**：打开 <http://127.0.0.1:5173/api/v1/sales/filters>，应返回包含 `data` 和 `meta` 的 JSON，再打开看板确认数据正常。若返回 502 或连接错误，检查本地配置路径、域名及终端输出。`/health` 仍指向本机后端，前端单独运行时不用它判断生产 API 是否正常。

<a id="daily"></a>

## 4. 日常同步与开发

| 名称                  | 位置和用途                                            |
| --------------------- | ----------------------------------------------------- |
| 本地 `codex/ytsn-dev` | 香草电脑上的工作分支，在这里修改、提交                |
| 远程 `codex/ytsn-dev` | GitHub 上香草的分支，接收他的推送                     |
| `origin/main`         | 最近一次 fetch 取回的远程 main 状态，用来更新本地基线 |
| 远程 `main`           | 团队交付基线，由 松饼 审核合并                        |

每次开始任务，在项目根目录执行：

```bash
git branch --show-current
git status
```

确认处于 `codex/ytsn-dev` 且工作区干净后，再执行：

```bash
git fetch origin
git merge --ff-only origin/codex/ytsn-dev
git merge origin/main
npm ci
npm run dev:web
```

这些步骤更新本地代码。遇到未提交改动、分叉或冲突，先保存当前工作并让 松饼 协助判断，禁止强推、丢弃改动或整条合入 松饼 的开发分支。一次完成一个待审任务；本次 PR 合并后，按上述流程同步再开始下一项。

<a id="pr"></a>

## 5. 验证、提交和提 PR

**① 验证效果**：在浏览器查看本次页面和受影响交互，再运行以下命令。公共组件获准修改后，也要检查使用它的其他页面。

```bash
npm run check:web
npm run test:web
npm run build
```

前端检查失败先处理，后端环境相关的全项目检查由 CI 和 松饼 执行。测试结果如实写进 PR。

**② 检查并提交**：让 Codex 根据 `git status`、`git diff` 逐个确认本次文件。`git add -- 文件路径` 只加入本次修改，再执行 `git diff --cached --check` 和 `git diff --cached` 复核。

提交格式为 `type(scope): 中文说明`，一笔对应一个完整目标，标题尽量 72 字符内。

| 改动               | 示例                               |
| ------------------ | ---------------------------------- |
| 新增能力           | `feat(products): 增加商品筛选入口` |
| 修复功能或视觉问题 | `fix(sales): 修正趋势图日期显示`   |
| 文档               | `docs(web): 补充页面操作说明`      |
| 纯代码排版         | `style(web): 统一组件代码格式`     |

scope 可用 `products`、`promotion`、`sales`、`aftersales`、`overview` 或公共 `web`。完整规则见 [AGENTS.md](../AGENTS.md)。例如：

```bash
git commit -m "fix(sales): 修正趋势图日期显示"
git push origin codex/ytsn-dev
```

**③ 提 PR**：打开 [GitHub 比较页](https://github.com/sunny-orb/pdd-operations-platform/compare/main...codex/ytsn-dev?expand=1)，确认 **base = main，compare = codex/ytsn-dev**。检查 **Files changed** 只有本次交付内容，填写改动页面、效果、验收步骤、检查结果和已知问题，必要时附脱敏截图，再把 PR 链接给 松饼。

**④ 审核与上线**：松饼 检查代码、页面效果和 CI，负责合并与发布。返修继续提交到 `codex/ytsn-dev`，原 PR 会自动更新。香草不自行合并 main、触发部署或改写已推送历史。新 PR 若夹带旧任务的改动，找 松饼 核对后再交付。

<a id="prompt"></a>

## 6. 直接给 Codex 的话

> 先读 AGENTS.md 和 docs/frontend-collaboration.md。我是香草，使用 codex/ytsn-dev，只运行前端，个人配置连接生产 API。请先检查分支和本地改动，按手册同步代码。我想把【页面的具体位置】从【现状】改成【期望效果】。沿用现有接口和指标口径，需要 松饼 确认的修改先列出来。完成后打开页面验证，运行前端检查，按手册提交到我的分支并创建 PR，把改动和验收步骤写清楚，由 松饼 审核合并和上线。
