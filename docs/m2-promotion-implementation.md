# M2推广分析实施方案（2026-10-28）

Goal：按requirements6和data-contract3.5.2还原店铺推广总览/商品推广明细，接入最新本地发布版本。共享导航保留，Ant Design/Recharts优先，经营事实不从原型JSON复制。

## 业务对象与结构

复用shop_channel_promotion_daily（店铺×渠道×日）、product_promotion_records（源推广行）、listings/profile_source_records/profile_listing_links及相容版本。资料N:M先逐字段共识，推广独立汇总，不能乘行；店铺/商品两个粒度不相加。当前物理表已有净归因成交额和笔数列，不建表、不改冻结migration。

扩M3相同来源必要投影：商品推广JSON净交易额/成交笔数，明星transaction_count；明星净额仍NULL。提升生产解释版本并重发源版，原四指标/M3回归对账。来源位置只用已核ODS商品推广概览、pdd_star_shop、BWD商品推广及数仓资料，不叠加ADS子集/旧补齐。只读RR固定本模式实际依赖，不受无关域版本变化牵连。

NUMERIC/Decimal汇总；API十进制字符串/null。比率仅用同一行成对有效分子/分母。跨渠道成交互斥/归因未知标参考或暂停合计成交率；净额缺项不能用商品渠道部分冒充完整净额。SOP按对象两个7日历窗及必需输入，unknown完整性保留观察/线索，未决花费组合不造已确认评级。

## API职责

/api/v1/promotion下meta/options/overview/days/diagnosis，mode=store|product。meta含各来源截至/成功/尝试/版本及推荐日期；其它请求显式data_version，409/422/503统一协议。overview合并趋势以同快照返回，days服务端分页。options级联有界商品候选，完整listing_id。店铺固定25行、商品25/50/100；商品模式不得依赖M1经营事实是否有同日行。

## 实施步骤

1. 后端：先写真实PG及数值反例，再实现相同来源扩列、独立快照、两模式查询/SOP，验证跨店同ID/NULL/净零/多推广方式/资料不放大/版本门禁。
2. 前端：只读本页必要源码/CSS及直接依赖，局部作用域迁移布局；接新API不搬全量快照，保持两模式字段/指标交互/趋势/漏斗/Top5/折叠清单/表格，最新日期与独立来源状态。
3. 联调：真实重拉后逐源投影/接口合计对账，浏览器两模式/筛选/分页/多指标/窄屏/字段核验；check/test/build和最终复核。
4. 文档：来源/契约/验收各归正文，剩余实际问题写统一issues，进度只在TODO。不推送或生产发布。

## Review Focus

跨渠道成交范围、逐字段NULL与真零、对象日历覆盖、推广独立身份/资料共识、版本向量、原型全部展示字段和真实最新数据分开验收。
