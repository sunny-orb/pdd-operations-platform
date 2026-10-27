# 经营事实字段设计：10张表

本文按用户已确认的首期30表范围，维护其中10张经营事实表的完整字段与关系；不沿用此前72表候选中已移除的主表、报告表或关系。本文件维护字段字典，实际结构由[独立迁移](migration-plan.md)管理；字段齐全不表示来源交付和业务口径已验收。

范围及关系见[数据库设计](../database-design.md#table-relationships)，公共规范见[本目录说明](README.md)，字段依据为[契约3.1—3.3](../data-contract.md#3-经营字段映射)和[3.5中文展示](../data-contract.md#page-display-contracts)。本文只保存必要原行及可核查度量；目标、规则和分析产物见[03](03-rules-results.md)，版本、血缘、当前发布及任务见[04](04-versioning.md)。

## 共同字段、约束与发布边界

人民币元及正式金额最多两位的发布验收、原值超精度保留/待核规则，统一见[公共字段规范](README.md#common-field-policy)。本文件NUMERIC继续保留源精度，不能用强制round让异常原值通过；各事实不额外增加整批相同的created_at/updated_at/deleted_at。

- **接收行和业务键分开。** 10张表均显式保存`id`、`dataset_version_id`、`source_row_key`、`source_record_id`。PK为`id`；共同UQ为`(dataset_version_id, source_row_key)`，只表示一份接收交付内的原行定位，不证明店铺日、款号或来源ID是永久业务唯一键。无原生ID时使用能在封存交付中复定位的确定定位符，不以内容哈希合并两条同值原行。重跑同一输入不制造第二份可累加版本；不同接收版本由当前发布选择，不直接叠加。
- **正式实体只有店铺和链接。** 事实业务FK只引用`shops.id`或`listings.id`，另有共同版本FK`dataset_versions.id`，均为TEXT。链接事实仅保存`listing_id`，规范店铺由`listings.shop_id`取得；原`source_shop_id/name`仍保留作来源证据。来源款号、商品编码、分类/子分类/虚拟分类、颜色规格、地区、退货原因和状态保存真实原值，不增加对应主表FK或只服务那些FK的匹配状态；同值跨命名空间不能默认相同。
- **版本冻结解释和来源。** `dataset_versions.dataset_code`决定逻辑用途，`origin_kind`为source/derived，`state`为draft/validated/published/rejected；`interpretation_ref`固定字段映射、身份解释、编码命名空间、单位/币种、日期时区、缺失和修订删除语义。每版只能有一种相容解释；引用须能按固定版本或摘要重读。规则包通过可空`dataset_versions.rule_set_id → rule_sets.id`取得，不给每行复制规则FK或阈值。物理来源、实际覆盖、完整性、发布时间及任务时间由04负责，不在事实中重复。
- **重解释追加派生版。** 封存后发生身份、来源分类、日期或单位重映射，生成`origin_kind=derived`的新版本；通过`version_inputs(output_version_id, input_version_id, input_role, window_start, window_end)`追溯原输入及实际使用的资料版本，依赖无环。不得原地替换旧版FK、改写原文或仅指向可变当前资料。类型化事实的业务值来自来源，不等于其版本一定为source；原行键须能追到确切输入交付。
- **专用日表不逐行保存day常量。** `product_daily_facts`、`shop_daily_facts`、`shop_channel_promotion_daily`、`shop_rating_daily`、`shop_region_daily`的类型契约及冻结版本解释限定day；接收校验须验证原交付周期，不能只因表名或可解析日期就判day。非day、缺周期或周期冲突的异常隔离，原周期标签及原行定位留在封存原交付与校验证据，不作为日事实发布，也不混入recent_7d/recent_30d滚动数据。`product_promotion_records`、`product_region_records`确有多周期，仍逐行保留`source_granularity`并按原周期隔离累计。
- **行期间与版本声明分开。** `period_start/end`只保存本行明确提供、且有依据规范化的实际期间，起止均包含；一个已核day标签可规范化为同日起止。本行没有期间列时不复制交付头、请求窗口、文件名或观察min/max。整份交付明确声明的实际期间存`dataset_versions.period_start/end`，可空。使用版本声明须由冻结解释允许并核对实际覆盖；有行期间先核对行值，冲突隔离，不用COALESCE静默覆盖。相同起止日不证明同批订单或完整。
- **状态不冒充数据可信度。** `shop_match_status/listing_match_status`取matched/unmatched/conflict/not_provided；仅matched时相应FK非空，其余FK为NULL，原值保留。`date_status`取parsed_label/verified_period/not_provided/missing/unparseable/conflict；前两种要求日日期或完整行期间非空，其余规范日期为NULL。parsed_label只证明标签可解析；verified_period须有业务日期依据。not_provided专指本行结构没有期间字段，与有字段但值空的missing不同；已有日期字段的日表不以not_provided绕过缺值。状态列显式填写，无业务默认值。
- **共同CHECK。** `id`、`dataset_version_id`、`source_row_key`及非空规范FK不得为空串；全部NUMERIC逐列排除NaN及正负Infinity，不预设precision/scale。只有已核商品整数计数使用BIGINT，其余源DOUBLE计数保留有限NUMERIC；转NUMERIC不能修复上游浮点误差。业务值无默认0；不统一加非负或0—1限制删除异常观测。期间同空或同非空，非空时起≤止。跨表语义、相容范围、行集完整性和重复不伪装为普通行CHECK，由发布校验执行。
- **时间保留原义。** 来源无时区时间先保留TEXT；只有时间含义与时区均已核实才填`source_created_at/source_updated_at/source_modified_at`的TIMESTAMPTZ。创建时间不当修改水位、业务日或应用成功时间。源ID/编码按字符串保存，已损失的前导零或精度不猜修复。
- **事实原名供固定输入回退。** 本文只保留`source_product_name`、来源业务日/期间及原行定位，不新增当前名或派生展示名。名称回退的固定事实来源/窗口、日唯一性、最近观察日、候选与冲突返回，统一见[01的固定版本读取与一行共识](01-master-data.md#固定版本读取与一行共识)。评分取其产物固定的`product_current`输入，新当前事实原名不得覆盖历史产物。参与售后原生分组的来源商品名遵从下文已核完整组键，不等同于给店铺链接另造身份。
- **独立聚合后组合。** 商品与推广、店铺销售与退款、J1与J2/J3分别处理真实维度并汇总；资料一对多用去重对象集合或EXISTS筛选，不能展开金额。J1独立合计Q/A，J2/J3分别合计S/P，再按确认范围组合；未证明销售—售后逐单关系，不设置事实之间的猜测FK。可空值、真零、无行、失败与未覆盖分别返回，不能把已知值小计称全量。
- **删除与修订。** 版本及实体FK统一ON DELETE RESTRICT、ON UPDATE NO ACTION，主对象停用不删历史，已封存事实不原地改写。源修订、删除、回补以获准协议形成新版本并重新对账；未读到一行、推广对象“已删除”或切换当前指针不等于删除事实。共享输入清理须另有保留、恢复与授权安排。

以下每表完整列出六列字典；共同约束逐表适用，各表补充专有UQ、CHECK和发布校验。

## `product_daily_facts` 商品日经营原行

粒度：一个来源版本内，一条店铺商品的day经营观察。限定已核BWD范围支持链接×来源日的条件业务键；未匹配行仍保留来源身份。

| 字段                    | 含义                               | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                            |
| ----------------------- | ---------------------------------- | -------------- | ---- | ------------------------ | ------------------------------------ |
| id                      | 本项目事实行ID                     | TEXT           | 否   | PK                       | 应用生成；不含业务含义               |
| dataset_version_id      | 可重读的商品日输入版本             | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验       |
| source_row_key          | 本版本内原行定位符                 | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键     |
| source_record_id        | 来源z_id原标识                     | TEXT           | 是   | 无                       | BIGINT源ID按TEXT保留；不当永久业务键 |
| listing_id              | 已核实的店铺商品链接               | TEXT           | 是   | FK → listings.id         | 不重复规范shop_id                    |
| listing_match_status    | 链接身份匹配状态                   | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认         |
| source_shop_id          | 来源shop_id原值                    | TEXT           | 是   | 无                       | 保留前导零                           |
| source_shop_name        | 来源观察店名                       | TEXT           | 是   | 无                       | 来源未提供则NULL，不从主表反填       |
| source_product_id       | 来源spu_id原值                     | TEXT           | 是   | 无                       | 不与款号/SKU混用                     |
| source_product_name     | 来源spu_name观察值                 | TEXT           | 是   | 无                       | 与资料运营名称区别保存               |
| source_dt_raw           | 来源dt原表示                       | TEXT           | 是   | 无                       | 只保留必要原时间证据                 |
| business_date           | 来源dt对应的日标签                 | DATE           | 是   | 条件UQ成员               | 已核day按DATE(dt)；不是补造事件日期  |
| date_status             | 行日期解析/核验状态                | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日   |
| source_related_date_raw | relevant_dt原值                    | TEXT           | 是   | 无                       | 已核范围全空；保留证据，不推定用途   |
| views                   | 商品浏览量product_pv               | BIGINT         | 是   | 无                       | PV，不是广告曝光                     |
| visitors                | 商品访客product_uv                 | BIGINT         | 是   | 无                       | 累计观测，不是区间去重人数           |
| favorites               | 收藏商品人数favorite_product_users | BIGINT         | 是   | 无                       | 不冒充加购                           |
| buyers                  | 成交买家transaction_users          | BIGINT         | 是   | 无                       | 不与订单/件数互换                    |
| orders                  | 成交订单transaction_orders         | BIGINT         | 是   | 无                       | 无默认0                              |
| units                   | 支付件数transaction_products       | BIGINT         | 是   | 无                       | 无默认0                              |
| product_revenue         | 商品成交金额transaction_amount     | NUMERIC        | 是   | 无                       | 已确认元；按原值，不再扣退款/做单    |
| source_conversion_rate  | 来源transaction_conversion_rate    | NUMERIC        | 是   | 无                       | 核对输入，不用于平均汇总             |
| source_first_category   | 来源平台一级类目观察值             | TEXT           | 是   | 无                       | 原平台一级类目，不等于运营品类       |
| source_second_category  | 来源平台二级类目观察值             | TEXT           | 是   | 无                       | 原平台二级类目；单值来源标签         |
| source_third_category   | 来源平台三级类目观察值             | TEXT           | 是   | 无                       | 原平台三级类目，不覆盖运营品类       |
| source_created_time_raw | 来源create_time原表示              | TEXT           | 是   | 无                       | 保留无时区来源时间                   |
| source_created_at       | 已有时区依据的来源创建时间         | TIMESTAMPTZ    | 是   | 无                       | 无依据则NULL，不作为修改水位         |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。仅对契约3.1已核day范围增加`(dataset_version_id, listing_id, business_date)`的条件UQ，条件为两业务列非空；同一版本不能混入未核来源或窗口后继续套用。未匹配行仍按原shop_id/spu_id/日查重，不由多个NULL逃过检查；不将z_id额外设为跨版本业务唯一。
- **CHECK / 发布校验：**共同约束及专用日表的day接收校验；已解析日期和匹配状态形状一致。发布前复核六项计数整数性、限定业务键、金额原值及来源范围；新窗口或重复先隔离，不取首末行。源转化异常保留作核对，不加0—1硬限制；已核原值不额外扣退款/做单。
- **关系 / 删除：**`dataset_versions` 1:N本表；`listings` 1:N本表，每条已匹配事实只属一个链接，未匹配为0个。规范店铺经链接取得，无规范shop_id副本，也无到商品资料原行的FK。版本/链接删除RESTRICT，缺行不自动当源删除。
- **函数依赖 / 范式：**原行键决定本次观测；规范店铺由链接决定，因此不重复保存。源名称、平台三级类目和源率是历史外部观测，不能由当前主值推出。运营品类/名称/等级取相容资料解释；转化、客单价、UV价值、收藏率、同比/环比、榜单及7/14/30日汇总按相容输入查询推导。
- **待源核验 / 口径边界：**已核范围外的键/窗口、自然日边界、商品全集、完整日及修订删除；M1-01收藏定义、M1-02推广归因沿契约原编号。来源是否已扣退款/做单继续补证，但已确认原值展示不再以此为前置，也不改称净GMV。

## `shop_daily_facts` 店铺日经营原行

粒度：店铺流量销售概览的一条店铺×日原始观察。JSON内实际需要的指标拆成显式列；店铺口径独立，不要求等于商品金额或访客合计。

| 字段                    | 含义                     | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                              |
| ----------------------- | ------------------------ | -------------- | ---- | ------------------------ | -------------------------------------- |
| id                      | 店铺经营原行ID           | TEXT           | 否   | PK                       | 应用生成；不含业务含义                 |
| dataset_version_id      | 店铺日输入版本           | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验         |
| source_row_key          | 本版本内原行定位符       | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键       |
| source_record_id        | 来源id                   | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL |
| shop_id                 | 已核实经营店铺           | TEXT           | 是   | FK → shops.id            | 未匹配不丢原行                         |
| shop_match_status       | 店铺匹配状态             | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认           |
| source_shop_id          | 来源shop_id              | TEXT           | 是   | 无                       | 原值字符串                             |
| source_shop_name        | 来源观察店名             | TEXT           | 是   | 无                       | 与当前规范店名分开                     |
| source_dt_raw           | 来源dt原表示             | TEXT           | 是   | 无                       | 不冒充支付/发货时间                    |
| business_date           | 可解释的来源日标签       | DATE           | 是   | 无                       | 缺/坏日期保留NULL                      |
| date_status             | 日期解析/核验状态        | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日     |
| shop_revenue            | JSON成交金额             | NUMERIC        | 是   | 无                       | 沿店铺原值，不混广告归因额             |
| shop_visitors           | JSON店铺访客数           | NUMERIC        | 是   | 无                       | 不用商品访客替换                       |
| shop_views              | JSON店铺浏览量           | NUMERIC        | 是   | 无                       | 源数值精度待核，不截整                 |
| product_visitors        | JSON商品访客数           | NUMERIC        | 是   | 无                       | 与店铺访客分别保留                     |
| product_views           | JSON商品浏览量           | NUMERIC        | 是   | 无                       | 与店铺浏览量分别保留                   |
| buyers                  | JSON成交买家数           | NUMERIC        | 是   | 无                       | 跨店跨日仅累计观测                     |
| orders                  | JSON成交订单数           | NUMERIC        | 是   | 无                       | 核对输入，不新增KPI卡                  |
| source_aov              | JSON来源客单价           | NUMERIC        | 是   | 无                       | 只作原值核对                           |
| source_uv_value         | JSON来源成交UV价值       | NUMERIC        | 是   | 无                       | 不平均该值求总览                       |
| source_conversion_rate  | JSON来源成交转化率       | NUMERIC        | 是   | 无                       | 正式率由合计买家/访客算                |
| source_updated_time_raw | 来源updated_at原表示     | TEXT           | 是   | 无                       | 不当应用接收成功时间                   |
| source_updated_at       | 有时区依据的来源更新时间 | TIMESTAMPTZ    | 是   | 无                       | 未解释时NULL                           |

- **PK / UQ：**`id`；只冻结`(dataset_version_id, source_row_key)`。版本×店铺×日是待核业务键，不提前硬设UQ；源id也不假定跨版本唯一。
- **CHECK / 发布校验：**共同约束及专用日表的day接收校验；日期、匹配状态与可空列相容。来源JSON只按冻结的必要键提取，解析失败/空值不补0；检查额外维度、明细/合计、重复店铺日与字段覆盖。销售/退款在各自范围聚合后匹配，不能原行互相JOIN。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表，已匹配行指向一个店铺，未匹配仍保留。与商品事实、目标、退款表无逐行FK。版本/店铺删除RESTRICT，来源无行不等于零成交或删除。
- **函数依赖 / 范式：**数值依赖本次完整来源观察；店铺/商品访客与浏览分别保留，不能互换。源客单价、UV价值和转化率仅作必要对账证据，正式率取相容合计分子分母；期间累计、贡献占比/排名与目标达成不落列。
- **待源核验 / 口径边界：**必要JSON键、金额单位/处理、计数精度、真实键、完整覆盖及修订删除。M3-01目标完成额路径、M3-02销售与退款日期/范围可比性继续待决；不因店铺原值存在而声称两项已确认。

## `product_promotion_records` 商品推广原行

粒度：一条商品推广来源记录及其实际day/滚动窗口、推广名称、场景与方式。相同链接同日可有多个推广方式，不建立到商品日事实的FK。

| 字段                       | 含义                         | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                              |
| -------------------------- | ---------------------------- | -------------- | ---- | ------------------------ | -------------------------------------- |
| id                         | 推广原行ID                   | TEXT           | 否   | PK                       | 应用生成；不含业务含义                 |
| dataset_version_id         | 推广输入版本                 | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验         |
| source_row_key             | 本版本内原行定位符           | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键       |
| source_record_id           | 来源id原标识                 | TEXT           | 是   | 无                       | 当次全表唯一；不据此宣称永久业务键     |
| listing_id                 | 已核实商品链接               | TEXT           | 是   | FK → listings.id         | 不重复规范shop_id                      |
| listing_match_status       | 链接匹配状态                 | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认           |
| source_shop_id             | 原shop_id                    | TEXT           | 是   | 无                       | 字符串                                 |
| source_shop_name           | 来源观察店名                 | TEXT           | 是   | 无                       | 不从主表反填                           |
| source_product_id          | 原spu_id                     | TEXT           | 是   | 无                       | 不等同款式/SKU                         |
| source_product_name        | 原spu_name                   | TEXT           | 是   | 无                       | 观察名称                               |
| source_dt_raw              | 原dt表示                     | TEXT           | 是   | 无                       | 窗口结束含义须有来源依据               |
| source_start_dt_raw        | 原start_dt表示               | TEXT           | 是   | 无                       | 不是修改水位                           |
| source_granularity         | 原time_granularity           | TEXT           | 是   | 无                       | day/recent_7d/recent_30d原值分开       |
| period_start               | 已解释的实际统计起日         | DATE           | 是   | CHECK成员                | 仅据本行start_dt等已核字段；包含起日   |
| period_end                 | 已解释的实际统计末日         | DATE           | 是   | CHECK成员                | 仅据本行dt等已核字段；包含末日         |
| date_status                | 期间解析/核验状态            | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日     |
| source_grouping            | 来源spu_grouping             | TEXT           | 是   | 无                       | 保留必要推广分组原值                   |
| promotion_name             | 来源推广名称                 | TEXT           | 是   | 无                       | 来源名称；不是稳定计划ID，无单列UQ     |
| promotion_type             | 来源推广场景/方式            | TEXT           | 是   | 无                       | 来源方式原值；不同名称合并沿已确认规则 |
| bidding_method             | 来源出价方式                 | TEXT           | 是   | 无                       | 必要原维度                             |
| source_deleted_label       | 来源is_deleted原标签         | TEXT           | 是   | 无                       | 指推广对象标签，不是事实删除指令       |
| spend                      | 总花费total_cost             | NUMERIC        | 是   | 无                       | 元，不与transaction_spend相加          |
| transaction_spend          | 成交花费transaction_cost     | NUMERIC        | 是   | 无                       | 含义不同，不能互替                     |
| attributed_revenue         | 总归因transaction_amount     | NUMERIC        | 是   | 无                       | 元，不称店铺成交                       |
| attributed_deals           | 总归因transaction_count      | NUMERIC        | 是   | 无                       | 来源DOUBLE，不截整                     |
| net_attributed_revenue     | 净归因net_transaction_amount | NUMERIC        | 是   | 无                       | 不二次扣退款                           |
| net_attributed_deals       | 净归因net_transaction_count  | NUMERIC        | 是   | 无                       | 不混总/结算笔数                        |
| settled_attributed_revenue | 结算归因金额                 | NUMERIC        | 是   | 无                       | 不等同净额                             |
| settled_attributed_deals   | 结算归因笔数                 | NUMERIC        | 是   | 无                       | 缺项保持NULL                           |
| impressions                | impression_count广告曝光量   | NUMERIC        | 是   | 无                       | 不用另一曝光列或商品PV替代             |
| clicks                     | click_count广告点击量        | NUMERIC        | 是   | 无                       | 不截断DOUBLE                           |
| source_roi                 | 来源actual_roi               | NUMERIC        | 是   | 无                       | 仅必要对账证据；正式率由相容合计重算   |
| source_net_roi             | 来源net_actual_roi           | NUMERIC        | 是   | 无                       | 仅必要对账证据；正式率由相容合计重算   |
| source_settlement_roi      | 来源settlement_roi           | NUMERIC        | 是   | 无                       | 仅必要对账证据；正式率由相容合计重算   |
| source_ctr                 | 来源click_through_rate       | NUMERIC        | 是   | 无                       | 仅必要对账证据；正式率由相容合计重算   |
| source_cvr                 | 来源click_conversion_rate    | NUMERIC        | 是   | 无                       | 仅必要对账证据；正式率由相容合计重算   |
| source_created_time_raw    | 来源创建时间原表示           | TEXT           | 是   | 无                       | 若源无必要时间则NULL                   |
| source_created_at          | 有依据的来源创建时间         | TIMESTAMPTZ    | 是   | 无                       | 不作为修改水位                         |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。不设版本×链接×日、promotion_name或推广场景唯一。来源id每批查重；推广名称不是稳定计划ID，不能据此丢另一条来源行。
- **CHECK / 发布校验：**共同约束；版本dataset_code为product_promotion，本行期间有序且与原周期/日期解释一致，已核day要求起止同日。M1/M2仅累计day；同源同店同商品同日不同推广名称按用户已确认规则分别合计每一花费、每套金额/笔数及曝光/点击，不叠加滚动期或ADS子集。spend与transaction_spend不能相加/互替，总/净/结算不得混成一个GMV，净额不二次扣退款。NULL小计携带缺项状态；原生源率只作必要核对。
- **关系 / 删除：**`dataset_versions` 1:N本表；`listings` 1:N本表，每条已匹配推广原行属一个链接，规范店铺经链接取得。与商品日、店铺渠道事实无逐行FK且不互相累计。版本/链接删除RESTRICT；source_deleted_label描述推广对象原状态，不下达事实删除。
- **函数依赖 / 范式：**窗口、方式与度量依赖原行；多推广方式是实际事实粒度。只保留页面和契约要求的原值及核对率，不把55字段全量复制；当前不纳入无展示/必保需求的直接/间接成交分项、其他曝光或询单等列。正式ROI、CTR、CVR、CPC、漏斗、低ROI日和SOP窗口由相容合计推导。
- **待源核验 / 口径边界：**M1-02归因、总/净/结算窗口、NULL与修订删除语义、用途及覆盖；依赖花费增长规则的判断仍受M2-03最低样本待决约束。不同推广名称的合并规则和已选总归因ROI已确认，不重列为待裁决；有限样例不证明归因互斥、全历史或原V6全模型。

## `shop_channel_promotion_daily` 店铺渠道推广日原行

粒度：商品推广概览或明星店铺来源的一条店铺×来源渠道×day观察，无商品链接维度。渠道只为当前两类来源的有限代码，不建立渠道管理主表。

| 字段                    | 含义                                  | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                             |
| ----------------------- | ------------------------------------- | -------------- | ---- | ------------------------ | ----------------------------------------------------- |
| id                      | 店铺渠道推广原行ID                    | TEXT           | 否   | PK                       | 应用生成；不含业务含义                                |
| dataset_version_id      | 渠道来源输入版本                      | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验                        |
| source_row_key          | 本版本内原行定位符                    | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键                      |
| source_record_id        | 来源id                                | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL                |
| shop_id                 | 已核实店铺                            | TEXT           | 是   | FK → shops.id            | 未匹配原行仍保留                                      |
| shop_match_status       | 店铺匹配状态                          | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认                          |
| channel_code            | 已核来源对应的有限渠道代码            | TEXT           | 是   | CHECK                    | product_promotion/star_shop；未核为NULL，无渠道主表FK |
| source_shop_id          | 来源店铺ID                            | TEXT           | 是   | 无                       | 原值                                                  |
| source_shop_name        | 来源观察店名                          | TEXT           | 是   | 无                       | 与当前主值分开                                        |
| source_channel_label    | 来源明确提供的渠道标签                | TEXT           | 是   | 无                       | 没有原标签则NULL，不伪装来源字段                      |
| source_dt_raw           | 来源dt原表示                          | TEXT           | 是   | 无                       | 不推定归因事件日期                                    |
| business_date           | 已解释的来源日标签                    | DATE           | 是   | 无                       | 不包含上海当天的规则由查询窗口控制                    |
| date_status             | 日期解析/核验状态                     | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日                    |
| spend                   | 概览总花费(元)/明星cost               | NUMERIC        | 是   | 无                       | 不用成交花费替代                                      |
| attributed_revenue      | 概览交易额(元)/明星transaction_amount | NUMERIC        | 是   | 无                       | 不等于店铺成交额                                      |
| net_attributed_revenue  | 概览净交易额(元)                      | NUMERIC        | 是   | 无                       | 明星未提供，必须NULL                                  |
| attributed_deals        | 概览成交笔数/明星transaction_count    | NUMERIC        | 是   | 无                       | 广告归因笔数，不是订单/买家                           |
| impressions             | 广告曝光量                            | NUMERIC        | 是   | 无                       | 保留来源精度                                          |
| clicks                  | 广告点击量                            | NUMERIC        | 是   | 无                       | 保留来源精度                                          |
| source_updated_time_raw | 概览updated_at原表示                  | TEXT           | 是   | 无                       | 明星未提供则NULL                                      |
| source_updated_at       | 可解释的来源更新时间                  | TIMESTAMPTZ    | 是   | 无                       | 不以创建时间填补                                      |
| source_created_time_raw | 明星create_time原表示                 | TEXT           | 是   | 无                       | 与概览更新时间不同语义                                |
| source_created_at       | 可解释的来源创建时间                  | TIMESTAMPTZ    | 是   | 无                       | 不当作修改/接收成功时间                               |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。版本×店铺×渠道×日只作待核候选；不以原型拒绝重复或渠道名称代替真实源键验收。
- **CHECK / 发布校验：**共同约束及专用日表的day接收校验；非空channel_code仅允许product_promotion/star_shop，取值由冻结解释映射。版本dataset_code须为shop_channel_product_promotion或shop_channel_star_shop；跨表发布校验前者仅对应channel_code=product_promotion、后者仅对应channel_code=star_shop。两个代码分别固定当前输入，每个代码只发布一个物理来源，两种真实渠道范围不能互相覆盖；页面先分别聚合，再按已核包含关系组合。channel_code为NULL的未核原行保留，不能进正式渠道合计；明星源未提供净归因额时必须NULL。核对实际适用店铺、渠道日缺行/真零和覆盖，不捏造不适用的明星店铺行；两源不与BWD商品推广相加。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表，已匹配行只属一个店铺；渠道是有限属性，无channel_id或渠道FK。版本/店铺删除RESTRICT；历史补齐重叠范围按切源规则只选一个生效来源。
- **函数依赖 / 范式：**渠道代码、原时间与度量依赖来源观察；源渠道标签仅在原行明确提供时保存，不把代码文案伪装为来源字段。无商品ID、规范商品FK或重复店铺经营额；ROI/净ROI/CTR/CVR/CPC、同期曲线、记录数、缺渠道日和分页查询派生。
- **待源核验 / 口径边界：**真实源键、渠道/店铺适用范围、历史覆盖、源时间含义及修订。跨渠道成交包含关系与归因沿M2-01/M2-02，缺去重键不能按金额/日期猜同笔成交。

## `shop_rating_daily` 店铺评分日原行

粒度：店铺评价概览中一个店铺一个来源日的评分原始观察；是外部平台评分，不重建平台评分模型，也不关联为新品评分的父记录。

| 字段                    | 含义                      | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                              |
| ----------------------- | ------------------------- | -------------- | ---- | ------------------------ | -------------------------------------- |
| id                      | 评分原行ID                | TEXT           | 否   | PK                       | 应用生成；不含业务含义                 |
| dataset_version_id      | 评分输入版本              | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验         |
| source_row_key          | 版本内原行定位符          | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键       |
| source_record_id        | 来源id                    | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL |
| shop_id                 | 已核实店铺                | TEXT           | 是   | FK → shops.id            | 未匹配保留原值                         |
| shop_match_status       | 店铺匹配状态              | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认           |
| source_shop_id          | 来源shop_id               | TEXT           | 是   | 无                       | 不经浮点转换                           |
| source_shop_name        | 来源观察店名              | TEXT           | 是   | 无                       | 不随主表名称改写                       |
| source_dt_raw           | 原VARCHAR dt              | TEXT           | 是   | 无                       | 保留原格式                             |
| rating_date             | 可解析的来源评分日        | DATE           | 是   | 无                       | 不拿create_time补评分日                |
| date_status             | 日期解析/核验状态         | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日     |
| source_score            | shop_rating_score原评分   | NUMERIC        | 是   | 无                       | NULL与真实0分不同；>1原值保留          |
| source_created_time_raw | create_time原表示         | TEXT           | 是   | 无                       | 不改称来源更新时间                     |
| source_created_at       | 有依据的来源创建/采集时间 | TIMESTAMPTZ    | 是   | 无                       | 无时区依据则NULL                       |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。版本×店铺×评分日唯一仍待真实重复核查，不提前建立UQ。
- **CHECK / 发布校验：**共同约束及专用日表的day接收校验；rating_date与日期状态相容。source_score只要求有限NUMERIC，不限制0—1；当前规则对0～1乘100，>1保留旧口径说明，缺行/空值/旧口径断线。不平均重复评分，不取最后一条，也不为缺分店铺回找旧日冒充同日分。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表，匹配行属一个店铺。与经营查询期、新品分数没有逐行FK。版本/店铺删除RESTRICT，历史补齐生成新版本。
- **函数依赖 / 范式：**原评分和来源创建时间依赖本次观察；百分制分、旧口径标志、全局最新评分日及趋势点均查询派生。评分日期与create_time不同义，创建时间不能填评分日或来源修改水位。
- **待源核验 / 口径边界：**dt格式/边界、真实源键、create_time时间语义/时区、空值原因和历史量纲；6个非空0～1样例只支持已核有限范围，不证明全历史同量纲。

## `shop_refund_records` 店铺退款统计原行

粒度：F01等经核准权威来源的一条实际退款统计记录，可能同时有平台、部门和店铺维度；未核真实粒度前不压成店铺日事实。

| 字段                     | 含义                         | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                   |
| ------------------------ | ---------------------------- | -------------- | ---- | ------------------------ | ------------------------------------------- |
| id                       | 退款原行ID                   | TEXT           | 否   | PK                       | 应用生成；不含业务含义                      |
| dataset_version_id       | 退款输入版本                 | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验              |
| source_row_key           | 本版本内原行定位符           | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键            |
| source_record_id         | 飞书record_id等来源标识      | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL      |
| shop_id                  | 已核实经营店铺               | TEXT           | 是   | FK → shops.id            | 未映射原行保留                              |
| shop_match_status        | 店铺匹配状态                 | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认                |
| source_shop_id           | 来源明确提供的店铺ID         | TEXT           | 是   | 无                       | F01只有店名时NULL                           |
| source_shop_name         | 来源店铺标签                 | TEXT           | 是   | 无                       | 不按后缀直接合并                            |
| source_platform          | 本行来源平台维度             | TEXT           | 是   | 无                       | 行级业务范围，不代替版本的物理来源/逻辑用途 |
| source_department        | 本行来源部门维度             | TEXT           | 是   | 无                       | 保留额外真实粒度，不按部门名推定负责人      |
| source_date_raw          | 来源日期原表示               | TEXT           | 是   | 无                       | 按已核日期/时区解释                         |
| business_date            | 可解释的统计日标签           | DATE           | 是   | 无                       | 与数仓成交日的可比性尚待M3-02               |
| date_status              | 日期解析/核验状态            | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日          |
| reported_refund_amount   | 统计退款金额原值             | NUMERIC        | 是   | 无                       | 不额外减做单，不冒充现金退款事件            |
| source_reported_amount   | 来源统计金额                 | NUMERIC        | 是   | 无                       | 仅用于核对F01自身退款占比分母               |
| source_refund_ratio      | 来源已算退款占比             | NUMERIC        | 是   | 无                       | 分母是统计金额，不能替代M3跨源退款率        |
| source_modified_time_raw | 来源明确提供的修改时间原表示 | TEXT           | 是   | 无                       | 没有可靠值时NULL                            |
| source_modified_at       | 有依据的来源修改时间         | TIMESTAMPTZ    | 是   | 无                       | 不以接收时间或业务日冒充                    |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。平台×部门×店铺×日只是候选；不设版本×店铺×日唯一，不按首末行消歧。
- **CHECK / 发布校验：**共同约束及日期/店铺匹配状态相容。先核对平台、部门、额外维度和合计行，再独立聚合为实际可比店铺日；正式M3退款率只取销售与退款双方均有已知值的匹配集，分子分母共同排除缺项。无匹配或非正分母不可算，不用F01自身source_refund_ratio替代跨源退款率。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表，已匹配行属一个店铺。与店铺日经营无逐行FK，退款先到、销售暂缺也可保留。版本/店铺删除RESTRICT。
- **函数依赖 / 范式：**退款、统计额和来源已算比例依赖真实原行；源比例只供核对其自身分母。匹配销售额、退款率、匹配店铺日/排除日、覆盖率和排除金额由查询计算，不复制销售事实值。退款金额不再额外减做单，也不称现金退款事件。
- **待源核验 / 口径边界：**F01真实粒度/合计行、金额单位、日期与部门范围、店铺映射、覆盖及修订删除。与数仓成交的业务可比性沿M3-02，不因源列已定位而当作验收通过。

## `shop_region_daily` 店铺地区成交日原行

粒度：后台地区来源的一条店铺×来源地区×day成交观察；省份/区域是来源属性，与聚水潭实发实退分开。

| 字段                    | 含义                 | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                               |
| ----------------------- | -------------------- | -------------- | ---- | ------------------------ | --------------------------------------- |
| id                      | 后台地区原行ID       | TEXT           | 否   | PK                       | 应用生成；不含业务含义                  |
| dataset_version_id      | 后台地区输入版本     | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验          |
| source_row_key          | 版本内原行定位符     | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键        |
| source_record_id        | 来源id               | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL  |
| shop_id                 | 已核实店铺           | TEXT           | 是   | FK → shops.id            | 未匹配保留                              |
| shop_match_status       | 店铺匹配状态         | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认            |
| source_shop_id          | 来源shop_id          | TEXT           | 是   | 无                       | 原值                                    |
| source_shop_name        | 来源观察店名         | TEXT           | 是   | 无                       | 来源证据                                |
| source_region_name      | 来源province标签     | TEXT           | 是   | 无                       | 保留其他/未知标签；不据同名宣称业务唯一 |
| source_dt_raw           | 来源dt原表示         | TEXT           | 是   | 无                       | 日期标签                                |
| business_date           | 可解释的来源日       | DATE           | 是   | 无                       | 不推断物流日期                          |
| date_status             | 日期解析/核验状态    | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日      |
| transaction_amount      | 地区成交金额         | NUMERIC        | 是   | 无                       | 已读来源标元                            |
| transaction_orders      | 地区成交订单         | NUMERIC        | 是   | 无                       | 源DOUBLE不截整                          |
| transaction_buyers      | 地区成交买家         | NUMERIC        | 是   | 无                       | 跨地区累加不称去重买家                  |
| source_price_per_buyer  | 来源买家均额         | NUMERIC        | 是   | 无                       | 仅核对，不平均                          |
| source_price_per_order  | 来源订单均额         | NUMERIC        | 是   | 无                       | 不混客单价分母                          |
| source_updated_time_raw | 来源updated_at原表示 | TEXT           | 是   | 无                       | 无时区原值保留                          |
| source_updated_at       | 有依据的来源更新时间 | TIMESTAMPTZ    | 是   | 无                       | 不等于发布成功时间                      |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。版本×店铺×原地区×日待核，不先强设业务UQ；同名地区或多个空地区不能充当去重证据。
- **CHECK / 发布校验：**共同约束及专用日表的day接收校验；日期/店铺匹配形状相容。核对地区别名、合计与额外维度；成交额、订单、买家分别合计，买家称累计观测。份额/客单价须相容正分母，不用max(分母,1)。无地区或其他标签保留，不猜省份。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表。原地区标签不建地区FK；本表与产品地区事实没有逐行关系且不相加。版本/店铺删除RESTRICT。
- **函数依赖 / 范式：**完整来源观察决定成交度量、原地区与源均额；两个均额分母不同，只作必要核对。规范地区标签如需调整，由固定解释形成派生版；地区数、份额、排名、汇总人均额和缺店提示查询派生，无地区展示宽表。
- **待源核验 / 口径边界：**真实键、原地区含义/别名、完整覆盖及修订删除。金额标元、订单/买家DOUBLE及当前day已核，不退回猜分母；不宣称跨省去重买家。

## `product_region_records` 产品地区实发实退原行

粒度：来源实际统计期内，店铺、来源产品/虚拟分类、款号/商品编码、颜色规格及地区的一条观察。可用day标签仅为日期证据，不证明发货/实退的事件归属。

| 字段                       | 含义                  | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                   |
| -------------------------- | --------------------- | -------------- | ---- | ------------------------ | ------------------------------------------- |
| id                         | 产品地区原行ID        | TEXT           | 否   | PK                       | 应用生成；不含业务含义                      |
| dataset_version_id         | 聚水潭地区输入版本    | TEXT           | 否   | FK → dataset_versions.id | 必填；版本用途及解释发布前核验              |
| source_row_key             | 本版本内原行定位符    | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键            |
| source_record_id           | 来源id                | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL      |
| shop_id                    | 已核实店铺            | TEXT           | 是   | FK → shops.id            | 不按店名后缀猜                              |
| shop_match_status          | 店铺匹配状态          | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认                |
| source_shop_id             | 原shop_id             | TEXT           | 是   | 无                       | 来源单值                                    |
| source_shop_name           | 原shop_name           | TEXT           | 是   | 无                       | 历史观察店名                                |
| source_style_code          | 原style_code          | TEXT           | 是   | 无                       | 字符串原值；命名空间由版本解释冻结          |
| source_product_code        | 原product_code        | TEXT           | 是   | 无                       | 字符串原值；数值化损失不能猜修复            |
| source_product_name        | 原product_name        | TEXT           | 是   | 无                       | 名称仅展示/追溯，不作身份键                 |
| source_color_specification | 原color_specification | TEXT           | 是   | 无                       | 一个来源规格描述                            |
| source_product_category    | 原product_category    | TEXT           | 是   | 无                       | 当时来源分类；分组/排除按冻结解释执行       |
| source_virtual_category    | 原virtual_category    | TEXT           | 是   | 无                       | 独立的当时虚拟分类；不合并分类体系          |
| source_region_name         | 原province            | TEXT           | 是   | 无                       | 包括其他/未知标签                           |
| source_dt_raw              | 原dt表示              | TEXT           | 是   | 无                       | M3-03未裁决时只称来源日期标签               |
| source_granularity         | 原time_granularity    | TEXT           | 是   | 无                       | 原day等标签；滚动期不能叠加成day            |
| period_start               | 已解释的实际统计起日  | DATE           | 是   | CHECK成员                | 本行已核day标签可令起止同日；不复制版本期间 |
| period_end                 | 已解释的实际统计末日  | DATE           | 是   | CHECK成员                | 包含末日；无本行期间证据则NULL              |
| date_status                | 日期解析/核验状态     | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日          |
| actual_shipped_qty         | 来源实发件数          | NUMERIC        | 是   | 无                       | 源DOUBLE不截整                              |
| actual_shipped_amount      | 来源实发金额          | NUMERIC        | 是   | 无                       | 单位继续按来源版本核验                      |
| actual_returned_qty        | 来源实退件数          | NUMERIC        | 是   | 无                       | 非退款订单/买家数                           |
| actual_returned_amount     | 来源实退金额          | NUMERIC        | 是   | 无                       | 不直接等同已退款现金                        |
| source_refund_rate         | 来源refund_rate核对值 | NUMERIC        | 是   | 无                       | 不直接平均源率                              |
| source_updated_time_raw    | 来源updated_at原表示  | TEXT           | 是   | 无                       | 保留原时间语义                              |
| source_updated_at          | 可解释的来源更新时间  | TIMESTAMPTZ    | 是   | 无                       | 不代替实际接收成功时间                      |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。店铺、来源分类、款码、规格、地区和期间的真实业务键待核，不压成款号×地区×日唯一。编码名称不用于任取一行去重。
- **CHECK / 发布校验：**共同约束及期间/日期/店铺匹配状态相容；按原周期隔离滚动期，不重复累计。产品候选按本事实来源产品分类＋可用款号、否则商品编码以及必要规格组成，经已确认规则排除包材/儿童赠品/分类未设置，不以当前资料的多值分类回填。四数值分别保留已知值与缺项条数；金额率只在分子分母覆盖相容时计算。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表。款码、SKU描述、分类和地区均为来源属性，无对应主表FK、匹配状态或到商品链接的猜测关联。版本/店铺删除RESTRICT；源分类修订产生新版本。
- **函数依赖 / 范式：**四度量和历史原分类/名称/地区依赖完整原行；不是当前名称或规范分类副本。来源款号与商品编码分别保留，命名空间/单位由版本冻结；不增全局SKU关系。实发实退额/件数、地区份额、四字段缺项和退款率由相容查询派生；不平均源refund_rate或将NULL补0。
- **待源核验 / 口径边界：**M3-03业务日期与实退归属、四数值单位/覆盖、真实货品规格键、组合拆分及修订删除。现行实退额÷实发额已有有限样例支持，不再问分母；它不等于同批实发最终退款率或现金退款率。

## `product_sales_records` 商品销售履约期间原行

粒度：J2已发货或J3已取消“销售排行（商品）”的一条真实统计原行。两类输入由版本用途区分；目前线索为订单支付期间，不能改成发货日或逐单销售事件。

| 字段                       | 含义                              | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                        |
| -------------------------- | --------------------------------- | -------------- | ---- | ------------------------ | ------------------------------------------------ |
| id                         | 销售期间原行ID                    | TEXT           | 否   | PK                       | 应用生成；不含业务含义                           |
| dataset_version_id         | 一份J2或J3类型化事实版本          | TEXT           | 否   | FK → dataset_versions.id | dataset_code区分J2/J3；经重映射由derived追原输入 |
| source_row_key             | 本版本内原行定位符                | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键                 |
| source_record_id           | 来源原生行ID                      | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL           |
| row_kind                   | 来源行的明细/合计性质             | TEXT           | 否   | CHECK                    | detail/subtotal/total/unclassified；无默认       |
| source_row_kind            | 来源明确提供的行类型标签          | TEXT           | 是   | 无                       | 不用数值大小猜合计                               |
| shop_id                    | 已核实店铺                        | TEXT           | 是   | FK → shops.id            | 原交付未提供则NULL                               |
| shop_match_status          | 店铺匹配状态                      | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认                     |
| source_shop_id             | 来源店铺ID                        | TEXT           | 是   | 无                       | 未交付不补                                       |
| source_shop_name           | 来源店铺标签                      | TEXT           | 是   | 无                       | 不认作当前六店全集                               |
| source_style_code          | 来源款号/款式编码                 | TEXT           | 是   | 无                       | 真实款号字符串；命名空间由版本解释冻结           |
| source_product_code        | 来源商品编码                      | TEXT           | 是   | 无                       | 真实商品编码字符串；不等同平台商品或全局SKU      |
| source_product_name        | 正式商品名称原值                  | TEXT           | 是   | 无                       | 不擅换成售后单的商品名                           |
| source_color_specification | 来源颜色规格                      | TEXT           | 是   | 无                       | 保留本次观察                                     |
| source_product_category    | 来源产品分类                      | TEXT           | 是   | 无                       | 销售缺分类不能伪填其他类                         |
| source_subcategory         | 来源实际参与分组/搜索的子分类原值 | TEXT           | 是   | 无                       | 原交付未提供则NULL，不由款号或品类反填           |
| source_virtual_category    | 来源虚拟分类                      | TEXT           | 是   | 无                       | 保留原来源分类                                   |
| source_period_label        | 来源本行期间原标签                | TEXT           | 是   | 无                       | 不复制交付头；文件名不等于实际范围               |
| period_start               | 本行明确提供的统计起日            | DATE           | 是   | CHECK成员                | 只规范化本行明确提供的端点；不复制版本声明       |
| period_end                 | 本行明确提供的统计末日            | DATE           | 是   | CHECK成员                | 只规范化本行明确提供的端点；不复制版本声明       |
| date_status                | 本行期间核验状态                  | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日               |
| sales_amount               | 本版本角色对应的来源销售金额      | NUMERIC        | 是   | 无                       | J2金额独立合计为S，J3独立合计为P；无默认0        |
| source_sales_qty           | 本版本角色对应的来源销售数量      | NUMERIC        | 是   | 无                       | 原列/精度待核；不能作为未发货退款件数            |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。真实排行粒度、组合拆分及额外分组未核，不设款号、SKU、店铺或期间业务唯一约束。
- **CHECK / 发布校验：**共同约束；row_kind仅detail/subtotal/total/unclassified，不默认detail，不按行顺序/金额大小判断合计。版本dataset_code只能是jst_shipped_sales（J2）或jst_cancelled_sales（J3）之一，冻结解释须与实际来源筛选一致；version_inputs的实际角色不能改变版本业务含义。同一版本不混J2/J3，行内不存sales_status。期间只使用本行证据或已核版本声明，冲突隔离；仅已核detail纳入累计。S/P各自锁定输入、范围、期间和完整性后独立合计。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表。款号、商品编码、分类、子分类、虚拟分类及规格保留原值，不挂款式/SKU/分类FK；无到return_records或商品链接的猜测FK。版本/店铺删除RESTRICT；取消状态不是删除或现金退款成功指令。
- **函数依赖 / 范式：**金额与数量依赖原行；版本决定J2/J3用途、支付期间解释和整交付声明，不重复成逐行sales_status或第二份S/P字段。sales_amount按相容角色独立合计为S或P，G=S+P查询生成；商品名称和分类保留源观测，不从退货记录或多值资料填回。来源分类缺失不填“其他类”，也不把一份销售金额复制给多分类。
- **待源核验 / 口径边界：**J2/J3实际交付、数量原列/精度、金额币种/单位、支付范围/店铺、行类型、组合拆分、完整性及修订删除；销售缺分类映射与零退货销售组仍需实际证据。source_subcategory仅在真实交付有值时参与搜索/分组；相同起止日不证明与J1同批订单。

<a id="aftersales-group-matching"></a>

### J1/J2/J3的完整组键、分母唯一归属及失败返回

以下是R05的确定性关联设计，落实已有“先独立聚合、分母不得复制”边界；尚未核实的真实交付、原生粒度和分类对应仍是技术待验项。没有完成相应验证时禁用该层分母关联，不把这里的设计说明当作新增业务口径或已存在的跨源映射证据；不增加订单FK、映射表或事实列。

1. **逐版冻结原生键及分析层级。** 报告固定的解释/规则引用须分别列出J1、J2、J3原生完整组键的字段顺序、含义与命名空间、分类体系、NULL/空串及未提供的区别、行类型/组合拆分和期间范围，再列每个实际分析层级的完整组键及允许投影。单款键至少表达已核的“来源分类体系＋分类＋款号或商品码命名空间及其原值”，实际参与原生分组的商品名、子分类、规格、店铺等也必须纳入；分类/虚拟分类体系不互换。使用款号还是商品码是逐版确定的来源键定义，不能按某行是否NULL临时切换，二者恰好同文也不合并。名称仅在真实分组证据确认参与时入键，不额外把原本同组记录按展示名拆开。当前来源缺少该维度和该维度的值为NULL分别解释，均不能拿空值当跨组通配符；必要键元缺失的原行保留为未归属，不能参与该层正式分母关联。
2. **各角色先独立聚合，再核合法匹配。** 只用各输入已核detail与真实完整组键，J1独立聚合Q/A，J2聚合S，J3聚合P，排除已识别的重复合计但不凭同值删除原行。行期间与版本声明按共同边界核验，报告固定J1确认期、J2/J3支付期及可比范围；起止相同不能把两种日期基础说成同批订单。向某分析层级汇总时，仅允许舍去已证可加的细维度；跨源匹配只使用双方共同有证、命名空间相容、且足以唯一对应的完整投影键。所有源原行/汇总块须能追到固定原行键；同一角色、同一分析层级，每一销售原行或不重叠汇总块只能贡献一次，不能通过多对多JOIN扩张。不同层级可各自重算，层级之间不能再相加。
3. **先证明唯一归属，不能从已见售后组猜出。** 对每个销售块，核对其范围覆盖与层级投影，只有能证明完全落在唯一合法目标组且无遗漏维度时，才把其金额纳入该组S或P。同一目标组接收多个可加销售块是允许的；同一销售块不能分配给多个目标组。销售没有商品名/子分类等细维度时，即使当前J1只观察到一个细组，也不能把缺维度当通配匹配；需要有证的唯一对应，否则该层仍失败。销售分类缺失时不从款号、售后分类或资料反填；分类体系不同且无证对应也不连。同款只是一部分键，不能作为降级连接条件。
4. **粗细不相容保留规模，返回不可计算原因。** 一个粗销售块涵盖多个细售后组，或缺少足以核定唯一归属的键元时，该细层受影响的S/P返回NULL及“分母粒度较粗”“组键缺失”或“命名空间/范围不相容”等明确原因；依赖它们的G/T和退款率相应不可计算，已知Q/A仍可展示，不复制、均摊或取首末组。已能独立确认的另一角色金额及不依赖缺项的指标可保留，不能把缺S/P补0。相容的品类/总体层可直接从各来源完整输入重新聚合并独立验算，不把失败细组的销售小计拿来拼总体；销售缺分类可以阻断品类层而不必阻断有证相容的总体层。
5. **分母来自完整销售范围，包含真实零退货销售组。** 每层分组全集分别取J1/J2/J3合法组键的并集并保留各角色覆盖状态，不只取售后对象并集；S/P从各角色全部相容销售块独立求和。销售组没有J1行时，仅在J1对该组所在范围完整、且已核“无行表示零退货”的交付语义下，才能确认Q/A为0并显示真实零退货；否则标无记录/未覆盖，不补0，也不能声称退款规模完整。J2/J3无行同理：只有该角色完整覆盖与已核无行即零语义共同成立才能认S/P为0；源金额NULL、键未归属、未接收或失败不能转成0。G=S+P、T=P+A各自要求组成项可用；P/G、A/S、T/G只在所需项已知、覆盖相容且各自分母为正时计算；已知0分母另报不可计算。若存在未归属、缺金额或覆盖不足，不丢掉它们后声称完整合计，返回缺项/排除范围及原因。
6. **报告比较用同一完整组键。** 本期/上期/去年先各自按本报告固定输入与解释完成上述聚合，再按分类体系、分类、编码命名空间及原生分组所需全部键元配对；禁止只用`style_code`或展示名配对。两期粒度、分类解释或范围不相容时，该层比较不可用；有证相容的粗层可独立比较。合并两期合法对象集合后，一侧无组须返回该侧无记录及覆盖状态，只有该侧完整交付语义能证明零时才视为已知零；缺键或不相容不伪装为新增/消失组。各率沿各自可用分母计算，比较所需任一期不可用时不输出率差或正式风险判断。报告修订与期间选择另遵03，当前事实更新不重写已固定的历史配对。

查询须按组、按Q/A/S/P分别返回值与可用状态，并附固定输入版本、完整组键、覆盖及不可用原因；失败原因须可定位到原行/销售块和缺失或冲突的键元。匹配校验不通过只阻断受影响层级/指标，不能发布复制分母后的率，也不能静默丢组后称全量。以上均为派生响应与校验证据，不增加逐行业务状态列。

以下均为设计示例，不是真实来源样例或执行结果：

| 情形                                                                                | 允许行为与失败结果                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| J2有已核某分类/款号销售100元，但没有子分类；J1同款有子分类甲、乙两组，A分别10、20   | 不能给甲乙各100，也不能各50；细组S与依赖率不可计算并报分母粒度较粗，Q/A照常展示。若品类范围/期间与覆盖均相容，品类层独立取S=100、A=30，可计算A/S=30%；G与总率仍取决于J3的P是否已知。                                     |
| 同品类J2有甲款100元、乙款200元；J1仅甲款A=10，已核J1完整且无行即零退货              | 乙款Q/A确认为0，品类S=300，A=10，售后率=10/300；只取甲款售后对象会错误得到10/100。若J1完整性/无行语义未证，则乙款为无记录，S仍保留真实300，但不宣称完整退款规模或正式品类率。                                            |
| 本期与上期均出现款号X，同时分别有“产品分类体系一/帽类/X”和“产品分类体系一/服装类/X” | 帽类只与帽类完整键配对，服装类只与服装类完整键配对，各自保留商品名/子分类等真实组元。若上期帽类缺组，返回该侧无记录，不能拿上期服装类X补齐；只有完整无行即零证据才能认零。若两期分类体系不同而无证对应，该层比较不可用。 |

## `return_records` 售后退货期间原行

粒度：J1“按退货原因”统计的一条真实来源原行，保留确认期间、实退数量Q和实际入库金额A；不预设一行一个订单、售后单或客户。

| 字段                       | 含义                              | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                                        |
| -------------------------- | --------------------------------- | -------------- | ---- | ------------------------ | ---------------------------------------------------------------- |
| id                         | 售后原行ID                        | TEXT           | 否   | PK                       | 应用生成；不含业务含义                                           |
| dataset_version_id         | J1类型化事实版本                  | TEXT           | 否   | FK → dataset_versions.id | J1确认期间输入；经重映射由derived追原输入                        |
| source_row_key             | 本版本内原行定位符                | TEXT           | 否   | UQ成员                   | 版本内定位原行；不冒充业务唯一键                                 |
| source_record_id           | 来源原生行ID                      | TEXT           | 是   | 无                       | 来源有值时原样按字符串保存；缺失为NULL                           |
| row_kind                   | 明细/小计/合计性质                | TEXT           | 否   | CHECK                    | detail/subtotal/total/unclassified；无默认                       |
| source_row_kind            | 来源实际行类型标签                | TEXT           | 是   | 无                       | 不按出现顺序推定                                                 |
| shop_id                    | 已核实店铺                        | TEXT           | 是   | FK → shops.id            | 源无店铺不伪填                                                   |
| shop_match_status          | 店铺匹配状态                      | TEXT           | 否   | CHECK                    | 显式填写共同匹配状态；无默认                                     |
| source_shop_id             | 来源店铺ID                        | TEXT           | 是   | 无                       | 字符串                                                           |
| source_shop_name           | 来源店铺标签                      | TEXT           | 是   | 无                       | 不凭别名后缀合并                                                 |
| source_style_code          | 来源款式编码                      | TEXT           | 是   | 无                       | 真实款号字符串；命名空间由版本解释冻结                           |
| source_product_code        | 来源商品编码                      | TEXT           | 是   | 无                       | 真实商品编码字符串；数值化损失不猜修复                           |
| source_product_name        | 正式商品名称原值                  | TEXT           | 是   | 无                       | 不以相近字段名替代                                               |
| source_color_specification | 来源颜色规格                      | TEXT           | 是   | 无                       | 一个来源描述值                                                   |
| source_product_category    | 来源产品分类原值                  | TEXT           | 是   | 无                       | 保留当时分类                                                     |
| source_subcategory         | 来源实际参与分组/搜索的子分类原值 | TEXT           | 是   | 无                       | 原交付未提供则NULL，不由款号或品类反填                           |
| source_virtual_category    | 来源虚拟分类原值                  | TEXT           | 是   | 无                       | 保留当时虚拟分类                                                 |
| source_return_reason       | 来源退货原因原文本                | TEXT           | 是   | 无                       | 原文本；不是已证实主原因或物流签收证据                           |
| source_analysis_fields     | 实际分类配置需要的额外原文本投影  | JSONB          | 是   | CHECK                    | 只保留未显式列出的必要原文本；确切键随版本解释冻结，当前配置未核 |
| source_aftersale_type      | 来源明确给出的售后分类            | TEXT           | 是   | 无                       | 仅本行明确提供时保留；不推定有效退款                             |
| source_aftersale_status    | 来源明确给出的售后状态            | TEXT           | 是   | 无                       | 来源原状态；未提供为NULL，不从筛选条件倒造                       |
| source_period_label        | 来源本行期间标签                  | TEXT           | 是   | 无                       | 不复制交付头，不从报告名猜起止                                   |
| period_start               | 本行明确提供的确认统计起日        | DATE           | 是   | CHECK成员                | 只规范化本行明确提供的端点；不复制版本声明                       |
| period_end                 | 本行明确提供的确认统计末日        | DATE           | 是   | CHECK成员                | 只规范化本行明确提供的端点；不复制版本声明                       |
| date_status                | 本行期间核验状态                  | TEXT           | 否   | CHECK                    | 显式填写共同日期状态；不证明完整日                               |
| actual_returned_qty        | 实退数量Q                         | NUMERIC        | 是   | 无                       | 不是退货数量、售后单数或退款订单数                               |
| actual_inbound_amount      | 实际入库金额A                     | NUMERIC        | 是   | 无                       | 不用退货金额替代，不称现金已退款                                 |

- **PK / UQ：**`id`；`(dataset_version_id, source_row_key)`。商品/规格/原因及组合拆分真实键待核，不设款号唯一，不以相同原因文本合并原行。
- **CHECK / 发布校验：**共同约束；版本dataset_code为jst_returns，row_kind仅detail/subtotal/total/unclassified，只汇总已核detail。source_analysis_fields非空时须为JSON对象，值仅必要的原文本或NULL；不许重复显式原原因/商品名称/子分类，不存业务FK、金额、数量、关系或规则输出。逐版核对实际分类配置、获准投影、显式输入和字段可用性，未核配置不能标证据齐全；期间与版本声明不冲突，J1确认窗口不偷换为J2/J3支付窗口。来源原状态保留，不由“不限制状态”推定全部有效。
- **关系 / 删除：**`dataset_versions` 1:N本表；`shops` 1:N本表。原分类/原因/款码及状态为来源属性，不建原因、标签或款式等主表FK，也无到销售原行的逐单FK。版本/店铺删除RESTRICT；销售暂缺仍保留售后，报告显示或当前版本切换不拥有/删除共享输入。
- **函数依赖 / 范式：**Q/A和原文本依赖完整原行，整交付恒定期间与分类规则归版本。source_subcategory保留真实搜索/分组证据，source_analysis_fields只是实际配置需要的有限原文本投影，不是任意EAV、标签数组或业务关系容器；无需额外投影时NULL。主原因、尺码三方向、质量多标签、原因Top及报告分组都从全量原行＋固定规则包查询生成，不另存可更新的重复结果表。
- **待源核验 / 口径边界：**J1实际交付、确认范围、有效状态、真实粒度/组合、金额单位、店铺、分类和子分类、去重/修订与覆盖。当前额外分类配置及确切读取键未核：不能猜备注/详情等字段已存在；后续按interpretation_ref冻结实际键、字段顺序和缺值处理，空键与未投影区分。M5-02多标签计数、M5-04主原因优先级仍待决；源原因关键词不冒充拒收/签收物流证据，不新增物流采集授权。

## 与3.5中文展示的对应及保留限制

| 展示范围                                                              | 基础输入位置                                                                                      | 查询及其他负责对象                                                                                                                                                                               |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| M1七指标、PV/访客/收藏/买家/件数链路、趋势/比较、榜单、日明细         | product_daily_facts六项计数、商品金额、源日期/身份；product_promotion_records必要广告输入         | 资料筛选见01；率/同比/环比/7、14、30日/排序按相容事实推导，评分与诊断见03。资料关联不放大经营额。                                                                                                |
| M2两模式八指标、净额附注、漏斗、低ROI日、日明细与SOP窗口              | 商品推广两花费、三套归因金额/笔数及广告曝光/点击；店铺渠道日花费/总净额/笔数/曝光/点击            | 两模式独立；ROI/CTR/CVR/CPC、前期/去年比较、14日覆盖及SOP证据查询派生。来源、时间和缺渠道日随版本/任务/覆盖说明，明星净额缺失不补0。                                                             |
| M3八KPI、店铺贡献与底部十列、销售/退款匹配附注                        | shop_daily_facts、shop_refund_records、shop_channel_promotion_daily各自独立聚合                   | 匹配销售/退款集合、匹配覆盖/排除日及金额、贡献占比、排名、率由查询得出；选择某店不改全店贡献分母。                                                                                               |
| M3月/年目标进度、达成率、进度条和缺口                                 | 店铺日成交为已选方案A的完成额输入，目标见03                                                       | M3-01已确认A：按目标实际店铺/期间汇总数仓店铺日成交，不重复扣退款/做单，不新增完成额表；真实范围、缺日、目标零/缺失继续核验。                                                                    |
| M3店铺评分卡、全局最新日、独立趋势/展开评分表                         | shop_rating_daily的rating_date、source_score及来源创建时间                                        | 百分制换算、旧口径提示、断线、全局最新日均查询生成；不平均、不借旧日分补缺店。                                                                                                                   |
| M3后台六列地区、聚水潭七列产品地区及四字段缺数提示                    | shop_region_daily成交额/订单/买家；product_region_records原分类/款码/规格/地区与四度量            | 两来源不相加；原值分组/排除、地区数/份额、人均额、实退额÷实发额和缺项条数查询生成。日期/单位仍遵M3-03及源证据边界。                                                                              |
| M5报告选择/名称、周/月和实际期间、生成时间/外链、历史记录及近8份Q趋势 | J1、J2、J3完整输入版本、明确期间及实际任务见04；报告元信息和比较绑定见03                          | 报告固定相容输入与规则。标题/报告类型、真实外链须有元数据依据，生成时间从实际产物封版取得；不凭文件名补期间，不把重叠30天报告相加成日趋势。                                                      |
| M5九卡、品类12列、单款13列、质量3列、历史10列、比较7列                | return_records Q/A和原分类/子分类/款码/原因/必要文本；product_sales_records的J2 S、J3 P与销售数量 | 按上文完整组键、分母唯一归属与失败规则分别聚合匹配；G=S+P、T=P+A、售前P/G、售后A/S、总T/G；源汇总行数、销售数量与订单数分开。全量分组/原因/标签由原行＋规则查询，不先截Top。                     |
| M5尺码三分、质量多标签、原因结构与退款阶段                            | 全量退货原行及实际可用文本/原状态、相容规则包，J3已取消销售P                                      | 尺码方向互斥须验证；质量标签可重叠，无法去重时不能SUM标签当总件数。尺码内部占比与占Q分别说明。未发货金额仅P代理；拒收/签收后无真实证据则保持缺失/待核，不从A减拒收反推，也不制造未发货退款件数。 |
| M5主风险、Top5、优先行动/优化Top3及整改八列                           | 同一查询组的Q/A/S/P、全量原因/问题证据和相容比较版本                                              | 风险和建议见03，目标见03；M5-01—04原待决继续约束结果。多层级金额不相加，不造整改工单、负责人、期限、进度或整改效果。                                                                             |
| M6四域摘要/预警、动作、精确定位及日期/状态                            | 复用相容商品、推广、店铺、售后输入和03产物                                                        | 中心不另建GMV/退款事实；完整截至、实际覆盖、成功/失败与更新时间由04返回；天气另属范围，不能由本事实设计填数。                                                                                    |

以上是当前30表范围内事实输入的字段覆盖，不把原型固定数字、Top截断结果、自动状态或禁用外链当真实源值。来源实际交付、真实键、字段可用性、期间、完整性及13项待决沿契约原编号记录；设计未补造缺失证据。后续建表/接入仍须单独实施计划、Alembic、真实PostgreSQL约束与修订恢复验证、来源对账及页面验收，本次只做文档静态核对。
