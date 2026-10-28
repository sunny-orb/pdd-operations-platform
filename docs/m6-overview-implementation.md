# M6运营中心真实数据与共享导航实施方案

本方案对应2026-10-28用户授权：运营中心接真实最新数据，优先复用已接通页面接口，正文参照sites-current线上原型同样展示；共享顶部/左侧导航保留，顶部导航由开发调整、风格统一；未完善项入统一issues。执行状态只写TODO，来源证据/契约/实际验证分别维护。

## 展示与导航

保留各域日期条→“一屏掌握经营重点”摘要/月GMV/目标/动作/四入口/P0位置→四状态卡→四域卡→全部/P0/P1/P2预警→八列本日动作→全国关键天气/发布日切换→默认折叠五段口径。顶部调整为五个稳定看板入口并标当前页，保留共享外壳的侧目录/折叠/拖宽/窄屏行为。中心五区目录为overview、alerts、actions、weather、definitions；正文局部CSS复用线上必要结构，不搬旧全局body/topbar或经营快照。

固定3异常、3负责人、78分、历史fallback和ROI简化评级不生效。M6-01已按[data-contract](data-contract.md#metric-m6-01)确认去重对象及售后父子分别计项；现行代码正式异常总数/分级数仍NULL，待实施/对账，不再把统计单位说成未答复；可说明实际列表有几条观察提示，但不称全域异常/订单/对象总数。负责人/期限/进度为未登记，动作是来源建议。保留综合评分位置但显示不适用，不重算跨域加权分。各域日期独立、完整日未知，金额不同性质/期间不相加。

## 四域复用与接口职责

中心前端通过既有`/api/v1`四域API，各域独立元信息→固定版本查询，失败隔离，45秒/聚焦只检查本地元信息；变版取消旧请求、整组重取，409有界重试。同版本更新尝试/失败状态，不把未齐数标完整。

- M1复用products/meta及最新观察单日overview：交易额/日环比/同比、真实风险观察和建议，金额原口径不改。日期及完整K跳转沿main_*及listing_id，#risk。
- M2复用promotion/meta(mode=product)及最新单日overview/diagnosis：总花费/归因交易额/ROI/CTR/CVR；不使用店铺跨渠道合计ROI。诊断固定7日窗必须单列，不能当成最新单日结果。跳转带product_*与完整listing_id，#promotion-diagnosis。
- M3复用sales/filters、当月targets和最新单日performance/shops：月累计/目标/达成/缺店铺日/最新日值及店数。月份按负责域默认月，不因较旧观察日换成已过月目标。M3尚无URL接收时普通入口明确保留，不只拼无效筛选声称精准定位；确需定位再按目标页范围补收。
- M5复用aftersales/meta与overview：真实报告身份/期间、总率/A/T及状态；现金额门禁不以旧报告填补。跳转必须带真实report_id和适用分类/关键词，#aftersales-analysis。整体/品类/单款不能重复加总。

中心只适配展示字段，不在前端重算经营金额/比率/增长/风险公式。源域优先级保留原标识；完整性/归因/规则未核者为中心观察，不自动把域内P0当严重质量或已证紧急事件。来源已确认的业务规则和统一SOP沿data-contract，不新增一套中心评级。数据请求不会触发上游、模型、派单或业务消息。

## 天气对象、数据库与发布专用设计

天气不以城市观测或预报数值代替全国发布文本。唯一生效来源选NMC官网天气公报与中期预报两个互补文档；飞书公报页签仅作参考链接，无故障自动混源/回退。必要正文与真实发布时间有限读取、保留源URL；不读群、不发卡。天气独立于四域经营，失败不阻塞经营页。

在既有30经营表之外单列`weather_publications`一张天气发布归档表，追加Alembic07，不改冻结01基线/已执行迁移。它的对象是一份NMC公报发布日下、绑定两份真实原文及解析版本的不可变展示文档，非城市×日数值事实、非通用指标/任意配置。

字段：`id TEXT PK/FK→dataset_versions.id`；`issue_date DATE NOT NULL`（公报实际发布日，不用抓取日代替）；`revision_no BIGINT NOT NULL CHECK>=1`；`source_system TEXT NOT NULL CHECK='nmc_official'`；`content_sha256 TEXT NOT NULL CHECK64位小写hex`；`parser_revision TEXT NOT NULL`；`document JSONB NOT NULL`（固定Pydantic天气发布文档，见下述封闭结构）；`received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()`。UQ为(issue_date,revision_no)及content_sha256；索引(issue_date DESC,revision_no DESC,id)支持默认/历史选择。文档内容与源身份/时间、原文、解析版本共同进入hash，抓取时间不进入；同语义重跑复用，同日更正分配新修订，旧行不UPDATE/DELETE。

`document`是本发布产物的固定整体结构：title、issue_time、focus_regions、today_rain/today_temperature（regions/detail/status）、next_7_days（period/regions/detail/status数组）、sources（name/url/issue_time/text数组）、forecast_url、reference_url、group_card_link=NULL、note；不放经营金额、城市数值、其他业务FK或任意字段。整份发布文档作为不可变原文/解释产物有意保存JSONB，不另存会不一致的分组/过程/地区重复结果；日期/修订/摘要提供关系查询键。输入/输出按确定性解析可复算，私有原文证据与DB文档核对。天气表与dataset_versions为1:1；不伪造城市/店铺关联。既有task_runs要求成功必须引用真实dataset_versions结果，故复用weather_publication源版本，source_system=nmc_official，固定官方源定位/解释/必要原文引用、parser与实际适用窗；先draft插天气产物，再校验封版/原子发布。task_runs.result_version_id引用真实父版本，current_datasets.weather_publication指最新发布日最高修订，不因旧日补报倒退；不为天气放宽通用任务成功约束。子表仅允许向draft父版插入，已封版不可增改删；API只读published父版。source内容语义摘要和父版本包含解释/窗口的指纹区分，非两份可独立修改的经营指标。

权限：pdd_app仅SELECT；pdd_ingest仅SELECT/INSERT新表，不授UPDATE/DELETE/DDL。发布期按PG advisory锁串行/去重，失败记录task_runs并保留旧发布。先隔离真实PG验证迁移/幂等/修订/权限/失败恢复，再备份结构/授权并仅本机06→07；回退停天气任务后撤新表运行权限，已有业务数据拒绝破坏性降级。原30经营表字段/索引不改；派生天气SQL单独审阅，schema.sql冻结基线保持只读。

## 天气API与更新

`GET /api/v1/weather/meta`返回data_version/default_publication_id、各实际发布日最高修订列表、最新成功/尝试/失败及状态。`GET /api/v1/weather/publication?publication_id=...&data_version=...`返回固定document及meta；无记录明确source_unavailable，非法ID422，版本失效409。历史id可读，源更新不能把旧天气替换成当前页；两份源发布时间分别显示，不假称中期与公报同日同批。

解析必须识别“预计…08时至…”中的真实降水词，不能取第一段大风冒降水；当日气温没有明确时间证据时标未识别/待核，不从实况/此前/未来跨日泛化。下一7日为发布日起至+6日的过程展示范围，原文较宽或无法可靠裁剪时保留原句并标边界，不伪造成七条逐日预报。无文本命中不等于无天气风险。具体预报URL为官网当前入口时明确“当前官方预报”，不称历史原图。发布日/同日修订与日历缺口分别表达。

项目独立10/14/18调度加入weather来源步骤，每个时间槽最多原预算尝试；API/页面刷新不连NMC。机器/数据库不可用限制沿现有调度说明。

## 实施与验证步骤

1. 先用原型必要源码和已接API核展示/定位字段；天气限定两官方来源实读与字段/时间/反例核验，来源结论写回source-evidence。
2. 页面先写各域独立加载/失败/最新版本/409/零缺失/正式数与参考数/八列表/过滤和定位反例，再实现React/Ant Design中心与共享导航变更；公共变化检查M1/M2/M3/M5。
3. 天气先真实PG及解析反例，再实现连接器/不可变发布/双文档解析/只读API与最小迁移；严禁运行旧群发送脚本。真实发布后核原文/发布时间/DB/HTTP一致。
4. 逐域与原API同版本同窗口对账，GET任务数不变；运行check/test/build/format、真实PG迁移/天气验证及一次独立复核，浏览器能力可用时核导航/五区/过滤/天气切日/窄屏。不能执行的验收明确记issues；只提交本轮内容、不推送生产，预存推广改动保留。
