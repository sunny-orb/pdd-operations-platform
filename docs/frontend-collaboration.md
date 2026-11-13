# 香草的前端协作手册

香草在自己电脑上用 Codex 改页面，推到 GitHub 的 `codex/ytsn-dev`，向 `main` 提 PR（合并申请）。Sunny 审代码、看页面、决定合并和发布。仓库写权限照旧；下面的范围是双方约定，GitHub 和 Codex 不会自动替你挡住所有越界操作。

| 分支              | 用途                                                   |
| ----------------- | ------------------------------------------------------ |
| `main`            | 团队基线；通过 PR 由 Sunny 合并                        |
| `codex/ytsn-dev`  | 香草的长期开发分支；本地提交只推到同名远程分支         |
| `codex/sunny-dev` | Sunny 的开发分支；不要向它推送，也不要整条合到香草分支 |

## 第一次：拉代码并打开页面

1. 接受仓库邀请，确认自己的 GitHub 账号能打开[私有仓库](https://github.com/sunny-orb/pdd-operations-platform)。电脑上准备 Git 和 Node.js 24 LTS；用 Codex 打开项目文件夹即可，GitHub Desktop、额外编辑器、Python、Docker 和本地数据库都不是必需的。
2. 在你用来存放项目的文件夹打开终端，运行下列命令。GitHub 要求认证时，用自己的账号登录。不要把密码、令牌或 SSH 私钥发给 Codex。

   ```bash
   git clone --branch codex/ytsn-dev https://github.com/sunny-orb/pdd-operations-platform.git
   cd pdd-operations-platform
   git fetch origin
   git branch --show-current
   git merge origin/main
   npm ci
   ```

   `git branch --show-current` 应显示 `codex/ytsn-dev`；如果合并报冲突，先停下，让 Codex 列出文件并请 Sunny 一起确认。若合入 `origin/main` 后前端仍不支持 `PDD_DEV_API_TARGET`，按下面的「代理还没进 main」处理。

3. 在项目内创建 `apps/web/.env.development.local`，写入：

   ```dotenv
   PDD_DEV_API_TARGET=https://pdd-operations.kktree.cn
   ```

   只填地址，不加 `/api/v1` 或账号密码。这个文件留在自己电脑上，Git 会忽略它；改完要重启 Vite。

4. 在项目根目录运行 `npm run dev:web`，保持终端运行，用浏览器打开 <http://127.0.0.1:5173>。页面代码在你的电脑上运行，数据通过公司看板的生产 API 读取。如果页面打不开，先看终端；如果页面能打开但没有数据，确认文件位置和域名、重启服务，告诉 Sunny 页面、时间、接口路径和状态码，别贴真实接口响应或凭据。遇到 401/403 请 Sunny 核对访问权限。

<a id="proxy-before-main"></a>

### 代理还没进 main

在自己分支合过 `origin/main` 后，可以先把 Sunny 单独提交的代理改动带到本地，供现在预览和开发：

```bash
git cherry-pick 69dc49d
```

只在当前分支缺少代理配置时执行一次。遇到冲突请 Sunny 处理，不要整条合入 `codex/sunny-dev`。准备提业务 PR 时，先看 GitHub 的 **Files changed**；如果还包含代理配置和它的配套文档，等 Sunny 把代理单独合入 `main`，再同步 `origin/main` 并复查 PR 差异。

## 平时：给 Codex 交代任务

如果手册还没出现在本地分支，先运行 `git fetch origin`，再让 Codex 用 `git show origin/codex/sunny-dev:docs/frontend-collaboration.md` 读远程手册即可，避免为了拿文档合入别人的开发分支。

- 每次开始前先确认位于 `codex/ytsn-dev`，用 `git status` 检查是否有上次未提交的改动。工作区干净时，依次运行 `git fetch origin`、`git merge --ff-only origin/codex/ytsn-dev`、`git merge origin/main`。其中 `origin/...` 是刚取回的远程分支状态；这些命令只更新本地。如果拉取失败、分支分叉或发生冲突，把原因交给 Sunny；不强推、不重置本地改动、不让 Codex 替你选冲突的一边。
- 只改任务对应的 `apps/web/src/` 下页面、样式、组件和前端测试。先让 Codex 列出打算改的文件；涉及公共组件、接口契约、指标口径或依赖变化，先跟 Sunny 确认。金额、比率、缺失和真实零按已有契约展示，不能为了让页面好看而编数据或在前端改正式指标。
- 后端代码可以读来理解接口；不改 `apps/api/`、数据库、迁移、同步任务、部署文件、GitHub Actions 或项目协作规则。不直连数据库，不试写生产接口，不跑批量请求或压力测试。需求需要新接口、权限或服务器配置时，停下来交给 Sunny。
- 不提交个人配置、真实数据、接口响应、数据库备份、日志、依赖目录或构建产物。给 PR 附图前遮盖真实业务数据、店铺信息和账号。

可以直接对 Codex 说：

> 先读 AGENTS.md 和 docs/frontend-collaboration.md，确认当前是 codex/ytsn-dev。我要改【页面】的【具体位置】，现在是【现状】，希望变成【效果】。先列出你会改哪些文件；只改本次相关的前端页面和测试，沿用已有 API 和指标口径。改完在浏览器打开页面验证，列出改动与检查结果。我确认后再提交、推到 codex/ytsn-dev 并提 PR；不要合并或发布。

## 提交、推送和 PR

1. 在浏览器检查改动前后的效果、受影响的交互；改的是公用组件，还要检查引用它的页面。在项目根目录运行下列前端检查；失败时记录命令和错误，别把未通过写成通过。需要后端环境的全项目检查由 CI 和 Sunny 处理。

   ```bash
   npm run check:web
   npm run test:web
   npm run build
   ```

2. 执行 `git status` 和 `git diff` 看改动，只用 `git add -- <本次要提交的文件>` 逐个加入，再执行 `git diff --cached --check` 和 `git diff --cached` 复核。一次提交对应一个目标；标题用 `type(scope): 中文说明`，例如 `fix(sales): 修正趋势图日期显示` 或 `feat(products): 增加商品筛选入口`。纯文档用 `docs`，纯代码排版用 `style`；视觉或功能变化按 `feat` 或 `fix`。完整约定见 [AGENTS.md](../AGENTS.md)。

   ```bash
   git commit -m "fix(sales): 修正趋势图日期显示"
   git push origin codex/ytsn-dev
   ```

3. 在 [GitHub 比较页](https://github.com/sunny-orb/pdd-operations-platform/compare/main...codex/ytsn-dev?expand=1) 创建 PR。确认 **base 是 `main`、compare 是 `codex/ytsn-dev`**，在 **Files changed** 确认只有这次要交付的东西。说明改了哪个页面、怎么复现或验收、跑了哪些检查，必要时附脱敏截图；有已知问题、未验证项或者基线依赖要写清楚。就绪后把 PR 链接给 Sunny。
4. 收到修改意见就在同一个分支继续提交、推送，原 PR 自动更新。Sunny 看实际效果、代码差异和 CI，再决定是否合入 `main` 和上线。一次只在这条长期分支上做一个待审任务；上次 PR 合并后先同步 `origin/main`，再接下一项。若旧提交或文件仍出现在新 PR，先让 Sunny 核对分支，别自行改远程历史。
