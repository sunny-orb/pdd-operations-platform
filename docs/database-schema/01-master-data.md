# 店铺、链接与商品资料字段设计

本文件维护首期范围中的4张物理表：`shops`、`listings`、`profile_source_records`、`profile_listing_links`。规范身份只有店铺和店铺商品链接；运营、品类、等级、款号、实际上架日期及上架状态是来源资料属性。资料共识由固定版本查询计算，不再持久化一份链接展示资料。

这是字段与关系字典；结构由[独立迁移](migration-plan.md)维护，来源接入仍须另行验收。业务依据为[数据契约3.5.1](../data-contract.md#351-m1-商品经营整页含-m4-新品评分)及[4.1](../data-contract.md#41-公共身份与关联基数)，设计流程见[数据库设计流程](../database-design.md#database-design-process)，公共类型与版本规范见[本目录说明](README.md#统一字段规范)及[版本设计](04-versioning.md)。来源未证、当前资料视角和历史有效性限制继续以数据契约为准。

公共ID、自动时刻及软删除规则见[统一字段规范](README.md#common-field-policy)；本文件只维护实际列和对象约束。所有PK/FK均使用`TEXT`。规范ID、版本ID及原行定位键必须非空且无首尾空白；来源原格保留NULL、空串、原空白和混合文字。业务日期使用`DATE`，真实事件时刻使用`TIMESTAMPTZ`；未解析的来源日期原格仍是`TEXT`，不能为了满足类型而补造日期。普通CHECK只检查本行；下文涉及版本、输入角色和其他行的条件，均属于发布事务中的集合校验。

## `shops` 经营店铺

粒度：一个已经核实的统一经营店铺。一个店铺可有多个链接、多个店铺事实；当前规范店名在本表维护，来源原名和别名留在各自原行及冻结的来源解释中。

| 字段         | 含义                         | PostgreSQL类型 | 可空 | 键/引用 | 默认/说明                                                     |
| ------------ | ---------------------------- | -------------- | ---- | ------- | ------------------------------------------------------------- |
| `id`         | 项目统一店铺ID               | TEXT           | 否   | PK      | 无；应用生成，稳定身份                                        |
| `shop_name`  | 已核实的当前规范店铺名称     | TEXT           | 是   | —       | 无；无可靠名称时NULL，不造“未知店铺”                          |
| `status`     | 该身份在本项目中的启用状态   | TEXT           | 否   | —       | 无；CHECK `active/inactive`                                   |
| `created_at` | 本项目首次建立该身份的时刻   | TIMESTAMPTZ    | 否   | —       | `transaction_timestamp()`；真实本地建立事件                   |
| `updated_at` | 本项目最后修订该主记录的时刻 | TIMESTAMPTZ    | 否   | —       | DEFAULT transaction_timestamp()；实际变更时由数据库触发器维护 |
| `deleted_at` | 本项目将该身份软删除的时刻   | TIMESTAMPTZ    | 是   | CHECK   | 默认NULL；受控软删除时由数据库赋时刻，不是来源停业日          |

- **PK：**`id`。
- **UQ：**不按名称唯一。同名、改名或来源别名均不能单独证明是同一店铺；已核映射不得为同一实际店铺建立重复规范身份。
- **CHECK：**`id = btrim(id) AND id <> ''`；`shop_name IS NULL OR btrim(shop_name) <> ''`；`status IN ('active','inactive')`；`updated_at >= created_at`。deleted_at非空要求status=inactive且created_at≤deleted_at≤updated_at。`status`只表示项目身份是否启用，不表示来源经营状态；inactive不代表已经软删除。
- **跨表及发布校验：**建立身份及来源映射须有已核证据。六店映射沿数据契约4.1，冻结于实际使用版本的`interpretation_ref`及适用规则；不根据“雨具/箱包”等列别名另造店铺，不在本表重复来源配置、映射版号或完整性时间。
- **关系与删除：**`shops 1:N listings`，FK在`listings.shop_id`；店铺事实及目标成员等在各自子表引用本表。全部引用默认`ON DELETE RESTRICT`；停用修改status，后台隐藏/恢复使用deleted_at，不级联修改链接、事实或历史结果。默认新建对象选项排除软删除店铺，历史事实仍能按原FK读取；软删除及恢复不重用id或另建同一店铺身份。软删除不会替代来源下架状态或历史操作日志。
- **范式：**`id → shop_name,status,created_at,updated_at,deleted_at`。1NF每列一个店铺属性；单列PK满足2NF；不存来源别名清单、链接数、当前指标或资料属性，不形成这些属性的传递依赖，满足3NF。当前名称不是历史有效期，建立时刻也不是店铺开业日。

## `listings` 店铺商品链接

粒度：一个平台、一个统一经营店铺内的一个平台商品链接。身份由平台、店铺和平台商品ID共同确定，链接可以没有运营资料；名称、款号和实际上架日期都不参与身份判定。

| 字段                  | 含义                           | PostgreSQL类型 | 可空 | 键/引用       | 默认/说明                                                     |
| --------------------- | ------------------------------ | -------------- | ---- | ------------- | ------------------------------------------------------------- |
| `id`                  | 项目链接ID                     | TEXT           | 否   | PK            | 无；应用生成，稳定身份                                        |
| `shop_id`             | 链接所属统一店铺               | TEXT           | 否   | FK → shops.id | 无                                                            |
| `platform_code`       | 已核实的平台命名空间           | TEXT           | 否   | —             | 无；取值及来源解释固定，不能由站名猜测                        |
| `platform_product_id` | 该店铺内的平台商品ID           | TEXT           | 否   | —             | 无；字符串，不能转浮点或丢前导零                              |
| `status`              | 该身份在本项目中的启用状态     | TEXT           | 否   | —             | 无；CHECK `active/inactive`                                   |
| `created_at`          | 本项目首次建立链接身份的时刻   | TIMESTAMPTZ    | 否   | —             | `transaction_timestamp()`；不是实际上架日                     |
| `updated_at`          | 本项目最后修订链接主记录时刻   | TIMESTAMPTZ    | 否   | —             | DEFAULT transaction_timestamp()；实际变更时由数据库触发器维护 |
| `deleted_at`          | 本项目将该链接身份软删除的时刻 | TIMESTAMPTZ    | 是   | CHECK         | 默认NULL；受控软删除时自动赋时刻，不是来源下架日              |

- **PK：**`id`。
- **UQ：**`UNIQUE(platform_code,shop_id,platform_product_id)`，三列均非空；不单独按商品ID、名称或款号跨店合并。
- **CHECK：**`id/shop_id/platform_code/platform_product_id`分别满足`值 = btrim(值) AND 值 <> ''`；`status IN ('active','inactive')`；`updated_at >= created_at`；deleted_at非空要求status=inactive且created_at≤deleted_at≤updated_at。来源数字ID始终是字符串，不能用数值转换消除不同原标识。
- **跨表及发布校验：**`shop_id`必须指向已核店铺，平台及商品命名空间与固定来源解释相容。`id → shop_id,platform_code,platform_product_id`是稳定身份依赖；迁店、换平台或身份重建须隔离核查，不能更新身份键使既有历史事实换属。来源下架文字不驱动身份删除或事实删除。
- **关系与删除：**店铺1:N链接；链接1:N商品事实、推广事实及评分等；资料原行与链接通过下述关系表组成N:M。规范链接事实只保存`listing_id`，不重复由链接决定的规范店铺、平台和商品ID；必要来源原键另以`source_*`证据语义保留。全部引用默认`ON DELETE RESTRICT`。软删除只隐藏后台可维护身份及新建选项，历史查询仍能读取原链接及经营事实，不在公共历史JOIN中一律加deleted_at IS NULL；停用/软删除均不级联删除历史。业务UQ仍覆盖软删除行，恢复原身份，不释放同平台/店铺/商品键重建重复身份。
- **范式：**主键及完整业务候选键决定其余属性。1NF单链接单行；2NF属性依赖完整平台—店铺—商品键；3NF不存店名、运营名、品类、等级、款号集合、来源上架状态或实际上架日，避免将当前资料当作链接永久属性。

## `profile_source_records` 商品资料原行

粒度：一个固定来源资料版本中的一条必要原行投影。逻辑数据集为`profile_records`，只允许`origin_kind=source`；来源版本1:N本表。原生来源主键和版本内定位键分开：前者有则保留，后者必需，不能按内容哈希合并本来不同的同值行。解析、映射或日期适用判断改变不复制本表原格，统一产生新的`profile_links`派生关系版。

| 字段                                   | 含义                           | PostgreSQL类型 | 可空 | 键/引用                  | 默认/说明                                |
| -------------------------------------- | ------------------------------ | -------------- | ---- | ------------------------ | ---------------------------------------- |
| `id`                                   | 本版本中的资料原行ID           | TEXT           | 否   | PK                       | 无；应用生成                             |
| `dataset_version_id`                   | 固定必要原行投影的来源资料版本 | TEXT           | 否   | FK → dataset_versions.id | 无；`dataset_code=profile_records`       |
| `source_row_key`                       | 本份输入内可重读的原行定位键   | TEXT           | 否   | —                        | 无；版本内确定，不按内容去重             |
| `source_record_id`                     | 来源原生主键，如W01的`__id`    | TEXT           | 是   | —                        | 无；来源提供时保留，不以业务编号代替     |
| `source_product_name_raw`              | 运营商品名原格                 | TEXT           | 是   | —                        | 无；W01 `spmc`                           |
| `source_category_raw`                  | 运营品类原格                   | TEXT           | 是   | —                        | 无；W01 `pl`，不等于平台或聚水潭分类     |
| `source_level_raw`                     | 产品等级原格                   | TEXT           | 是   | —                        | 无；W01 `cpdj`，不在原行推算新品标志     |
| `source_operator_raw`                  | 负责运营原标签                 | TEXT           | 是   | —                        | 无；W01 `fzyy`，不是人员ID或真实指派     |
| `source_listed_at_raw`                 | 实际上架日期/时间的原表示      | TEXT           | 是   | —                        | 无；W01 `sjsjsj`，非法和适用性未知仍保留 |
| `source_listing_status_raw`            | 来源上架/下架状态原格          | TEXT           | 是   | —                        | 无；W01 `zt1`，不是删除指令              |
| `source_style_codes_raw`               | 款号原格完整内容               | TEXT           | 是   | —                        | 无；W01 `spkh`，多款原串只作来源证据     |
| `source_listing_ids_official_raw`      | 官方店ID列原格                 | TEXT           | 是   | —                        | 无；W01 `spigf`，整格保留                |
| `source_listing_ids_apparel_raw`       | 服饰店ID列原格                 | TEXT           | 是   | —                        | 无；W01 `spifs`，整格保留                |
| `source_listing_ids_outdoor_raw`       | 户外店ID列原格                 | TEXT           | 是   | —                        | 无；W01 `spihw`，整格保留                |
| `source_listing_ids_raingear_raw`      | 雨具别名列ID原格               | TEXT           | 是   | —                        | 无；W01 `spiyj`，实际店铺沿已核映射      |
| `source_listing_ids_luggage_raw`       | 箱包别名列ID原格               | TEXT           | 是   | —                        | 无；W01 `spixb`，保留下架/铺货混合文字   |
| `source_listing_ids_kids_footwear_raw` | 童装童鞋店ID列原格             | TEXT           | 是   | —                        | 无；W01 `spitztx`，不从混串抽数字        |

- **PK：**`id`。
- **UQ：**`UNIQUE(dataset_version_id,source_row_key)`。本表不对商品ID、款号或原生来源ID另设无条件唯一；W01已证原生主键在相应版本中重复时，作为发布错误保留核查，不取首末行去重。`source_record_id`不是跨版本身份，也不代替`source_row_key`。
- **CHECK：**`id/dataset_version_id/source_row_key`分别非空且无首尾空白；`source_record_id`非NULL时非空且无首尾空白；13个原格不加“非空白”CHECK，以便保留来源NULL/空串/原空白的差异。不增设解析JSON列或全量词元副本。
- **跨表及发布校验：**版本必须属于`dataset_code=profile_records`且`origin_kind=source`，`interpretation_ref`冻结本次必要字段投影、原值表示、来源命名空间及缺失/修订删除解释。W01与同源F02在同一逻辑范围只启用一个，不能把两份原行混进一份有效资料版。来源未提供字段由版本解释区分`not_provided`，不是由某行NULL猜测。不在原行重复来源、接收时刻或规则版号；`rule_set_id`保持NULL。解析或映射更正不得伪装成来源修订。
- **关系与删除：**原行通过`profile_listing_links`可关联多个已核链接，一个链接也可关联多条原行。版本和原行被引用时`ON DELETE RESTRICT`；发布后原格封存。只有真实来源修订、回补、删除才生成新的来源资料版本，须核对完整范围、保留及删除语义；未读到不等于删除，不清理已引用原行。原行输入没有变化时重解释只发布新关系版，不改原行ID或复制全部原格。
- **范式：**`id`及`(dataset_version_id,source_row_key)`决定本次原行投影。1NF中每个原格的领域是一个来源文本值，不用其分隔串直接表示正式关系；正式原行—链接关系逐行落在关联表。2NF属性依赖完整版本原行键；3NF不复制规范店名、链接属性、版本级来源/时间/规则、可重算词元或经营度量。必要来源原格是不可变证据，不是链接资料共识的另一份物化结果。

### 从固定原格重放解析与诊断

解析按关系版不可变的`interpretation_ref`及`producer_revision`重放，始终读取其唯一`profile_records`输入指向的来源原行。返回每个必要原字段的解析状态、问题及原格定位；未匹配行也必须可独立查询，不能只有成功关系可查。固定输入和解释所需材料须在保留范围内可重读；引用失效或重放失败明确返回不可用，不能假称“无问题”。

- 已投影字段的NULL/去首尾空白后为空派生为`missing`；版本没有投影该字段才为`not_provided`。不支持的原表示为`unsupported`，非法值为`invalid`；冲突保留`conflict`及全部候选。这些状态、问题码和诊断仅为API/重放结果，不新建表或逐行JSON。
- 六个ID原格仅接受整串纯数字单ID，或以换行、斜杠、分号分隔的纯数字列表；冻结解释明确分隔和首尾空白规则。整格全部通过才产生候选标识，前导零不丢失；同格重复标识不复制正式关系。逗号、下架/铺货混串、空项分隔或其他非数字文本不局部抽取ID，返回`unsupported/invalid`与`unsupported_separator/mixed_text/invalid_token`等具体原因，原格全文仍可读。
- 款号按本来源已核规则解析；命名空间或拆分规则未核时保留原格并返回`style_namespace_unverified`或`unsupported`，不得把数字片段当链接ID或跨源统一款式。
- 日期只从固定`source_listed_at_raw`按已冻结原表示、时区/日历边界解析；非法日期返回`invalid_date`，时区未核返回`date_timezone_unverified`，不造日期。能解析出来源日期仍不证明该日期适用于某个链接，逐关系适用性由下表保存。
- 店铺映射未核/冲突、标识未匹配及日期适用性未证分别保留具体诊断，如`shop_mapping_unverified/shop_mapping_conflict/listing_unmatched/date_applicability_unverified`，不得建占位链接或丢弃未成功原格。必要发布失败与检查范围写入`dataset_versions.validation_evidence_ref`；原行键、字段和固定解释足以定位并重放具体失败，不记录凭据、堆栈或无关完整行。

来源不变时，首次解析、改六店映射、修复解释或重判日期适用性均只生成新的`origin_kind=derived`、`dataset_code=profile_links`版本，直接通过唯一`version_inputs.input_role=profile_records`锁定原`source`版本；关系引用原`profile_record_id`。不会生成`derived profile_records`、另一份全量原格或词元表。新来源输入再修订关系版；历史查询与评分仍固定旧输入及解释，不随当前关系更正而变化。

## `profile_listing_links` 资料原行与链接关系

粒度：一个固定关系结果版本内，一条资料原行与一个已核链接的一项关系核验结果。逻辑数据集为`profile_links`，必须为`origin_kind=derived`；原行与链接的正式匹配为N:M。没有已核目标的原标识仍留在原行，不为其制造关系或链接身份。

| 字段                               | 含义                             | PostgreSQL类型 | 可空 | 键/引用                        | 默认/说明                                              |
| ---------------------------------- | -------------------------------- | -------------- | ---- | ------------------------------ | ------------------------------------------------------ |
| `id`                               | 本版本的原行—链接关系ID          | TEXT           | 否   | PK                             | 无；应用生成                                           |
| `dataset_version_id`               | 固定关系结果与规则的版本         | TEXT           | 否   | FK → dataset_versions.id       | 无；`dataset_code=profile_links`                       |
| `profile_record_id`                | 该关系涉及的固定资料原行         | TEXT           | 否   | FK → profile_source_records.id | 无                                                     |
| `listing_id`                       | 该关系涉及的已核店铺商品链接     | TEXT           | 否   | FK → listings.id               | 无；不重复规范店铺、平台和商品ID                       |
| `relationship_status`              | 本关系的核验结果                 | TEXT           | 否   | —                              | 无；CHECK `matched/rejected`                           |
| `relationship_reason`              | 确认或排除关系的可核查依据       | TEXT           | 否   | —                              | 无；必填说明，不存其他目标ID或ID集合                   |
| `listed_date_applicability_status` | 本原行日期对本链接的适用状态     | TEXT           | 否   | —                              | 无；CHECK `confirmed/not_applicable/unknown/conflict`  |
| `listed_date_evidence_ref`         | 日期适用/排除/冲突的固定证据引用 | TEXT           | 是   | —                              | 无；confirmed/not_applicable/conflict必填，unknown可空 |

- **PK：**`id`。
- **UQ：**`UNIQUE(dataset_version_id,profile_record_id,listing_id)`。同原格重复同ID、多个原格支持同一目标时只形成一条关系；全部支持原格可重读，所有出现位置及诊断可按固定解释重放，不取一个“代表词元”覆盖其余证据。
- **CHECK：**`id/dataset_version_id/profile_record_id/listing_id`分别非空且无首尾空白；`relationship_status IN ('matched','rejected')`；`btrim(relationship_reason) <> ''`；日期状态限`confirmed/not_applicable/unknown/conflict`，不设业务默认。`listed_date_evidence_ref`非NULL时须非空且无首尾空白；日期状态不是`unknown`时证据引用必填；`confirmed`要求本行`relationship_status=matched`；`rejected`要求日期状态为`not_applicable`。本行约束不能代替证据内容和真实日期校验。
- **跨表及发布校验：**关系版必须以`version_inputs.input_role=profile_records`恰好固定一份`dataset_code=profile_records`、`origin_kind=source`版本；每个`profile_record_id`必须属于这份输入，不能混入其他原行版。解析、六店映射、日期适用及共识规则由关系版不可变`interpretation_ref`与`producer_revision`冻结；资料原行/关系这两个用途的`dataset_versions.rule_set_id`为空，不借用评分/风险包，也不在关系行重复规则引用。`matched`必须由整格严格通过的标识、已核六店映射及相容平台命名空间到达相同链接业务键；所有支持原格共同复核，不能仅凭同商品ID跨店匹配。`rejected`只记录对一个已核存在目标的明确排除依据，不能构造未知目标或候选列表。
- **逐关系日期校验：**`confirmed`须原格真实可解析、关系`matched`，且固定证据明确证明此日期对该链接适用；不得把“整条资料有日期”或“标识已匹配”当适用证据。`not_applicable`须固定依据明确排除此原行日期对该链接的适用；`conflict`须能定位矛盾适用依据，均不得用于输出日期。`unknown`用于缺失、未提供、非法、解释未核或适用性未证，证据可空，但具体解析问题仍须重放可查，不得出日期。`rejected`的`not_applicable`引用实际关系排除依据，不伪造日期或另造无业务依据的证据。引用内容须不可变、可重读并定位原行/链接及判断依据；发布前检查引用内容与状态相符，不能用任意非空文本通过校验。关系不另存日期值，只有需要展示时从固定原格解析。
- **关系与删除：**关系版1:N关系；原行1:N关系，链接1:N关系，组合表达原行↔链接N:M。版本、原行和链接FK均`ON DELETE RESTRICT`。删除某关系不得级联删除原行、链接、事实或其他版本；关系修订重新生成结果版并原子发布，不修改封存版。
- **范式：**`id`及完整`(dataset_version_id,profile_record_id,listing_id)`键决定关系状态、日期适用状态和各自依据。1NF单关系单行；2NF核验属性依赖完整版本—原行—链接键；3NF不重复店铺、商品ID、原行资料、来源配置、任务时刻或经营度量。关系状态用于决定是否可参与共识，不另存可推导的链接总体资料状态。

### 固定版本读取与一行共识

评分/查询的`profile`输入角色指向`profile_links`版，再沿该版唯一的`profile_records`输入角色取得不可变来源原行。默认入口可由当前指针选择，但一次查询或一个保存产物必须固定这一版本路径及其实际解释/代码修订；不能再读另一份“最新原行”。解析、共识、日期及名称回退所用的固定事实输入、窗口、解释和代码修订一并纳入响应版本向量；两个资料用途的`rule_set_id`仍NULL。当前资料视角用于历史查询时继续明确标“按当前资料归属”，不伪造历史业务有效期。

对每个链接，取该关系版中全部`matched`关系涉及的原行，逐字段处理名称、运营、品类、等级和来源状态。只按已冻结的trim及精确匹配规则比较；只有一个不同非空值才取共识，全部空为`missing`，多个不同非空值为`conflict`；来源投影未提供才是`not_provided`。不擅自把不同营销名称归一。保留全部冲突值、原行及诊断，不以原生主键大小、读取顺序或第一/最后一条选值。运营仍是资料标签，不推定人员身份、告警负责人或动作认领；运营品类不拿平台/聚水潭分类替代，等级不由成交推算。

实际上架日先检查全部`matched`关系的逐链接适用状态及其证据。`not_applicable`只有明确排除依据时才不进入日期候选；任何`unknown/conflict`都使链接日期留空，不能被另一条`confirmed`掩盖。其余适用关系必须全部`confirmed`、证据可核、固定原格均能解析且所得日期仅有一个不同值，才输出实际上架日；不同日期为`conflict`，没有适用日期为缺失/不适用并保留原因。逐链接`confirmed`不能由多行共识反向推定；非空非法原日期或未证适用性不得悄悄跳过。`rejected`关系不参与资料及日期共识，排除证据仍可查询。

款号只按本来源规则派生去重展示集合；命名空间未核不作跨源归属，不按款分摊经营金额。首次经营观察日、观察天数、新品判断及总体匹配状态从固定事实、资料和规则派生，不写回链接主表。首次观察日期仍只表示实际输入覆盖，不填补缺失上架日。

共识查询每链接最多输出一行，原行候选与款号集合只能附在该行或通过独立证据查询返回。事实先按其真实粒度聚合，再LEFT JOIN这一行资料；无资料、字段缺失或冲突均不删除事实，也不改用名称分组。名称首先采用资料唯一非空共识；缺失或冲突时按下列固定范围回退，并继续返回原`field_match_status`及资料候选，不能因显示了回退名就抹掉冲突：

- **日明细：**只取同K（平台、店铺、商品ID）、本响应实际固定事实输入、当业务日的非空来源原名。trim/精确比较后恰有一个不同值才用；同日多名保留全部候选，以商品ID定位，不取同日首行或末行。
- **期间卡片、Top及评分卡：**只在该卡片实际固定事实输入与业务窗口内，寻找最近一个有非空原名的业务日；该日只有一个不同非空值才用，标“最近观察名称”，同时返回观察日及来源/版本。该日多名即冲突，返回该日全部候选，以商品ID定位，不越过冲突日改找更旧单名。评分严格使用其`product_current`固定版和当期窗口，不读新的当前事实版或其他参照窗。
- 无合格非空原名时仍以商品ID定位并返回缺失原因；不跨店、不借当前另一来源或超窗旧名，不用同名证明身份。名称候选属于展示/诊断结果，不新增名称表、列或人员ID；来源事实的原名仍是唯一事实证据。

发布前在同一事务内核验版本角色、来源原行归属、严格解析、已核映射、全部逐关系日期依据及集合共识，覆盖“同原行两链接仅一个日期适用”“一条好日期与另一条未知/冲突”“同日多名”“最近名超窗”等反例；原格失败与未匹配原行也要可查询和重放。比较关联前后事实键数、行数及金额/数量，关系及共识都不得放大经营事实。上述跨表/集合条件不能放进普通CHECK，后续实施须在真实PostgreSQL验证失败回滚、修订幂等、输入封存与原子切换。字段设计和本轮静态核对不代表来源、真实约束、性能或业务验收已通过。
