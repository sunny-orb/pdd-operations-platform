# 实施设计目录

这些文档负责具体模块的设计和实施边界。当前进度只见[TODO](../../TODO.md)，执行结果只见[验收记录](../validation.md)，字段与口径以[数据契约](../data-contract.md)为准。

| 范围             | 专用设计                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------- |
| M1 商品经营      | [新版链路](m1-products-implementation.md)                                                                           |
| M1 / M4 参考评分 | [模型复用、试行标准与限制](m1-model-reuse.md)                                                                       |
| M2 推广分析      | [双模式接入](m2-promotion-implementation.md)                                                                        |
| M3 店铺销售      | [首链、续段与目标](m3-sales-implementation.md)                                                                      |
| M5 售后分析      | [报告、金额、分类与目标](m5-aftersales-implementation.md)                                                           |
| M6 运营中心      | [四域复用与天气扩展](m6-overview-implementation.md)                                                                 |
| 全项目同步       | [来源编排与一致性](project-sync-implementation.md)                                                                  |
| AI能力规划       | [页面AI分析、项目问答与商品建议兜底草案](ai-assistance.md)（仅规划，规则待用户确认）                                |
| 看板性能         | [API计算与复测](api-performance.md)、[全库索引](database-performance.md)、[前端请求与渲染](frontend-performance.md) |
| 跨页筛选         | [共用能力、等级投影及联动统一建议](filter-consistency.md)（业务调整待确认实施）                                     |
| 采集归档         | [去重、容量与离线保留](archive-retention.md)                                                                        |
| 公司部署         | [Docker、CI/CD、单账号初始化与回退](deployment.md)                                                                  |
| 平台迁移         | [完整执行链接管与回退](sync-platform-handoff.md)                                                                    |

实施步骤描述设计形成时的任务分解，不表示全部尚未执行或已验收。修改业务规则前核对当前契约和问题台账；涉及数据库时遵守[设计流程](../database-design.md)。
