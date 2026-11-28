# 看板 AI 问答 API v1

供看板前端及准备接入的业务系统使用。正式入口为 https://pdd-operations.kktree.cn/api/v1/assistant，沿用已确认的网站匿名访问方式。供应商密钥仅在服务器保存。公司维护联系人待指定。

| 本机入口                                 | 用途                                                                                                                                                                                    |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `http://127.0.0.1:8000/api/v1/assistant` | API 基址。Vite 开发代理可从 5173 端口同路径访问                                                                                                                                         |
| `/docs`、`/redoc`、`/openapi.json`       | API 服务的交互文档和请求模型。流式事件与报告含义见下文                                                                                                                                  |
| 访问条件                                 | `APP_ENV=development`，客户端与 Host 限回环地址。有 Origin 时须匹配获准本机来源。生产须显式开启 ASSISTANT_PRODUCTION_ENABLED，并校验正式站点 Host 和浏览器 Origin；供应商凭据由后端保管 |
| 服务预算                                 | 正式配置每个 API 进程最多同时生成两条回答，等待槽位 1 秒，每分钟最多接受 20 次提问。生成限时 90 秒，最多 6 次模型请求、6 次工具调用                                                     |

## 快速接入

以下路径都相对于 API 基址。

| 方法          | 请求                               | 成功响应                                                                  |
| ------------- | ---------------------------------- | ------------------------------------------------------------------------- |
| `GET /models` | 无正文                             | `models` 为获准型号数组，`business_data_enabled` 为业务数据开关           |
| `POST /chat`  | ChatInput，支持 JSON 与 SSE        | JSON 返回 ChatAnswer。请求头带 `Accept: text/event-stream` 时返回事件流   |
| `POST /stop`  | `{"generation":"本次生成的 UUID"}` | `{"stopped":true}` 表示服务端已处理停止请求，即使任务已结束也可收到此响应 |

启用后，`GET /models` 的型号数组只含 `deepseek:deepseek-flash`。调用方可用它检查可用状态，提问时省略 `model`。使用 SSE 时把 Accept 改为 `text/event-stream`，并给 curl 加 `-N`。[配置及启动](../development.md#ai-runtime)

```bash
curl --noproxy '*' -sS http://127.0.0.1:8000/api/v1/assistant/chat \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  --data '{"generation":"a55e8827-ec88-4b32-8ff0-64a2115b534d","page":"商品数据","question":"商品支付转化率怎么算？","context":{"domain":"products"},"history":[]}'
```

## 请求与追问

| 字段                | 调用约定                                                                                                                                       |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `generation`        | 必填。客户端为每次生成创建 UUID，用于停止和排除迟到事件。每次重试都可能重新计费                                                                |
| `page`              | 必填。`运营看板中心`、`商品数据`、`推广分析`、`店铺销售`、`售后分析` 之一                                                                      |
| `question`、`model` | 问题必填，去首尾空白后 1–2000 字符。服务端默认使用 DeepSeek。若显式传入其他型号，返回 HTTP 422                                                 |
| `context`           | 默认 `{}`。传已选业务、对象、筛选、期间及服务端返回的版本。ChatScope 拒绝未知字段                                                              |
| `history`           | 客户端管理，最多六项 `{page, question, answer, status?}`。问题同上，回答 1–8000 字符。status 默认 answered，也接受 need_selection、no_evidence |

| 常用范围   | 字段                                                                                                                                                                                                                    |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 业务和身份 | `domain` 支持 all/products/promotion_store/promotion_product/sales/aftersales/weather/data_status/definitions。店铺／链接 ID 为字符串，使用 shop_id、filter_shop_id、listing_id。用户选定候选后可传 candidate_confirmed |
| 日期和筛选 | date_from、date_to 成对传 ISO 日期，通用查询最多 366 天，单款两期每期最多 31 天。listing_ids、shop_ids、category_values、operator_values、level_values 各最多 20 项，单值筛选及长度见 ChatScope                         |
| 专用参数   | 推广 mode 为 store/product，目标 month 为 YYYY-MM，售后用 report_id、view、focus，其他商品筛选包括 product_key、keyword                                                                                                 |
| 版本和主题 | 保留返回的 versions（最多 20 键）和 explanation（view、可选 report_id）。兼容 product_version/promotion_version。收到 explanation:null 要清旧主题                                                                       |

**同一范围的追问沿用服务端返回的 `query_context`。** `risk_reference` 记录上轮明确点名的风险商品，含 listing_id、shop_id、data_version、date_from、date_to。原样回传供单款追问核验，收到 null 时清除。它独立于页面筛选。 页面跟随开启时，切页或修改筛选后的新问题使用新范围；明确引用旧问题时使用对应轮次的范围和相关历史。关闭跟随后，从全局入口提问，省略页面商品、店铺和日期。每次提交固定当时的筛选。可保留尚待澄清的问题，停止、失败和断流的半段正文排除在完成历史之外。

## 返回结果

| 字段                                       | 前端怎么用                                                                                                                     |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `answer`、`model`                          | answer 按安全 Markdown 渲染。model 为服务配置的型号，usage.requests 记录实际模型请求数                                         |
| `status`                                   | answered 展示回答，need_selection 展示候选，no_evidence 展示缺口或澄清提示                                                     |
| `subject`、`candidates`、`subject_cleared` | 已选商品或候选含 listing_id、shop_id、name、shop_name 和可选 product_id。subject 可为 null，subject_cleared 标记显式清除旧对象 |
| `evidence`、`reports`                      | 来源说明和结构化依据，由服务端组装。reports 可为空。正文与依据分开展示                                                         |
| `query_context`、`usage`                   | 前者供追问续用。后者可含本轮 input_tokens、output_tokens、requests、tool_calls，分别统计 token、模型请求和工具调用             |

**`answered` 配空 `reports` 是有效回答。** 能力介绍、用法、写作、按用户所给数字试算都可以只返回正文。客户端应直接展示，保留已选对象。询问型号直接读取服务配置，`usage.requests` 为 0。重新核实实绩或公司规定时展示对应依据。收到 `subject_cleared:true` 或新的待选列表时清除旧选择。

`kind=dashboard` 的报告用 domain、capability、title、source 标明来源，period、scope、versions、data_version 描述实际范围。facts 返回带单位、状态及原因的指标，tables 返回表头、行、总数与偏移，series 返回带日期的点，notes 说明限制。金额和比率使用十进制字符串，例如 percent 的 `"0.03"` 表示 3%。保留 null 和真实零的区别。

公共 SOP 沿用同一个问答接口。列目录只返回标题和适用范围。运营建议在选中并核对文章后返回 `business_knowledge`，其 `scope.document_key` 标识文档，`scope.document_url` 可打开原文，`data_version` 为实际修订。没有适用条款时不生成文档依据。

正式六店月末 GMV 返回 `sales_forecast`。已核修订可直接复算；新版首次提问的解读调用计入 `usage.requests` 和 token，总数已包含 `sop_review_requests`。供应商用量未取得时省略无法确认的总数字段，并返回 `sop_review_usage_complete=false`。

预测正文含数据截止日、结果和“查看预测 SOP”链接。客户端从 `reports[].versions.forecast_sop` 取飞书修订号用于展开依据，`forecast_inputs` 供核对经营输入。`notes` 提供本次使用的公式或所引章节。回算可写“截至 11 月 25 日”，原样回传 `query_context.forecast` 即可继续追问同一期间。复合问题另附本轮查证的其他指标。

「假设近 3 日占 60%、近 7 日占 40%，预测本月 GMV」在同一个接口返回 `scenario_center`（本题试算）和 `official_center`（正式预测），均为估计金额。新修订存在矛盾时返回 `status=no_evidence`、本版原句和出处，金额字段留空。经营建议使用已有公共查询，可同时引用业务数据和 SOP 章节。

单款报告 `product_performance`、`product_promotion` 另含商品、前后期日期、两期指标与变化、各自覆盖、逐日值及规则依据。客户端按 kind 解析，完整结构见[报告契约](../../apps/web/src/shared/assistant/contracts.ts)。

<details>
<summary>展开完整 JSON 示例 · 商品经营支付转化率</summary>

报告由本地口径工具生成，版本标识和用量为示例值。

```json
{
  "answer": "商品经营支付转化率是支付买家数除以商品访客数。",
  "status": "answered",
  "subject": null,
  "subject_cleared": false,
  "candidates": [],
  "reports": [
    {
      "kind": "dashboard",
      "domain": "definitions",
      "capability": "metric_definitions",
      "title": "指标口径",
      "period": null,
      "scope": {
        "keyword": "商品经营支付转化率",
        "limit": 10,
        "offset": 0
      },
      "facts": [],
      "tables": [
        {
          "title": "适用口径",
          "columns": [
            {
              "key": "domain",
              "label": "业务",
              "unit": "text"
            },
            {
              "key": "metric",
              "label": "指标",
              "unit": "text"
            },
            {
              "key": "numerator",
              "label": "分子",
              "unit": "text"
            },
            {
              "key": "denominator",
              "label": "分母",
              "unit": "text"
            },
            {
              "key": "note",
              "label": "说明",
              "unit": "text"
            },
            {
              "key": "source",
              "label": "实现出处",
              "unit": "text"
            }
          ],
          "rows": [
            {
              "domain": "商品",
              "metric": "商品经营支付转化率",
              "numerator": "支付买家数",
              "denominator": "商品访客数",
              "note": "新品评分与经营转化率的分子不同。",
              "source": "app/modules/product_board/metrics.py"
            }
          ],
          "total": 1,
          "offset": 0
        }
      ],
      "notes": ["本工具说明公式与边界；实际经营数字、售后报告和SOP命中仍从对应查询取得。"],
      "series": [],
      "source": "现有指标实现与数据契约",
      "data_version": "example-v1",
      "versions": {
        "definitions": "example-v1"
      }
    }
  ],
  "evidence": "现有指标实现与数据契约，版本 example-v1",
  "query_context": {
    "domain": "products",
    "shop_ids": [],
    "category_values": [],
    "operator_values": [],
    "level_values": [],
    "versions": {
      "definitions": "example-v1"
    }
  },
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 60,
    "requests": 2,
    "tool_calls": 1
  },
  "model": "deepseek:deepseek-flash"
}
```

</details>

## 流式显示和停止

浏览器用 `fetch` POST 同一个请求体，按 SSE 消息边界读取 `data: <JSON>\n\n`。每条事件的类别在 JSON 的 `type` 字段。

商品文案和历史改写在核对数字、规格后发送正文，可能只收到一条 `delta`。客户端以 `done` 为完成标志。

| type   | 行为                                                                         |
| ------ | ---------------------------------------------------------------------------- |
| status | 展示 text 中的当前阶段                                                       |
| delta  | 将 text 追加到临时正文                                                       |
| done   | 用 result 中完整的 ChatAnswer 替换临时正文，按 status 展示依据或候选         |
| error  | 展示 error.message，保留请求编号。只收到部分正文或连接提前关闭，按未完成处理 |

以下示例展示一段回答流中断时的实际帧格式。成功结束时收到 done，result 的结构与上面的 JSON 一致。

```text
data: {"type":"status","text":"正在理解问题…"}

data: {"type":"delta","text":"这里的商品经营支付转化率"}

data: {"type":"error","error":{"message":"回答中断，请重试"}}

```

流开始后的失败可能仍是 HTTP 200。先检查 HTTP 状态，再检查 done/error 终态。用户点停止时，将原 generation 发给 `/stop`，终止读取并忽略迟到帧。现版无断点续传或生成去重，重试应由用户发起并使用新 UUID。

## 错误与部分结果

| HTTP 状态       | 调用方处理                                                              |
| --------------- | ----------------------------------------------------------------------- |
| 403             | 核对本机访问条件、Origin 和供应商授权                                   |
| 409             | DATA_VERSION_CHANGED，刷新相关版本，重新选择范围                        |
| 422             | 核对必填字段、期间、型号和错误信息。使用上限另有下述正常响应路径        |
| 429             | 当前进程忙，等待生成任务释放。服务端无统一每分钟配额或 Retry-After 保证 |
| 499             | 用户已取消                                                              |
| 502 / 503 / 504 | 分别检查模型调用失败、未配置或服务不可用、生成超时                      |
| 500             | 保存响应头 X-Request-ID，供服务维护者排查                               |

错误示例为 `{"error":{"code":"DATA_VERSION_CHANGED","message":"页面与查询的数据版本不同，请刷新页面后重新选择商品"}}`。code、error.request_id 按错误路径返回，以 message 供用户阅读。

**预算耗尽通常也会返回 HTTP 200 或 SSE done。** 当前有报告时 status 可为 answered，并说明只取得部分结果。无报告时为 no_evidence。路由另有 422 / ASSISTANT_LIMIT_REACHED 兜底。当前客户端应保留原回答提示，不能根据 answered 推定所有查询已完成。

## 公司平台接入（待开放）

接入方提供受控只读查询、指标口径、数据维护人和用户可见范围。共享上线前确定公司网关身份（拟用 Bearer）、平台与用户权限、并发额度、模型外发许可、测试地址及联系渠道。供应商密钥只留在服务端。

通用页面标识、会话关联 ID、依据引用和明确的部分完成字段属于后续协议。可选字段按向后兼容原则发布，破坏性调整另定版本。当前联调使用上表 v1 字段。[开发设计](../implementation/ai-assistance.md)
