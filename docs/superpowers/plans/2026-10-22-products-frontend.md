# 商品整页前端实施计划

> 按用户已批准的整页方案连续实施；本地功能分支，不接数据库、不访问数仓。使用写计划、测试驱动与分工审阅流程。

**目标：** Ant Design统一控件，Recharts图表；完整商品页与明确标注的开发样例。
**架构：** React路由 → Query请求层 → 同形开发适配/未来API；展示区块不计算真实经营指标。URL维护三套独立筛选。
**规格：** `docs/m1-frontend-refactor-proposal.md`；用户已确认保留热卖商品榜及第6节调整，并要求优先组件库（替代先前SVG图表建议）。

## 约束与关注

- 默认未接入；开发模式通过明确入口启用合成样例，生产不包含样例自动回退。
- null与0、跨店同商品ID、三套筛选、旧请求取消、版本冲突、评分/诊断失败不阻断其他区块。
- 沿用线上内容顺序和紧凑卡片；不新增业务写操作，不调用AI。
- 保留既有未提交文档与其他业务路由，功能分支在当前工作区创建；不创建额外worktree。

## Task 1：类型、筛选、请求与开发数据

文件：`products/api/contracts.ts, productsApi.ts, queries.ts`；`state/filters.ts`；`preview/`。
接口：标准JSON响应包含data/meta、availability；Provider按endpoint、filters、signal返回契约结果。

- [x] 先写失败测试：URL三套状态隔离、跨店商品键、null/0、样例分页/过滤、取消请求/非法响应。
- [x] 实现Zod契约与只读API适配；开发样例在DEV动态加载，有限合成记录，无线上明细。
- [x] 运行针对测试验证；记录实际输出。

## Task 2：六区块及统一组件

文件：`ProductsPage.tsx, components/, sections/, products.css`。

- [x] Ant Design Card/Select/DatePicker/Table/Modal/Tag/Alert/Skeleton/Empty，Recharts趋势。
- [x] 完成摘要、评分独立筛选/分组分页与详情、七指标/周期/链路、诊断、告警、独立明细。
- [x] 请求/覆盖/计算/闭环状态区分；开发场景切换用于交互验收。
- [x] 浏览器验证筛选、分页、弹窗和无数据状态；静态样式不写镜像测试。

## Task 3：路由集成及关键交互验证

文件：`App.tsx`、必要公共样式；组件行为测试。

- [x] /products移除初始化首屏遮挡，其他路由保持。
- [x] 先写必要交互失败测试，再实现范围联动/状态处理。
- [x] npm run check、npm test、npm run build、格式与diff检查。

## Task 4：审阅、页面展示及交付记录

- [x] 独立代码审阅并修复重要问题；浏览器实际打开本地开发预览。
- [x] 更新TODO/方案状态及真实验收限制，只提交本次负责文件，不推送不发布。

执行记录：当前工作区功能分支codex/products-frontend；使用Ant Design+Recharts替代原拟SVG。数据/评分UI由分工实现，主任务完成整页及集成，独立审阅后修复5项问题。最终51项前端测试及4项原有后端测试通过；数据库、数仓、模型和发布均未执行。
