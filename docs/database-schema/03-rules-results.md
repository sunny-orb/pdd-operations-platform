# 03 目标、规则、评分与售后报告

本文是[30表完整字段设计](README.md)的组成部分，维护下列11张表：`sales_targets`、`sales_target_shops`、`refund_targets`、`refund_target_categories`、`refund_target_styles`、`rule_sets`、`score_standards`、`score_standard_items`、`product_scores`、`product_score_items`、`aftersales_reports`。这是按用户本轮范围重写的字段稿；不沿用已收敛掉的对象、候选字段或任务引用，结构文件统一见[迁移与审查计划](migration-plan.md)，实际执行与验证状态以该方案及TODO/validation为准。

业务定义及其可信状态以[数据契约3.5](../data-contract.md#page-display-contracts)为准，12项业务未决仍由[3.5.6](../data-contract.md#business-metric-questions)维护。设计有字段不表示来源、指标或页面已验收。公共类型、不可变版本及删除规则见[统一规范](README.md#统一字段规范)，关系边界见[数据库设计](../database-design.md#table-relationships)。

## 共同约束与版本接口

人民币元金额、自动时刻及不逐表套软删除的公共边界见[字段规范](README.md#common-field-policy)；标准线、率和UV价值保真实精度，不套金额两位规则。

- 所有ID、FK和来源编码均为`TEXT`；必填文本及可选文本有值时均不得为空串。业务日期用`DATE`，真实事件时刻用`TIMESTAMPTZ`。所有`NUMERIC`不预设precision/scale，必须排除NaN、正负Infinity；解析、汇总、比较保持Decimal精度，API传十进制字符串。已核BWD商品整数计数用`BIGINT`，推广DOUBLE计数用有限`NUMERIC`，不按显示样式截整。业务数值、日期和状态均无默认0或猜测值。
- 本文件的版本FK统一引用[04中的`dataset_versions.id`](04-versioning.md)。`dataset_code`区分逻辑交付用途，`origin_kind`为`source/derived`，`state`为`draft/validated/published/rejected`；`interpretation_ref`冻结当次映射、粒度、单位、日期、范围及未知项。版本的`period_start/end`仅为来源整份交付明确声明的实际期间，可空；它不是请求期间、评分窗口或完整日。`sealed_at`是实际封存时刻，不是业务日期。
- 派生版本经`version_inputs(output_version_id, input_version_id, input_role, window_start, window_end)`追到真实输入；`(output_version_id, input_role)`唯一，每个实际角色恰用一版。结果版本可用`rule_set_id → rule_sets.id`绑定规则。输入角色、窗口和同一已冻结解释一起参与校验，不把相关角色直接拼为一份来源。
- 评分的输入角色为`product_current`、`product_previous`、`ad_current`、`profile`、`standard`，需要额外观察历史时才有`product_history`。两个商品角色可引用同一来源版，但各有相邻、不重叠的实际7日窗；推广使用同一当期窗。持久化并发布评分必须实际绑定`product_current`及已知7日窗，评分截至日取其`window_end`，不从任务时间猜。该输入可部分/无可判值，仍逐项门控；整份当期输入尚未接收时只返回任务/待输入状态，不生成无时点的评分版。资料/标准无日窗口时两端均空；历史角色记录实际读取窗口。缺真实输入不造占位版本，缺口进入分项证据，不能让角色缺失绕过有效性校验。
- 售后报告的实际输入角色为`j1_returns`、`j2_shipped_sales`、`j3_cancelled_sales`及实际使用的`targets`。各角色保留实际使用窗口和自身确认时间/订单支付时间依据；同起止日不证明同批订单。没有适用目标时不造目标输入，目标相关结果明确待设/待确认。整期间汇总输入没有原日期时，不能从较大窗口按天切分或按比例分摊。
- 目标本地规范化允许为`derived`，通过`target_source`追原交付；使用独立固定映射或独立名单时分别增加`scope_mapping`、`scope_list`输入角色，映射也可冻结在当次`interpretation_ref`。解释/清单修订产生新版本，不改旧行；这里不保留另一套直接映射版本指针。
- 所有FK默认`ON DELETE RESTRICT、ON UPDATE NO ACTION`，已发布头、成员、分项、规则和输入均追加修订，不级联删除、不自动清理。下文发布校验是待实施的事务约束；普通CHECK不能跨表。一个候选版本的内容、必要分项、校验及发布状态应在同一受控事务中完成，当前只做静态设计。

## `sales_targets` 销售目标

粒度：一份目标规范化版本中，一个明确业务范围和期间的GMV金额目标原记录。首期固定GMV金额定义及元单位在目标版本解释中冻结，不逐行保存常量指标码。全店目标金额只存一份，实际店铺集合另列成员。

| 字段                | 含义                                 | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                               |
| ------------------- | ------------------------------------ | -------------- | ---- | ------------------------ | --------------------------------------- |
| id                  | 本目标记录标识                       | TEXT           | 否   | PK                       | 应用生成，不以月份或店名作ID            |
| dataset_version_id  | 目标原值及解释的规范化版本           | TEXT           | 否   | FK → dataset_versions.id | 可为source或derived，须可追原交付       |
| source_row_key      | 原交付中本行的确定定位               | TEXT           | 否   | UQ组合                   | 不以金额哈希合并同值行                  |
| source_target_key   | 来源已有目标记录ID                   | TEXT           | 是   | —                        | 原来源未给则NULL                        |
| source_scope_label  | 来源实际范围名称/说明                | TEXT           | 是   | —                        | 不是店铺关联键                          |
| source_period_label | 来源年份、月份或期间原标签           | TEXT           | 是   | —                        | 保留跨年经营年度表示，不由标签猜日期    |
| period_kind         | 目标期间类型                         | TEXT           | 否   | CHECK                    | month/year/custom/unknown               |
| period_start        | 经核实的目标起日，含当日             | DATE           | 是   | CHECK配对                | 未核实保持NULL                          |
| period_end          | 经核实的目标止日，含当日             | DATE           | 是   | CHECK配对                | 不从接收时间补造                        |
| period_status       | 期间依据状态                         | TEXT           | 否   | CHECK                    | confirmed/unknown/conflict              |
| target_amount       | 这一范围的一份目标金额               | NUMERIC        | 否   | CHECK                    | 元单位须有证据；真实0保留，进度不可除零 |
| scope_status        | 固定店铺集合核验状态                 | TEXT           | 否   | CHECK                    | complete/partial/unmapped/conflict      |
| evidence_note       | 原入口、重复月份、范围和期间核验说明 | TEXT           | 是   | —                        | 不含凭据或完整敏感查询参数              |

**键与CHECK：**PK为`id`；UQ为`(dataset_version_id, source_row_key)`，只防同份输入重复接收。目标金额有限且≥0；期间同时为空或同时非空且起≤止；`period_status=confirmed`要求两端非空且`period_kind≠unknown`；状态取表列有限集合。没有证据前不把月份设为全局唯一。

**发布校验与基数：**版本1:N目标；目标↔店铺经`sales_target_shops`为N:M。`scope_status=complete`要求至少一个已核成员，成员集合与原范围一致；不完整目标可作为带状态的输入保存，不能输出正式达成率。来源真实业务键、重复月份、月/年范围及金额单位按契约核验；冲突不取首末行。查询先按目标ID取金额一次，再用成员筛事实；选单店不能擅自分摊全店目标。独立年度交付可另存年目标，按月求和的年度目标只查询派生，二者不累加。

[M3-01](../data-contract.md#metric-m3-01)已于2026-10-28确认方案A：按数仓店铺日成交金额汇总月/年完成额，不重复扣退款和做单，不增加完成额表。使用固定shop_daily输入及目标的实际店铺/期间，完成额、进度和缺口由共享查询派生；不存入目标头，缺日/范围/零分母及来源数据仍需技术验收。

**删除与范式：**被使用/发布的目标及成员保留，新交付或解释修订新建版本。`id`或版本内原行键决定目标期间、金额和原范围；成员拆行且不复制金额，满足1NF/2NF。规范店名从店铺取得，单位/映射从版本解释取得，不保存完成额、进度和缺口，避免3NF中的传递依赖。

## `sales_target_shops` 销售目标店铺成员

粒度：一个销售目标实际包含的一家已核店铺。

| 字段              | 含义                     | PostgreSQL类型 | 可空 | 键/引用                       | 默认/说明                |
| ----------------- | ------------------------ | -------------- | ---- | ----------------------------- | ------------------------ |
| target_id         | 所属销售目标             | TEXT           | 否   | PK组合；FK → sales_targets.id | 不复制目标金额或期间     |
| shop_id           | 固定范围内的统一店铺     | TEXT           | 否   | PK组合；FK → shops.id         | 不按后续店铺全集自动扩展 |
| source_member_key | 原范围中该成员的确定定位 | TEXT           | 是   | —                             | 来源确有定位才填         |
| evidence_note     | 原别名对应及成员核验说明 | TEXT           | 是   | —                             | 映射解释沿父目标版本追溯 |

**键与CHECK：**PK/UQ为`(target_id, shop_id)`，禁止重复店铺；可选文本有值不得为空。没有额外按店名唯一约束。

**发布校验与基数：**目标1:N成员、店铺1:N成员，实现目标↔店铺N:M。“全部店铺”也固定为当次经核实的成员集合。未识别别名留父目标原范围及状态，不创建假店铺或把半份名单标完整。

**删除与范式：**两端FK均RESTRICT，发布后成员不原地编辑。非键属性依赖完整目标—店铺键，不存仅由某一端决定的名称、目标金额或月份，符合1NF/2NF/3NF。

## `refund_targets` 退款率目标

粒度：一份版本中，一组真实范围条件、一项期间声明和一个总退款率目标。首期固定total_refund_rate的分子/分母、量纲及适用解释随版本冻结，不逐行保存常量指标码，也不替代售前/售后率。契约已列的17组各保留一条定义，不合成统一全店率。

| 字段                      | 含义                           | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                                        |
| ------------------------- | ------------------------------ | -------------- | ---- | ------------------------ | ---------------------------------------------------------------- |
| id                        | 本退款目标记录标识             | TEXT           | 否   | PK                       | 应用生成                                                         |
| dataset_version_id        | 数值、原范围及解释的不可变版本 | TEXT           | 否   | FK → dataset_versions.id | 规范化可为derived                                                |
| source_row_key            | 原目标记录的确定定位           | TEXT           | 否   | UQ组合                   | 保留17组各自原身份                                               |
| source_target_key         | 来源已有目标ID                 | TEXT           | 是   | —                        | 没有则NULL                                                       |
| source_scope_label        | 原细类/目标范围简称            | TEXT           | 否   | —                        | 简称不作为身份或完整筛选条件                                     |
| source_scope_text         | 包含、排除及原筛选条件全文     | TEXT           | 否   | —                        | 不能结构化的条件仍保留                                           |
| scope_kind                | 总体范围是否受限               | TEXT           | 否   | CHECK                    | all/restricted；不得把未知当all                                  |
| category_match_mode       | 分类包含集合的算子             | TEXT           | 否   | CHECK                    | all/include_union                                                |
| style_match_mode          | 款码包含集合的算子             | TEXT           | 否   | CHECK                    | all/include_union                                                |
| scope_status              | 整份条件及清单核验状态         | TEXT           | 否   | CHECK                    | complete/partial/unmapped/conflict/unresolved_condition          |
| unresolved_condition_text | 尚未落入可执行条件的原文       | TEXT           | 是   | —                        | 有值则不能将范围视为完整                                         |
| source_period_label       | 来源适用期间原标签             | TEXT           | 是   | —                        | 累计参考标签不等于滚动期授权                                     |
| period_kind               | 来源目标期间类型               | TEXT           | 否   | CHECK                    | cumulative_reference/natural_month/rolling_window/custom/unknown |
| period_start              | 经核实的目标适用起日           | DATE           | 是   | CHECK配对                | 不从2026年标签补造                                               |
| period_end                | 经核实的目标适用止日           | DATE           | 是   | CHECK配对                | 不以报告末日代填                                                 |
| period_status             | 目标期间及适用依据状态         | TEXT           | 否   | CHECK                    | confirmed/reference_only/unknown/conflict                        |
| target_rate               | 本组总退款率目标               | NUMERIC        | 否   | CHECK                    | 小数率，33%存0.33；成员不复制                                    |
| evidence_note             | 数值、期间、清单及范围依据     | TEXT           | 是   | —                        | 指向原交付与契约证据                                             |

**键与CHECK：**PK为`id`；UQ为`(dataset_version_id, source_row_key)`。目标率有限且在[0,1]；期间配对且起≤止，`confirmed`要求实际期间非空且类型已知；所有代码受有限集合约束。`scope_kind=all`要求两种包含模式均为`all`；是否无排除条件仍须跨表校验。`scope_status=complete`要求`unresolved_condition_text`为空，其他缺口仍须发布过程检查。

**发布校验与基数：**版本1:N目标，目标各1:N分类条件/款码条件。分类集合`C=(明确总体内全部分类，或包含分类并集)−排除分类并集`，款码集合`S=(明确总体内全部款码，或包含款码并集)−排除款码并集`，适用范围为同一业务总体中的`C∩S`。空包含名单只有在该维度明确为`all`时才表示不限；`include_union`空名单、缺分类体系/款码命名空间或残留原条件均阻止应用。不同体系同名不合并，层级子孙不自动展开。

童帽指定14款使用分类包含条件与14行款码包含；其补集使用相同分类集合、款码`all`并排除同一份真实14款。必须核对完整清单及补集使用同版名单，缺一款不将13款发布为完整14款。普通童装的合作/自研及排除条件、秋冬帽范围及排除红包、产品分类/虚拟分类并集均按真实原条件固化；未知清单不解释为全量。

17组数值及原细类范围的唯一正文见[契约3.5.4](../data-contract.md#m5-display-contract)。范围重叠导致同一对象多目标命中时报告冲突，不取第一条。参考定义可保留，但目标用于滚动30天/自然月及多个细类合并的规则仍受[M5-03](../data-contract.md#metric-m5-03)阻断；不得自动平均17组目标率、套全店均值或为混合总体生成目标。

**删除与范式：**目标、两类条件和实际原名单按固定版本保留，修订追加。头键决定目标率、期间及集合算子，条件成员分行，满足1NF/2NF。分类/款码是来源条件标量，不是未建设的主数据引用；不复制总体目标、测算损失或每个成员的目标率，符合3NF。

## `refund_target_categories` 退款目标分类条件

粒度：一个目标中的一条真实来源分类包含/排除条件。这里只有`target_id`是FK，不设置统一分类主对象。

| 字段                   | 含义                           | PostgreSQL类型 | 可空 | 键/引用                        | 默认/说明                            |
| ---------------------- | ------------------------------ | -------------- | ---- | ------------------------------ | ------------------------------------ |
| target_id              | 所属退款目标                   | TEXT           | 否   | PK组合；FK → refund_targets.id | 版本及原名单沿父目标追溯             |
| source_member_key      | 原条件的确定定位               | TEXT           | 否   | PK组合                         | 无源成员ID时由固定解释定位原条件片段 |
| effect                 | 包含或排除                     | TEXT           | 否   | CHECK                          | include/exclude                      |
| source_category_system | 实际来源分类体系               | TEXT           | 否   | —                              | 产品分类/虚拟分类/运营品类等分别识别 |
| source_category_key    | 原分类编码                     | TEXT           | 是   | 条件UQ                         | 原来源无编码则NULL，不造ID           |
| source_category_label  | 原分类标签                     | TEXT           | 是   | 条件UQ                         | 编码/标签至少一项有值；原观察证据    |
| match_basis            | 条件实际按编码还是精确标签匹配 | TEXT           | 否   | CHECK                          | source_code/exact_label              |
| condition_status       | 本条件是否已明确可执行         | TEXT           | 否   | CHECK                          | confirmed/unresolved/conflict        |
| evidence_note          | 别名、体系或原条件核验依据     | TEXT           | 是   | —                              | 不将同名自动视为跨体系同一分类       |

**键与CHECK：**PK为`(target_id, source_member_key)`。有编码时UQ为`(target_id, source_category_system, source_category_key)`；无编码时条件UQ为`(target_id, source_category_system, source_category_label)`。两者都不含`effect`，防同一条件重复或同时包含/排除。编码/标签至少一项非空；`source_code`要求编码，`exact_label`要求原标签；状态及effect按表列集合。

**发布校验与基数：**目标1:N条件；体系和匹配依据必须与被筛事实的冻结字段映射相容。仅标签条件需先核对本源同标签冲突、空白及层级语义；未确认条件使父目标不能标`complete`。条件唯一不能替代两个不同体系实际匹配范围是否重叠的检查。

**删除与范式：**FK RESTRICT，发布后随目标追加修订。属性依赖完整目标—原条件键；原编码/标签是本次条件的历史证据，不是规范分类名称副本。没有分类ID或分类主表的传递依赖，符合1NF/2NF/3NF。

## `refund_target_styles` 退款目标款码条件

粒度：一个目标中的一条真实来源款码包含/排除条件。这里只有`target_id`是FK，不建立未证实的统一款式身份。

| 字段                   | 含义                     | PostgreSQL类型 | 可空 | 键/引用                        | 默认/说明                            |
| ---------------------- | ------------------------ | -------------- | ---- | ------------------------------ | ------------------------------------ |
| target_id              | 所属退款目标             | TEXT           | 否   | PK组合；FK → refund_targets.id | 不复制目标率或期间                   |
| source_member_key      | 原名单/原条件中的定位    | TEXT           | 否   | PK组合                         | 不以当前名单顺序替代原定位           |
| effect                 | 包含或排除               | TEXT           | 否   | CHECK                          | include/exclude                      |
| source_style_namespace | 款码实际所属来源命名空间 | TEXT           | 否   | UQ组合                         | 保留ERP/来源范围，不与平台商品ID混用 |
| source_style_code      | 原来源款式编码           | TEXT           | 否   | UQ组合                         | 字符串保留前导零，不数值化猜回编码   |
| condition_status       | 款码及匹配语义核验状态   | TEXT           | 否   | CHECK                          | confirmed/unresolved/conflict        |
| evidence_note          | 清单、别名或编码冲突说明 | TEXT           | 是   | —                              | 原名单可沿父版本及输入重读           |

**键与CHECK：**PK为`(target_id, source_member_key)`；UQ为`(target_id, source_style_namespace, source_style_code)`，不含`effect`，同款码不得同时包含/排除。effect及状态取有限集合；编码/命名空间均非空。

**发布校验与基数：**目标1:N条件。款码和事实字段须在同一已核命名空间中精确匹配；同码不同范围不擅自合并。指定14款及补集按父目标所述成组核验，不能以本表当前行数推断原清单已完整。

**删除与范式：**FK RESTRICT，发布成员不可改。每个属性依赖完整目标—原成员键；不存款式名称、商品归属、目标率等可从别处取得或尚未成立的属性，符合1NF/2NF/3NF。

## `rule_sets` 不可变规则参数包

粒度：一个业务域的一份完整、带结构版本的参数包修订。它保存有限规则配置；指标公式/单位的权威仍在领域代码与数据契约，不建设自助指标定义平台。

| 字段                | 含义                               | PostgreSQL类型 | 可空 | 键/引用 | 默认/说明                                                  |
| ------------------- | ---------------------------------- | -------------- | ---- | ------- | ---------------------------------------------------------- |
| id                  | 规则包修订标识                     | TEXT           | 否   | PK      | 被版本引用后不可改义                                       |
| set_code            | 同一用途规则包的稳定代码           | TEXT           | 否   | UQ组合  | 名称变化不变更既有含义                                     |
| revision            | 本包修订号                         | TEXT           | 否   | UQ组合  | 参数或启用状态改变均新增修订                               |
| domain              | 包所属业务域                       | TEXT           | 否   | CHECK   | product_scoring/product_risk/promotion/aftersales/overview |
| schema_revision     | 参数结构的确切版本                 | TEXT           | 否   | —       | 对应固定校验器，不是可变当前schema                         |
| parameters          | 该修订完整结构化参数               | JSONB          | 否   | CHECK   | 对象形状，按下述封闭结构校验                               |
| confirmation_status | 整包业务确认状态                   | TEXT           | 否   | CHECK   | confirmed/partially_confirmed/pending_confirmation         |
| source_locator      | 本包依据的确切SOP/契约/交付定位    | TEXT           | 否   | —       | 必须可追到固定内容，不只存当前网页                         |
| source_observed_at  | 真实观察该依据的时刻               | TIMESTAMPTZ    | 是   | —       | 未记录则NULL，不冒充规则生效日                             |
| confirmation_ref    | 整包确认或逐分支确认记录的固定引用 | TEXT           | 是   | —       | 未确认不得填已批准文案                                     |
| confirmed_at        | 真实整包确认时刻                   | TIMESTAMPTZ    | 是   | —       | 仅整包确认且时刻有证据时填，未记录则NULL                   |
| content_sha256      | 结构版本及参数正文的内容摘要       | TEXT           | 否   | CHECK   | 固定规范UTF-8 JSON后求SHA-256，不用摘要当业务ID            |
| evidence_note       | 已确认范围、未知项及适用边界       | TEXT           | 是   | —       | 不复制完整业务数据或敏感行                                 |

**键与CHECK：**PK为`id`；UQ为`(set_code, revision)`。`parameters`必须是JSON对象；摘要为64位小写十六进制；代码/状态取表列集合。整包`confirmed`要求`confirmation_ref`非空；`confirmed_at`有值时要求整包已确认且时刻有证据，未知时不补造，分支自身确认记录仍须保留。JSON中的业务小数只接受D字符串，整数只接受下述I；拒绝非有限值、浮点舍入及未知键；不能只以“JSON可解析”作为业务结构通过。

### `parameters` v1封闭结构

`schema_revision="v1"`仅允许根对象`{policy, branches, dictionaries, suggestions}`，四键必需且不可NULL；根及下列全部对象均拒绝未知键。domain只取表列五域，policy按域选择下表形状，不允许跨域分区。此为可实现的校验契约，尚未导入真实规则包，不代表实际配置已经齐备。

**标量约定：**`D`为精确十进制字符串，匹配`^-?(0|[1-9][0-9]*)(\.[0-9]+)?$`，拒绝指数、NaN/Infinity及JSON浮点；`I`为0至BIGINT上限的JSON整数；`B`为JSON布尔；`S`为非空字符串；`S[]`为去重字符串数组。下文未注明可选或NULL的键全部必需且非NULL。天数、档位、已核整数门槛/顺序/条数可用I；权重、元、比率、倍数及分值用D，后端Decimal解析/汇总/比较，不经JS Number重算。NULL不是0、不限或成功。

| domain          | policy精确键、类型及v1允许值                                                                                                                                                                                                                                                  | 单位及边界                                                                                        |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| product_scoring | `{metrics, gates, tier_policy_code, lifecycle_policy_code}`；metrics/gates见下；tier_policy_code=`highest_inclusive_quarter_weight`；lifecycle_policy_code只能NULL                                                                                                            | 当前没有已核动态阶段算法，NULL使阶段未知，不关闭独立可算项；v1不生成/拟合四线                     |
| product_risk    | `{baseline_days:I=7, hit_policy_code:"first_match", sort_policy_code:"priority_score_identity", excluded_level_codes:["清仓"], blockbuster_policy_code:"legacy_revenue_top_20_percent"}`                                                                                      | 缺失/冲突等级同样不进入正式风险；爆品沿当前筛后线上集合/名额/稳定顺序，不擅加正金额或临界同额扩入 |
| promotion       | `{current_window_days:I=7, previous_window_days:I=7, end_policy_code:"min_selected_source_latest_shanghai_yesterday", hit_policy_code:"collect_findings_highest_priority", sort_policy_code:"priority_current_spend_identity", focused_level_policy_code:"sa_exclusive_new"}` | 相邻7日日历窗；重点款仅S/A（可带级）、含专供或新品；无finding的默认观察不计异常                   |
| aftersales      | `{comparison_policy_code:"frozen_report_periods", grouping_policy_code:"full_source_groups_unique_denominator"}`                                                                                                                                                              | 分别按本节报告选择和02完整分组/唯一分母规则，不在JSON另写匹配公式                                 |
| overview        | `{priority_order:["P0","P1","P2","observation"], sort_policy_code:"sop_impact_deterioration_scope", deduplication_policy_code:"same_object_group_no_parent_child_amount_sum", weighted_score_policy_code:"not_applicable", count_policy_code:null}`                           | 仅组织相容域结果及既有SOP分级/排序；M6-01未定时不输出正式异常总计，不制造统一分数                 |

product_scoring的policy.metrics为固定顺序十项数组，每项恰为`{metric_code:S, weight:D, validation_branch_code:S}`。代码唯一且集合完整，validation_branch_code固定为`validate_`加同一metric_code并引用本包分支。以下为全部代码/权重/单位；十权重精确相加为"100"，未启用收藏仍保留其10分。

| metric_code                 | weight（D） | 特征/单位                     |
| --------------------------- | ----------- | ----------------------------- |
| visitors_7d                 | "12"        | 7日访客累计/人次              |
| product_revenue_7d          | "18"        | 7日商品交易额/元              |
| units_7d                    | "12"        | 7日支付件数/件                |
| favorites_7d                | "10"        | 7日收藏，定义待M1-01          |
| score_conversion_rate_7d    | "8"         | orders_7d/visitors_7d，小数率 |
| revenue_per_visitor_7d      | "10"        | 商品交易额/访客，元/人次      |
| ad_roi_7d                   | "8"         | 总归因成交额/总花费，倍数     |
| ad_click_through_rate_7d    | "4"         | 点击/曝光，小数率             |
| ad_click_conversion_rate_7d | "4"         | 总归因成交笔数/点击，小数率   |
| revenue_multiple_7d         | "14"        | 本/前7日商品交易额，倍数      |

product_scoring的policy.gates恰为`{current_window_days:I=7, previous_window_days:I=7, ad_impressions_min:D="2000", previous_orders_min:I=5, conversion_numerator_code:"orders", roi_basis_code:"total_attributed_revenue_over_total_cost", require_source_completeness:B=true, require_verified_reference:B=true, require_attribution_evidence:B=true}`。最后三项是**正式放量**证据门控，不关闭全包或全部数值试算。各项按自身覆盖、非负输入、正分母、相容标准独立判有效；推广三项另需曝光≥2000，增长需完整双7日、前期订单≥5、前期金额>0且达到本项真实growth_base_floor。有效增长倍数≤1得0分，不加100元候选线。M1-02未解时可显示已选口径来源比值/试算，正式放量进入pending_validation；不冒称归因已验。

### 有限分支与阈值

branches每项精确键为`{code, enabled, confirmation_status, validation_status, contract_ref, evidence_ref, depends_on_question_codes, predicate_code, thresholds, template_code}`。code/predicate_code均为下表有限S且v1相等，不接受公式、SQL或脚本。enabled为B；confirmation_status为confirmed/pending_confirmation；validation_status为verified_in_scope/pending_evidence；contract_ref为固定契约小节S；evidence_ref为S或NULL（未知）；depends_on_question_codes为有限问题S[]，无依赖为[]；thresholds按下表；template_code为本包模板码或NULL。validation分支模板须NULL，其他启用展示分支须引用真实模板。

表列分支在所属域恰各一项，按本表顺序，十validation按指标表顺序；不靠JSON对象键顺序执行。enabled=true要求规则已确认、配置证据已核、evidence_ref非空、依赖问题已解决。业务未决须禁用并pending_confirmation；纯技术证据缺口用pending_evidence并禁用，不转成用户逐词审批。partially_confirmed包可运行其余已确认分支；实际输入覆盖仍独立校验。

| domain及code=predicate_code（固定顺序）                                                            | thresholds精确键、类型、值/NULL语义                                                                                                                                                                                                                                      | 运行边界                                                                                                                    |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| product_scoring：十个`validate_<metric_code>`                                                      | 各为`{}`，无隐藏参数                                                                                                                                                                                                                                                     | 按固定指标/gates和输入/标准判有效；收藏enabled=false、pending_confirmation、依赖M1-01                                       |
| product_scoring：unscored                                                                          | `{}`                                                                                                                                                                                                                                                                     | 无有效项/计算未完成；缺整份当期输入时不生成无时点评分版                                                                     |
| product_scoring：pending_validation                                                                | `{}`                                                                                                                                                                                                                                                                     | 非完整评分/曝光不足/正式放量证据未过，优先于数值结论；本分支可展示缺口，不因M1-01/02未回复禁用                              |
| product_scoring：optimize                                                                          | `{score_lt:D="50", review_window_days:I=7}`                                                                                                                                                                                                                              | 正式门控全过且分数<50                                                                                                       |
| product_scoring：hold                                                                              | `{score_gte:D="50", score_lt:D="75", observe_days:I=3}`                                                                                                                                                                                                                  | 50≤完整分<75                                                                                                                |
| product_scoring：fix_key_metrics                                                                   | `{score_gte:D="75", roi_min_tier:I=2, conversion_min_tier:I=2, click_conversion_min_tier:I=1, growth_multiple_gt:D="1"}`                                                                                                                                                 | ≥75但任一关键条件未过，收集全部短板                                                                                         |
| product_scoring：small_scale                                                                       | `{score_gte:D="75", roi_min_tier:I=2, conversion_min_tier:I=2, click_conversion_min_tier:I=1, growth_multiple_gt:D="1", budget_step_min:D="0.10", budget_step_max:D="0.20", observe_days:I=3, review_interval_days:I=1}`                                                 | 关键条件全部通过；预算比例仅原建议提示，不是自动动作                                                                        |
| product_risk：no_transaction                                                                       | `{visitors_min:I=100, orders_eq:I=0, priority:"P1", risk_score:D="100"}`                                                                                                                                                                                                 | V≥100且O=0，依契约首命中返回                                                                                                |
| product_risk：severe_order_conversion_drop                                                         | `{visitors_min:I=100, current_to_baseline_lte:D="0.70", priority:"P1", risk_score:D="90"}`                                                                                                                                                                               | 基准订单转化>0                                                                                                              |
| product_risk：severe_traffic_drop                                                                  | `{baseline_daily_visitors_min:D="30", relative_change_lte:D="-0.40", absolute_drop_min:D="30", blockbuster_revenue_fraction:D="0.20", blockbuster_previous_orders_min:I=10, blockbuster_priority:"P0", blockbuster_risk_score:D="95", priority:"P1", risk_score:D="85"}` | 爆品集合和订单门槛均过才升级；金额缺项仅阻断影响估算                                                                        |
| product_risk：moderate_order_conversion_drop                                                       | `{visitors_min:I=100, current_to_baseline_lte:D="0.85", priority:"P2", risk_score:D="70"}`                                                                                                                                                                               | 基准订单转化>0                                                                                                              |
| product_risk：moderate_traffic_drop                                                                | `{baseline_daily_visitors_min:D="30", relative_change_lte:D="-0.20", absolute_drop_min:D="20", priority:"P2", risk_score:D="65"}`                                                                                                                                        | 变化为小数率，绝对差为访客量                                                                                                |
| promotion：total_roi_drop、net_roi_drop（依次各一项）                                              | 各为`{current_spend_min:D="300", previous_spend_min:D="300", p2_drop_gte:D="0.15", p1_drop_gte:D="0.25"}`                                                                                                                                                                | 两期相容覆盖/记录、前ROI>0，下降≥15%且<25%为P2、≥25%为P1，总/净独立                                                         |
| promotion：spend_growth_underperformed                                                             | `{spend_growth_gte:D="0.20", revenue_growth_fraction_lt:D="0.5", current_spend_min:null, previous_spend_min:null, current_deals_min:null, previous_deals_min:null}`                                                                                                      | 四NULL只代表未裁决门槛，不等于不设门槛；enabled=false、依赖M2-03，不借用300元；答复后需新结构修订，v1不收猜测值             |
| promotion：spend_without_deals                                                                     | `{current_spend_min:D="300", deals_eq:D="0", priority:"observation"}`                                                                                                                                                                                                    | 有花费无成交只观察，不自动停投                                                                                              |
| promotion：ctr_down、cpc_up、cvr_down（依次各一项）                                                | 各为`{relative_change_boundary:D="0"}`                                                                                                                                                                                                                                   | CTR/CVR变化<0、CPC>0，仅给线索；两期有记录且正有效基数                                                                      |
| aftersales：refund_rate_change                                                                     | `{attention_change_gte:D="0.03", warning_change_gte:D="0.05", current_sales_denominator_min:D="5000", previous_sales_denominator_min:D="5000", current_refund_numerator_min:D="1000"}`                                                                                   | 三率各用其对应分子/分母；3至不足5百分点关注、≥5预警，金额单位元                                                             |
| aftersales：issue_share_change                                                                     | `{attention_change_gte:D="0.03", warning_change_gte:D="0.05", current_return_qty_min:D="30", previous_return_qty_min:D="30", current_issue_qty_min:D="10"}`                                                                                                              | 尺码/质量分别依已核词典及计数执行，未核只阻断依赖项                                                                         |
| aftersales：single_style_amount_review                                                             | `{top_limit:I=10, priority:"review"}`                                                                                                                                                                                                                                    | 同期间单款退款金额Top10复核，不单凭金额排名预警                                                                             |
| aftersales：mixed_risk_ranking、quality_quantity、target_application、primary_reason（依次各一项） | 各为`{}`                                                                                                                                                                                                                                                                 | enabled=false、pending_confirmation，分别依赖M5-01/02/03/04，不预填混排/计数/目标/优先级答案                                |
| aftersales：size_dictionary、quality_dictionary、refusal_dictionary（依次各一项）                  | 各为`{}`                                                                                                                                                                                                                                                                 | 实际词典/投影未核时enabled=false、pending_evidence；命中只作文本标签/线索，拒收不是物流阶段                                 |
| overview：sop_priority                                                                             | `{}`                                                                                                                                                                                                                                                                     | 有证据/人工确认严重质量或无法销售等P0，达预警且满足样本P1，达关注或高金额待复核P2，其余观察；只用相容域证据，不篡改M1原级别 |
| overview：same_object_grouping                                                                     | `{}`                                                                                                                                                                                                                                                                     | 同款多异常集中、父子金额不重加，不猜跨域对象映射                                                                            |
| overview：exception_count                                                                          | `{}`                                                                                                                                                                                                                                                                     | enabled=false、pending_confirmation、依赖M6-01；列表长度不冒充正式异常总计                                                  |

问题码全集为`M1-01/M1-02/M2-01/M2-02/M2-03/M3-01/M3-02/M3-03/M5-01/M5-02/M5-03/M5-04/M6-01`，分支只能引用真实依赖。M3-01已确认为A，业务确认依赖不再按待决阻断；问题码为历史/追溯保留。M2跨渠道合计、M3退款匹配/地区日期仍由负责域和解释门控，不新增业务规则或完成额表。overview不允许40/25/20/15权重、90/70/50分界、原型固定分或统一加权predicate；天气不在五域包内。

### 词典、建议模板与发布校验

非aftersales域dictionaries必须`[]`；aftersales域只允许下表四个dictionary_code且各一项。每项精确键为`{dictionary_code, enabled, validation_status, field_codes, entries, match_mode, conflict_policy_code, branch_code, evidence_ref}`。enabled为B、validation_status同分支、evidence_ref为S或NULL；field_codes为去重S[]，v1仅接受显式原列`source_return_reason`。额外source_analysis_fields投影键尚未核，不能用通配键开启；取得真实键后须在新结构修订逐键列出，原行和必要投影继续保留可核查。当前四词典均enabled=false、pending_evidence、entries=[]；未证实所需全字段时field_codes=[]，不把结构可支持的字段当实际配置。

| dictionary_code | branch_code        | output_code全集                                                     | conflict_policy_code                    |
| --------------- | ------------------ | ------------------------------------------------------------------- | --------------------------------------- |
| size_issue      | size_dictionary    | larger/smaller/unspecified                                          | single_or_conflict                      |
| quality_issue   | quality_dictionary | quality/workmanship/damage                                          | collect_distinct_labels                 |
| primary_reason  | primary_reason     | size/quality/product_mismatch/logistics/price/style/original_reason | first_ordered_match；M5-04未决禁用      |
| refusal_clue    | refusal_dictionary | refusal_clue                                                        | collect_distinct_labels；不作为物流事实 |

entries每项精确为`{entry_code:S, order:I, term:S, output_code:S, negative_terms:S[]}`；entry_code/order在本词典唯一，order从1连续增长。match_mode仅literal_substring/exact_text：对同一字段原文本按Unicode字面、区分大小写比较，不做正则、分词、同义词扩展或脚本；多字段逐字段匹配。negative_terms允许[]，只在同一字段命中否定词时抑制本entry，不全局否定其他字段、不推断自然语言语义。按order升序处理；first_ordered_match取首个未否定项，collect_distinct_labels对输出码去重，single_or_conflict在多种不同输出时返回conflict，不能取首项；无命中返回unmatched，original_reason只保原文本，不编归因。当前entries=[]时不实施匹配，所有输出为规则未验证，不伪装无命中。

上述是有限可校验的表达形状；真实词条、顺序、否定配置及冲突模式仍须用原配置/必要样例核验，未证实前不得以此形状制造新分类算法。primary_reason分支仍依M5-04；多质量标签可保候选，不能相加标签件数成总质量量，正式计数另依M5-02。技术词典待验由技术取证处理，不让用户逐词审批。

suggestions每项精确为`{template_code:S, branch_codes:S[], text:S, verification_metric_codes:S[], execution_mode:"display_only"}`。template_code包内唯一，代码全集为本域上述**非validation**分支码加`tpl_`前缀；无实际模板依据时不放入数组，并禁用相应展示分支。branch_codes非空且只引用本包已有分支，与分支template_code双向一致。text仅转录/规范化相应契约已核检查/观察提示，证据沿分支evidence_ref定位，不填原型占位负责人/绝对期限/进度。verification_metric_codes可为[]（原模板没列指标），非空时按domain封闭选择：product_scoring只用上述十个metric_code；product_risk只用`visitors/orders/order_conversion_rate/product_revenue`；promotion只用`total_roi/net_roi/spend/ctr/cpc/cvr`；aftersales只用`total_refund_rate/presale_refund_rate/aftersale_refund_rate/return_qty/return_amount/size_issue_share/quality_issue_share`；overview必须[]，它引用原域证据、不另造验证指标。对应谓词还必须实际提供同定义指标，不能用跨域同名值验证。没有任意未来模板/验证指标码；既有相对观察天数由分支阈值读取，不复制出真实期限。

**整体发布校验：**分支/指标/词典/模板代码唯一且引用闭合，固定十项总权重、各域分支全集、每键类型/单位/NULL形状、禁用原因/问题依赖、启用/证据状态一起校验。词典启用还要求字段非空且齐备、entries非空、关联分支启用及原配置/样例证据已核；当前未满足就保持禁用。原模板依据及有限指标代码不是业务数据齐备证明。整包确认状态由真实逐分支记录取得，不能把enabled=false的业务未决整包宣称confirmed。

JSON不存店铺/链接/目标成员FK或名单，不存日事实、四线、样本、13实际特征、结果分数或执行记录。标准从当前真实交付导入；历史动态生成/回退未恢复，不放不存在的回退参数分区。未知字段、规则或业务答案须形成新结构及包修订，不靠JSON偷偷加规则；[12项业务未决](../data-contract.md#business-metric-questions)仍由契约负责。

**不落结果的查询选择：**商品风险/健康度、推广SOP及中心查询使用部署配置按domain指定的明确不可变包ID；入口核对存在、用途/结构及已确认启用分支，不按最大修订或确认时间取一条。一次请求固定包ID和实际领域代码修订，二者与数据/解释版本一起纳入响应向量，跨请求变化则整组重取。评分/报告等保存产物仍从所属版本FK取得历史实际包，不受当前配置影响。配置本身按修订管理，当前没有自助规则维护页面或规则当前指针表。

**关联、删除与范式：**规则包1:N结果版本；标准不引用计分/建议包，不要求与评分结果版同包ID。参数与依据依赖`set_code+revision`，不保存与其无关的实体/经营行。完整参数文档是原子配置对象，JSON内部结构由版本化schema约束，1NF不用于掩盖未拆开的正式关系；没有独立指标主表及重复权重，满足本表职责下的2NF/3NF。被封存版本使用后RESTRICT、不可变，新参数/确认决定新建修订。

## `score_standards` 新品评分标准

粒度：一份标准版本中的一组有真实原商品身份、原品类和参照上下文的四档标准。当前已见固定显示基线按原身份保存，不因表结构存在就放宽为品类通用标准。

| 字段                   | 含义                         | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                       |
| ---------------------- | ---------------------------- | -------------- | ---- | ------------------------ | ----------------------------------------------- |
| id                     | 本标准修订标识               | TEXT           | 否   | PK                       | 引用后不可改义                                  |
| standard_code          | 标准系列的稳定代码           | TEXT           | 否   | UQ组合                   | 不以商品名作为身份                              |
| dataset_version_id     | 标准参数及出处的可重读版本   | TEXT           | 否   | FK → dataset_versions.id | 评分standard角色须指向实际使用版本              |
| listing_id             | 已核原绑定的店铺商品链接     | TEXT           | 是   | FK → listings.id         | 原身份未核则NULL，不能候选取首项                |
| source_listing_key     | 标准声明的原完整店铺商品定位 | TEXT           | 是   | —                        | 单一原身份文本，解释中保留各键含义              |
| source_category_system | 原绑定的品类体系             | TEXT           | 否   | —                        | 运营品类与模型品类分开                          |
| source_category_label  | 原绑定的运营品类原标签       | TEXT           | 否   | —                        | 与固定资料输入精确核对，不跨体系同名合并        |
| model_category_label   | 历史模型品类声明             | TEXT           | 是   | —                        | 标签证据，不造分类主对象                        |
| season_label           | 历史季节上下文               | TEXT           | 是   | —                        | 不是本次重新分类结果                            |
| lifecycle_label        | 历史生命周期上下文           | TEXT           | 是   | —                        | 不是当前实际阶段                                |
| basis_kind             | 标准依据类别                 | TEXT           | 否   | CHECK                    | fixed_display_trial/verified_reference/proposed |
| identity_match_status  | 原链接及品类核验状态         | TEXT           | 否   | CHECK                    | matched/unmapped/conflict                       |
| source_effective_date  | 有证据的标准适用起日         | DATE           | 是   | —                        | 不从版本号/登记日倒推                           |
| source_date_status     | 上述日期依据状态             | TEXT           | 否   | CHECK                    | confirmed/unknown                               |
| source_observed_at     | 实际观察标准出处的时刻       | TIMESTAMPTZ    | 是   | —                        | 未记录则NULL                                    |
| source_locator         | 原标准页面/交付的确切定位    | TEXT           | 否   | —                        | 可找到原身份及四线                              |
| evidence_note          | 原精度、适用边界及缺口       | TEXT           | 是   | —                        | 固定显示基线继续标试算                          |

**键与CHECK：**PK为`id`；UQ为`(dataset_version_id, standard_code)`。各状态取表列集合；`source_date_status=confirmed`当且仅当适用起日非空；`matched`要求`listing_id/source_listing_key`非空，原品类体系和标签均不得为空。`unmapped/conflict`可保留原证据，不进入有效判分。

**发布校验与基数：**版本1:N标准、链接1:N历史标准。指定原链接、原品类体系/标签、单位、季节/阶段适用依据、来源日期与本次评分必须相容；固定资料品类冲突或改变时不继续套旧线。标准角色必须包含该标准所属版本。四线导入冻结真实原输入/交付、解释及实际导入代码修订；指标语义签名由固定十项代码、特征定义、单位、7日窗口、分子分母/ROI依据及参照口径构成，须与实际评分包逐项相容。只改权重/建议不要求复制四线或共享包ID；特征/口径变化则重验相关线。当前真实历史生成算法未知时如实留缺口，不编造生成包或generation_rule_set_id。多套同时可用标准不取首末行，按明确选择规则核验，否则标标准冲突。原身份不清不能扩成全品类，新增通用标准须另有真实依据。

同系列标准在新完整标准批次可保留原参数而取得新行ID，批次内standard_code唯一；不要求未变化标准自增本地修订号。真实原上游修订若有，沿原交付及版本证据保留，当前不新增source_revision列。日期未知不反套历史；明确出处的当前固定基线只能按契约门控试算，不能因此输出已满足正式放量条件。标准版本封存时刻从所属版本取得，不重复登记为每套标准的来源日期。

**删除与范式：**被引用标准、参数版及分项RESTRICT，修订追加。标准键决定原身份与共同上下文，逐项参照拆到分项，满足1NF/2NF；规则参数和权重不复制，原标签是历史观察而非规范名称副本，符合3NF。

## `score_standard_items` 逐指标四线与参照

粒度：一个标准中的一个固定评分指标。当前值、权重和计分结果不存此表。

| 字段                      | 含义                         | PostgreSQL类型 | 可空 | 键/引用                         | 默认/说明                                              |
| ------------------------- | ---------------------------- | -------------- | ---- | ------------------------------- | ------------------------------------------------------ |
| standard_id               | 所属不可变标准               | TEXT           | 否   | PK组合；FK → score_standards.id | 共同身份和来源解释沿父表取得                           |
| metric_code               | 十项评分中的一个有限指标代码 | TEXT           | 否   | PK组合；CHECK                   | 使用rule_sets所列固定十项，不建指标主表                |
| basic_threshold           | 基础档下限                   | NUMERIC        | 是   | CHECK                           | 未交付/不可解释则NULL                                  |
| good_threshold            | 良好档下限                   | NUMERIC        | 是   | CHECK                           | 比较原精度                                             |
| excellent_threshold       | 优秀档下限                   | NUMERIC        | 是   | CHECK                           | 允许与相邻线相等                                       |
| outstanding_threshold     | 突出档下限                   | NUMERIC        | 是   | CHECK                           | 转化线不统一强限≤1                                     |
| growth_base_floor         | 交易倍数项的前7日金额基数线  | NUMERIC        | 是   | CHECK                           | 仅增长项适用，不填候选100元线                          |
| threshold_status          | 本项四线可用性               | TEXT           | 否   | CHECK                           | available/partial/missing/incompatible                 |
| precision_kind            | 阈值精度出处                 | TEXT           | 否   | CHECK                           | source_display_rounded/full_precision/unknown          |
| reference_group_label     | 本项实际参照组原声明         | TEXT           | 是   | —                               | 不是正式成员名单                                       |
| reference_category_system | 本项借用/参照的原分类体系    | TEXT           | 是   | —                               | 来源未明则NULL，不伪造统一分类                         |
| reference_category_label  | 本项借用/参照的原品类标签    | TEXT           | 是   | —                               | ROI借用品类在本项单列                                  |
| declared_sample_count     | 来源声明的参照样本数         | BIGINT         | 是   | CHECK                           | 未知款/特征行时不冒充去重款数                          |
| declared_sample_unit      | 原样本单位声明               | TEXT           | 是   | —                               | 未交付则NULL                                           |
| sample_basis_status       | 样本口径核验状态             | TEXT           | 否   | CHECK                           | verified_in_scope/declared_unverified/unknown          |
| history_start             | 有证据的参照历史起日         | DATE           | 是   | CHECK配对                       | 未交付不补日期                                         |
| history_end               | 有证据的参照历史止日         | DATE           | 是   | CHECK配对                       | 不是评分当期窗                                         |
| quantile_method           | 有证据的分位/插值方法        | TEXT           | 是   | —                               | P25标签不能代替完整算法                                |
| fallback_level            | 本项实际回退层级/声明        | TEXT           | 是   | —                               | 未发生/未知不造层级                                    |
| fallback_basis_status     | 回退依据状态                 | TEXT           | 否   | CHECK                           | not_used/verified_in_scope/declared_unverified/unknown |
| source_locator            | 本指标四线和参照的原定位     | TEXT           | 否   | —                               | 能从固定标准交付重读                                   |
| missing_reason            | 缺线、单位或参照冲突说明     | TEXT           | 是   | —                               | 缺标准不保存成0线                                      |

**键与CHECK：**PK/UQ为`(standard_id, metric_code)`，metric_code只能取固定十项。四线和基数线有值均有限；`available`要求四线齐全且非递减，允许相等；`missing`要求四线全空；`partial`要求有线但不齐；`incompatible`可留有限原参数但禁止使用。基数线有值≥0，非增长项必须为空；样本数有值≥0；历史起止配对且起≤止。状态按表列集合。非available须有缺项/冲突原因。

**发布校验与基数：**标准1:N分项；一个完整结构恰有固定十项，缺线仍留一行及状态，不能删掉该指标凑完整。增长项缺基数线只阻断该项；逐项参照和声明样本不得被父标准共同标签覆盖。`verified_in_scope`的样本需有单位和可核依据，借用品类的体系未知不能伪称已核。当前无可定位的实际回退参数，不设回退参数码。原回退声明、参照样本和四线继续保留，fallback_basis_status按实际证据填写；将来具备真实生成规则后再按版本绑定，不把原声明冒充已恢复的算法。

**删除与范式：**标准/分项引用均RESTRICT，旧分项不原地更新。全部参数及参照依赖完整标准—指标键；四列是四个固定有名档位，满足1NF/2NF。权重只在规则包，单位只沿固定指标定义及解释取得，没有多余标准分项ID、分类FK或重复规则版本FK，符合3NF。

## `product_scores` 商品评分对象及实际特征

粒度：一个评分结果版本下，一个完整店铺商品链接的实际计算结果。缺标准或无可判项时仍保留评分对象及原因，不补假分。

| 字段                     | 含义                          | PostgreSQL类型 | 可空 | 键/引用                          | 默认/说明                              |
| ------------------------ | ----------------------------- | -------------- | ---- | -------------------------------- | -------------------------------------- |
| id                       | 本评分结果定位                | TEXT           | 否   | PK                               | 详情用，不替代链接身份                 |
| dataset_version_id       | 本评分结果不可变版本          | TEXT           | 否   | FK → dataset_versions.id；UQ组合 | derived；输入、窗口及规则沿版本取得    |
| listing_id               | 被评分的完整店铺商品链接      | TEXT           | 否   | FK → listings.id；UQ组合         | 不复制店铺/商品名/运营                 |
| standard_id              | 本次实际适用标准修订          | TEXT           | 是   | FK → score_standards.id          | 无相容标准则NULL，不选候选首项         |
| visitors_7d              | 当期7日商品访客累计特征       | BIGINT         | 是   | —                                | 固定商品输入聚合，缺项不补零           |
| product_revenue_7d       | 当期7日商品交易额             | NUMERIC        | 是   | CHECK有限                        | 元，数仓原值，不额外扣退款/做单        |
| units_7d                 | 当期7日支付件数               | BIGINT         | 是   | —                                | 不代替订单/买家                        |
| favorites_7d             | 本次实际观察的7日收藏聚合特征 | BIGINT         | 是   | —                                | 定义未齐可封存观察合计，但不可正式计分 |
| buyers_7d                | 当期7日买家累计核对特征       | BIGINT         | 是   | —                                | 区别于评分订单分子，不冒充跨日去重     |
| orders_7d                | 当期7日订单累计特征           | BIGINT         | 是   | —                                | 支付转化分子，不用件数替代             |
| ad_spend_7d              | 当期7日推广总花费             | NUMERIC        | 是   | CHECK有限                        | 商品/推广先分别聚合                    |
| ad_attributed_revenue_7d | 当期7日总归因成交额           | NUMERIC        | 是   | CHECK有限                        | 总口径，不混净/结算额                  |
| ad_impressions_7d        | 当期7日推广曝光合计           | NUMERIC        | 是   | CHECK有限                        | impression_count，DOUBLE不截整         |
| ad_clicks_7d             | 当期7日推广点击合计           | NUMERIC        | 是   | CHECK有限                        | 保留来源实际精度                       |
| ad_deals_7d              | 当期7日总归因成交笔数         | NUMERIC        | 是   | CHECK有限                        | 不代替商品订单或买家                   |
| previous_revenue_7d      | 前7日商品交易额               | NUMERIC        | 是   | CHECK有限                        | 交易倍数分母                           |
| previous_orders_7d       | 前7日订单累计特征             | BIGINT         | 是   | —                                | 增长项5单门槛的实际输入                |
| current_lifecycle        | 本次有真实依据的阶段分类结果  | TEXT           | 是   | —                                | 当前未分类为NULL，不复制历史标准阶段   |
| lifecycle_reason         | 当前阶段判定依据或未知原因    | TEXT           | 是   | —                                | 缺上架日不以首次观察日替代             |
| decision_code            | 本次确定性门控的实际输出分支  | TEXT           | 否   | CHECK                            | 六分支代码，非人工处理状态             |
| decision_evidence        | 本次分支及关键门控的具体原因  | TEXT           | 是   | —                                | 绑定缺项/条件，不复制通用建议模板      |

**键与CHECK：**PK为`id`；UQ为`(dataset_version_id, listing_id)`。全部NUMERIC有值必须有限；六种`decision_code`限定为规则包所列集合。`current_lifecycle`为空须有未知原因，有值须有实际判定依据。所有特征无业务默认，完整区间的真实零才存0；发现有限异常负额/负计数时可封存实际特征，但依赖该值的分项必须不可计分，不靠全表非负CHECK抹掉证据。

**发布校验与基数：**结果版本1:N评分对象，链接1:N历史评分；每个评分可引用0或1个标准，一个标准可供多个评分使用。结果版本须为评分用途、`origin_kind=derived`并绑定唯一`product_scoring`规则包；输入角色/两个7日窗/对应来源解释及覆盖须一致。标准非空时，其指标语义签名、参照口径、原链接、原品类和适用日期均相容，标准版本必须是本结果`standard`输入。没有标准或标准冲突时不可出现valid分项。商品与推广分别聚合后关联，不做原明细多表JOIN再SUM。

13项聚合特征只在本父行保存一次，用于**审计本次真正进入计算的数值、观察合计和缺项**。它们是审计目的的派生持久化，不是因假设性能而加入的经营汇总。权威原值仍为固定输入事实；任务在同一输入快照内计算13特征、分项状态、当前阶段和建议分支，复核后与结果版一起同事务追加。输入、解释、资料、标准或规则变动产生新结果版；用原输入、角色窗口、冻结解释和确切代码修订可逐字段复算，不能在两个版本间拼特征。13特征和原因不替代原输入保留与独立恢复。

评分卡名称按固定product_current实际窗口及该评分绑定的当前资料关系版，使用[01的固定版本名称规则](01-master-data.md#固定版本读取与一行共识)；不得另读其他最新事实版回退名称。评分日、上架日期、首次观察日、观察天数及其范围沿版本角色/资料/实际历史查询取得，不重复保存。已知观察范围最早日不等于实际上架日。当前阶段未知时继续NULL，已有固定历史参照允许的试算也须明确身份与日期限制。收藏观察合计即使有值，M1-01未确认仍无正式区间结论/得分；M1-02或完整性/试算标准未验仍阻断正式放量结论。

总分、已计分得分、有效/待验证权重、完整/部分/未评分状态及达标项数均从固定十项派生，不落父表。`decision_code`及当前阶段是本次实际输出的审计结果，同事务封存并按同一规则复算核对；建议标签和共用模板查询时取得。`small_scale`仅为建议，不产生预算操作授权。

**删除与范式：**发布结果RESTRICT，历史资料变更不改旧评分。完整结果版本—链接键决定实际特征、标准选择及输出，没有店名、单位或权重的传递依赖，1NF/2NF/3NF的对象结构成立。上述13特征和实际分支是明确说明的审计派生保存，不能再以审计为名复制每项分母、阈值或总分。

## `product_score_items` 评分分项状态与缺项证据

粒度：一个评分结果对一个固定预期指标的实际有效性判定。十项始终存在，不因缺项删行。

| 字段                | 含义                         | PostgreSQL类型 | 可空 | 键/引用                        | 默认/说明                            |
| ------------------- | ---------------------------- | -------------- | ---- | ------------------------------ | ------------------------------------ |
| score_id            | 所属商品评分结果             | TEXT           | 否   | PK组合；FK → product_scores.id | 规则及标准沿父结果取得               |
| metric_code         | 固定十项中的指标代码         | TEXT           | 否   | PK组合；CHECK                  | 与标准分项使用同一有限集合           |
| signal_status       | 本项本次可用状态             | TEXT           | 否   | CHECK                          | valid/partial/missing/not_computable |
| missing_reason_code | 缺项/不可计分原因代码        | TEXT           | 是   | CHECK形状                      | 非valid必须有，valid为空             |
| missing_reason_text | 具体缺失字段、日期或标准解释 | TEXT           | 是   | —                              | 不将缺项保存成0值                    |
| evidence_context    | 本项核验所需非数值证据       | JSONB          | 是   | CHECK                          | 对象；仅下述四个封闭键               |

**键与CHECK：**PK/UQ为`(score_id, metric_code)`；metric_code只能取固定十项，状态取表列集合。非valid必须有原因代码，且具体原因文本/证据至少一项非空；valid的缺项原因代码与文本均为空。证据有值须为非空封闭对象，键全集恰为以下可选键，不允许其他键或JSON null：`input_role`为本节评分角色枚举中的一个字符串；`field_codes`为不重复、非空字段码数组，代码全集为本文件13个实际特征列名加`feature.`前缀，或score_standard_items中除standard_id/metric_code外的20个显式列名加`standard.`前缀；缺少原字段用该字段所供特征代码说明，不允许任意原列/对象键；`missing_dates`为不重复、升序ISO YYYY-MM-DD字符串数组，每日须为所述角色实际读取窗口中的真实缺口；`note`为非空说明字符串。数组/文本键出现时不得为空，missing_dates出现必须同时有input_role及其非空日窗口；不同角色缺口以具体原因文本分别陈述，不能塞额外嵌套对象。不得藏正式FK名单、实际分子分母、金额、阈值或权重。

**发布校验与基数：**评分1:N十项。每个评分恰有同一规则包预期十项；valid必须由固定输入、覆盖、非负有效值、分母、曝光/增长门槛及相容标准共同证明，不能手工改状态绕过检查。标准分项通过父结果`standard_id`加本行`metric_code`唯一定位；不用再复制标准ID/标准分项ID。父标准为空或相应线缺失时，不允许该项valid。普通FK不能证明这些跨表条件，实施时须在同一发布事务锁定父结果、固定标准及输入，并由不可绕过的发布过程/延迟约束完成。

当前值的派生路径：访客/金额/件数/收藏取父行对应特征；评分转化=`orders_7d/visitors_7d`，UV价值=`product_revenue_7d/visitors_7d`，ROI=`ad_attributed_revenue_7d/ad_spend_7d`，CTR=`ad_clicks_7d/ad_impressions_7d`，点击转化=`ad_deals_7d/ad_clicks_7d`，交易倍数=`product_revenue_7d/previous_revenue_7d`。分子分母都是父行一次保存的输入，不平均日比率。阈值从父标准同指标取得，权重从结果版规则包同指标取得。

只有valid项按未舍入值比较四线，取达到的最高档k（0～4，含等于、同线取最高），`points=weight×k/4`；有效交易倍数≤1为0分。其他状态的档位/points为NULL。至少一项有效时已计分得分为有效points之和，否则NULL；有效权重相加、待验证权重=100−有效权重；十项均有效才有完整总分，否则NULL；有部分有效为partial，无有效为unscored，不将部分分归一到100。列表分组、排序和数量使用同一固定结果版派生。

**删除与范式：**FK RESTRICT，已发布状态不改，修订新建结果版。完整评分—指标键决定状态及证据，满足1NF/2NF；当前值、分母、四线、权重、单位、档位、points及总分均不重复存储，符合3NF并保持单一复算路径。valid只表明本项输入和数学门控通过，不冒充来源/模型业务验收。

## `aftersales_reports` 售后报告头

粒度：一份真实类型、选定期间及修订的售后报告身份，对应唯一结果版本。它不保存Q/A/S/P、各分组结果或建议明细。

| 字段                         | 含义                             | PostgreSQL类型 | 可空 | 键/引用                      | 默认/说明                                 |
| ---------------------------- | -------------------------------- | -------------- | ---- | ---------------------------- | ----------------------------------------- |
| id                           | 本份报告修订标识                 | TEXT           | 否   | PK                           | 同期间修订新身份，不覆盖旧报告            |
| dataset_version_id           | 本报告固定输入及规则的结果版本   | TEXT           | 否   | UQ；FK → dataset_versions.id | derived；每版最多一个报告头               |
| report_type                  | 实际报告类型                     | TEXT           | 否   | CHECK                        | weekly/monthly/custom；不以类型猜周期长度 |
| period_start                 | 本报告选定起日，含当日           | DATE           | 否   | CHECK                        | 不是事实完整覆盖证明                      |
| period_end                   | 本报告选定止日，含当日           | DATE           | 否   | CHECK                        | 滚动30天保留真实端点                      |
| revision_no                  | 同类型/期间内本地修订序号        | BIGINT         | 否   | CHECK                        | ≥1，显式分配，不从文件顺序猜              |
| source_report_key            | 来源已有的报告身份               | TEXT           | 是   | —                            | 无原身份则NULL                            |
| source_label                 | 来源报告原标签                   | TEXT           | 是   | —                            | 不用标签反推实际日期                      |
| source_generated_text        | 来源实际生成时间原文本           | TEXT           | 是   | —                            | 保留原精度/原时区线索                     |
| source_generated_at          | 有依据规范化的来源生成时刻       | TIMESTAMPTZ    | 是   | —                            | 无时区/不可解析时不猜                     |
| source_generated_time_status | 来源生成时间解释状态             | TEXT           | 否   | CHECK                        | confirmed/unparsed/unknown                |
| source_generated_time_note   | 原时间无法规范化的原因/解释      | TEXT           | 是   | —                            | 不把本地封存时刻冒充来源时间              |
| document_url                 | 实际飞书报告文档链接             | TEXT           | 是   | —                            | 无链接为NULL，前端禁用                    |
| sheet_url                    | 实际分析表格链接                 | TEXT           | 是   | —                            | 不保存#占位或含凭据URL                    |
| previous_report_id           | 本次实际绑定的同类型上期报告     | TEXT           | 是   | FK → aftersales_reports.id   | 绑定具体修订，不随新回补自动重绑          |
| previous_comparison_status   | 上期比较可用状态                 | TEXT           | 否   | CHECK                        | available/missing/incompatible/pending    |
| previous_comparison_reason   | 上期缺失/不相容/未判定原因       | TEXT           | 是   | CHECK形状                    | 非available必须说明                       |
| yoy_report_id                | 本次实际绑定的去年同类型同期报告 | TEXT           | 是   | FK → aftersales_reports.id   | 比较日期映射须有依据                      |
| yoy_comparison_status        | 去年比较可用状态                 | TEXT           | 否   | CHECK                        | available/missing/incompatible/pending    |
| yoy_comparison_reason        | 去年比较缺失或不相容原因         | TEXT           | 是   | CHECK形状                    | 不补造去年报告                            |
| evidence_note                | 真实范围、修订、比较及必要限制   | TEXT           | 是   | —                            | 具体输入覆盖沿版本/覆盖记录取得           |

**键与CHECK：**PK为`id`；UQ为`dataset_version_id`，期起≤止，修订号≥1；状态取表列集合。本表逻辑用途固定为aftersales_report，逻辑报告键为`(report_type, period_start, period_end)`；修订直接UQ为`(report_type, period_start, period_end, revision_no)`，不再把跨表dataset_code冒称普通UQ。两种比较均禁止自引用；`available`要求对应FK非空，`missing/pending`要求对应FK为空，非available必须有原因；`incompatible`可保留实际找到但不能比较的报告引用及理由。来源生成时间`confirmed`要求规范时刻非空，`unparsed`要求原文本非空、规范时刻为空且有原因，`unknown`要求两者均空；有链接时须为实际可用且无凭据URL。

**发布校验与基数：**结果版本1:0..1报告，每份报告必须有唯一结果版；报告各0..1引用上期/去年报告，被比较报告可被多份后续报告引用。结果版须为dataset_code=aftersales_report、derived，实际J1/J2/J3及必要规则/目标均经`version_inputs`固定。报告选定期间、各输入实际使用窗、日期依据、分类/款码匹配、量纲及覆盖一起校验。J1按确认时间、J2/J3按订单支付时间组织；来源头有整交付期间不等于每组均齐全，实际覆盖/完整日仍从固定覆盖证据取得，不能用报告期间或生成成功冒充完整。

本地生成报告的真实生成/封存时刻取结果版本`sealed_at`，不复制到来源生成时间列。迁入历史报告时只有真实原时间才填原文本/规范时刻；顶部历史更新时间、来源更新时间和每份报告生成时间分开。weekly实际30天仍显示“滚动30天，非自然周”，monthly按真实自然月输入核验，不从标签拆出伪日期。

**期间及修订选择：**每逻辑期间的默认修订仅在已发布且校验通过的候选中取最大本地revision_no，其他修订完整保留可查。期间列表与近8份趋势先各期间选默认修订，再按period_end降序；同止日技术顺序为monthly、weekly、custom，再按period_start降序、id升序稳定排序，趋势展示时可反转时间方向。一个期间不能因多次修订占据多期。默认报告入口和current_datasets.aftersales_report遵守同一顺序；新旧期均可发布，但后完成的旧期补报不能使当前入口退回旧期。

环比只从同report_type、period_end严格小于本报告period_end的实际期间中，按上述期间顺序取最近一期，再取该期默认修订；本逻辑期间所有旧修订均排除，不按修订号、生成时间或文件顺序找上期。最近候选不相容时绑定它并标incompatible，不悄跳更早一期；无候选为missing。允许滚动窗重叠和缺期，展示实际两期起止，不补造等距期间、不把重叠报告相加。同比按同类型、经核去年对应起止端点精确选期间及其默认修订；闰日或映射无依据标不可比，无对应期间为missing。比较两侧须为已发布、可重读版本，指标/范围解释相容；对象配对使用[02完整分组键及唯一分母规则](02-business-facts.md#aftersales-group-matching)，不能仅按style_code匹配。

previous/yoy具体修订FK、状态和原因随本报告封存，禁止比较引用环；之后补报、修订或回补不暗改已存绑定，重新评价须产生本报告新修订。历史记录可显式查旧修订，但“同期间修订差异”必须与“前后期间经营变化”分开，不能把同期间R1当R2上期。

报告主体、原label/真实原生成时间/外链、所选比较修订及其状态/原因、实际输入/规则/代码/解释均参加[04报告语义指纹](04-versioning.md)；本地id、revision_no、排队/创建/封存时间及尝试观测不参与。先按语义指纹复用已有版本/报告，确有语义变化才在逻辑报告键的并发锁内分配下一revision_no；不能先递增修订再以它制造每次重试都不同的语义摘要。本地生成报告时刻仍只取版本sealed_at。

**查询与持久化边界：**销售/退货完整分组、合法汇聚及销售分母唯一归属仅见[02](02-business-facts.md#aftersales-group-matching)，本节不另定义一份组键或细组匹配规则。J1保留实退数量Q/实际入库金额A，J2角色的销售额为S，J3角色为P；G=S+P、T=P+A及三率从相容角色独立聚合后计算。全量品类、单款、原因、尺码、质量标签、阶段、比较、风险和建议均由共享领域查询生成，不再新增报告分组/原因/标签/阶段/风险/动作结果表。必要原分类、子分类、虚拟分类、款码及原文本仍保留在02原行；只存TopN不能恢复总体，Top仅是展示限制。固定规则/词典随报告结果版重读，不用“当前配置”重解释旧报告。

未发货金额P保留“已取消销售代理”依据，关键词拒收只作线索；缺物流事实的签收后退货不以相减补造，阶段数量/金额缺证据为NULL。质量主原因与多标签计数、原因优先级、混排评分和目标应用继续遵守M5四项未决；可独立成立的Q/A/S/P与已定三率不因此伪造风险分。没有人工处理或AI产物时，界面仅展示有依据的建议及未启用状态，不建持久化动作/诊断表。

**历史、删除与范式：**每份已发布报告及其原输入保持可读，历史记录可显式读取旧修订，期间列表/近8期实退数量趋势依上述默认修订选择并按各自固定版本查询；当前指针只决定默认最新入口，不能使旧报告历史读取返回当前版本失效。被引用的报告、比较对象及版本均RESTRICT，不覆盖同期间旧修订。报告键决定自身类型、选定期间、原标签/原时间/外链和固定比较绑定；没有日事实、重复封存时间或汇总量，满足1NF/2NF/3NF。报告头不是不可恢复的历史汇总副本，真实输入保留/修订及恢复仍须另行验证。

## 静态设计与后续验证边界

本文件严格为11表；实体只引用当前范围中的`shops/listings`，正式目标成员仍拆行。规则包集中权重/门槛/词典/建议参数，标准分项保存逐项四线/样本/回退，评分父行保存13项真实聚合特征，分项保存状态/缺因，售后只保存报告头及比较关系。没有为页面计数、列表Top、同比环比或建议另建存储表。

字段/键/FK、NULL及状态形状、同角色单版、输入/标准/规则相容、13特征复算、目标范围完整性、历史报告重读及M3方案A的实际范围/覆盖，须分别进入后续实施与验收。当前仅核对Markdown字段、引用及封闭结构文本，不声称PostgreSQL约束、真实输入对账、迁移恢复或查询性能已通过；实施授权与记录统一见[TODO](../../TODO.md)及[验证文档](../validation.md)。
