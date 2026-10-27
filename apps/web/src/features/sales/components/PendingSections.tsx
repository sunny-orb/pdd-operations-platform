import { Button, Card, Checkbox, DatePicker, Input, Progress, Select, Table } from "antd";
import type { TableProps } from "antd";
import type { FiltersResponse } from "../api/contracts";

export const TARGET_SOURCE =
  "https://kocotree.feishu.cn/base/XGzTbd9xJaASV3soYnycMmf0nY3?table=tblwEjKpt1k51M3j&view=vewbtMXayB";
export const REFUND_SOURCE =
  "https://kocotree.feishu.cn/base/XGzTbd9xJaASV3soYnycMmf0nY3?table=tblISBHrFF4jFJqW&view=vewrM4E5Np";
const emptyState = "尚未接入 · 经营数据保持独立，未将缺失记录视为零";

function EmptyTable({
  headers,
  label,
  minWidth,
}: {
  headers: string[];
  label: string;
  minWidth: number;
}) {
  const columns: TableProps<Record<string, string>>["columns"] = headers.map((title, i) => ({
    title,
    key: String(i),
    dataIndex: String(i),
  }));
  return (
    <Table<Record<string, string>>
      columns={columns}
      dataSource={[]}
      pagination={false}
      size="small"
      scroll={{ x: minWidth }}
      locale={{ emptyText: emptyState }}
      components={{
        table: (props: React.HTMLAttributes<HTMLTableElement>) => (
          <table {...props} aria-label={label} />
        ),
      }}
    />
  );
}

export function PendingTargets({ month }: { month: string }) {
  return (
    <section id="targets" className="target-grid" aria-label="目标完成进度">
      <Card className="target-card monthly-target">
        <div className="target-title">
          <span>本月 GMV 目标进度</span>
          <b>目标跟踪</b>
        </div>
        <div className="target-metric">
          <strong>—</strong>
          <span>月累计 GMV · {month || "—"}</span>
        </div>
        <Progress
          percent={0}
          showInfo={false}
          className="target-pending-progress"
          aria-label="月度目标尚未接入"
        />
        <div className="target-footer">
          <span>月度目标 —</span>
          <strong>—</strong>
        </div>
      </Card>
      <Card className="target-card annual-target">
        <div className="target-title">
          <span>年度 GMV 目标进度</span>
          <b>年度累计</b>
        </div>
        <div className="target-metric">
          <strong>—</strong>
          <span>2026.02–2027.01 累计 GMV</span>
        </div>
        <Progress
          percent={0}
          showInfo={false}
          strokeColor="#15805d"
          className="target-pending-progress"
          aria-label="年度目标尚未接入"
        />
        <div className="target-footer">
          <span>年度目标 —</span>
          <strong>—</strong>
        </div>
      </Card>
      <Card className="target-card target-summary">
        <span>本月目标缺口</span>
        <strong>—</strong>
        <p>目标台账尚未接入，缺口与达成率暂不可计算。</p>
        <a href="https://bi.kktree.cn/" target="_blank" rel="noreferrer">
          查看店铺销售原始数据 ↗
        </a>
      </Card>
    </section>
  );
}

export function PendingPromotion() {
  return (
    <div className="promotion-source">
      <details>
        <summary>查看推广口径与补齐记录</summary>
        <p>商品推广概览、明星店铺尚未接入。来源最新日、更新时间：—；缺渠道：待接入核验。</p>
        <p>
          历史补齐与确认零记录：未接入；逐条店铺、日期、来源、花费及生效状态：—。不自动混用另一份来源。
        </p>
      </details>
    </div>
  );
}

export function PendingRatings({
  filters,
  shop,
}: {
  filters: FiltersResponse | null;
  shop: string;
}) {
  const options = filters?.data.shops ?? [];
  const shown = shop ? options.filter((item) => item.shop_id === shop) : options;
  const names = shown.length
    ? shown.map((s) => s.display_name)
    : ["官方店", "户外店", "服饰店", "雨具店", "箱包店", "童装童鞋店"];
  return (
    <>
      <div className="rating-heading">
        <h3>店铺评分</h3>
        <p>最新评分日期：— · 尚未接入 · 不随经营日期筛选变化</p>
      </div>
      <div className="store-ratings">
        {names.map((name) => (
          <Card className="store-rating-card" key={name}>
            <span>{name}</span>
            <strong>—</strong>
            <small>评分日期：— · 未接入</small>
          </Card>
        ))}
      </div>
      <p className="rating-source">
        来源：ods_rpa_pdd_evaluation_overview_f · 字段 shop_rating_score · 来源创建时间：—。0–1
        原值乘 100 展示为百分制；历史大于 1 的值保留原值并标注旧口径，不混算；空值显示“—”。
      </p>
      <div className="rating-trend-panel">
        <div className="rating-heading">
          <h3>评分趋势</h3>
          <p>默认最近 30 天 · 日期筛选独立于经营数据 · 尚未接入</p>
        </div>
        <div className="rating-trend-controls">
          <fieldset disabled>
            <legend>选择店铺</legend>
            {["官方店", "户外店", "服饰店", "雨具店", "箱包店", "童装童鞋店"].map((name, i) => (
              <Checkbox disabled defaultChecked={i < 3} key={name}>
                {name}
              </Checkbox>
            ))}
          </fieldset>
          <div className="region-date-filter">
            <span>趋势日期</span>
            <DatePicker
              variant="borderless"
              size="small"
              disabled
              placeholder="尚未接入"
              aria-label="评分趋势开始日期"
            />
            <span>至</span>
            <DatePicker
              variant="borderless"
              size="small"
              disabled
              placeholder="尚未接入"
              aria-label="评分趋势结束日期"
            />
          </div>
        </div>
        <p className="rating-empty" role="status">
          店铺评分尚未接入，暂无可展示的趋势。
        </p>
        <p className="rating-source">
          百分制日评分，不做平均；纵轴按所选数据范围缩放。空值、缺失日期及旧口径数据断线展示，不补零。鼠标移至对应日期即可悬浮查看当天已勾选店铺的评分；详细数值也可在下方展开查看。
        </p>
        <details className="rating-values">
          <summary>查看评分趋势明细</summary>
          <div className="rating-values-scroll">
            <EmptyTable
              headers={["日期", "官方店", "户外店", "服饰店"]}
              label="评分趋势明细"
              minWidth={620}
            />
          </div>
        </details>
      </div>
    </>
  );
}

export function PendingRegions() {
  return (
    <>
      <section className="dashboard-section region-section" id="regions">
        <div className="section-heading">
          <div>
            <p className="eyebrow">REGION TRANSACTION</p>
            <div className="performance-title-row">
              <h2>地区交易分布（来源店铺后台）</h2>
              <div className="shop-filter">
                <span>店铺筛选</span>
                <Select
                  variant="borderless"
                  size="small"
                  disabled
                  value="全部店铺"
                  aria-label="地区交易店铺筛选"
                  options={[{ value: "全部店铺" }]}
                />
              </div>
              <div className="region-date-filter">
                <span>时间筛选</span>
                <DatePicker
                  variant="borderless"
                  size="small"
                  disabled
                  placeholder="尚未接入"
                  aria-label="地区交易开始日期"
                />
                <span>至</span>
                <DatePicker
                  variant="borderless"
                  size="small"
                  disabled
                  placeholder="尚未接入"
                  aria-label="地区交易结束日期"
                />
              </div>
            </div>
          </div>
          <p>尚未接入 · 可选日期范围待来源交付</p>
        </div>
        <div className="region-card">
          <div className="region-overview">
            <div>
              <span>地区成交金额</span>
              <strong>—</strong>
              <small>全部店铺汇总 · 尚未接入</small>
            </div>
            <dl>
              {["覆盖地区", "成交订单", "成交买家"].map((title) => (
                <div key={title}>
                  <dt>{title}</dt>
                  <dd>—</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="region-table">
            <EmptyTable
              headers={["地区", "成交金额", "销售额占比", "成交订单数", "成交买家数", "成交客单价"]}
              label="店铺后台地区数据"
              minWidth={760}
            />
          </div>
        </div>
        <p className="sales-footer">
          来源：bwd_rpa_pdd_region_sales_summary_pddht_f ·
          来源更新时间：—。按业务日期汇总省份成交金额、订单及买家，不与聚水潭实发金额相加。
        </p>
      </section>
      <section
        className="dashboard-section region-section product-region-section"
        id="product-regions"
      >
        <div className="section-heading">
          <div>
            <p className="eyebrow">PRODUCT REGION TRANSACTION</p>
            <h2>产品地区交易分布（来源聚水潭）</h2>
          </div>
          <p>尚未接入 · 日期及实退归属待核验</p>
        </div>
        <div className="product-region-filters">
          <div className="shop-filter">
            <span>店铺</span>
            <Select
              variant="borderless"
              size="small"
              disabled
              value="全部店铺"
              aria-label="产品地区店铺筛选"
            />
          </div>
          <div className="shop-filter">
            <span>品类</span>
            <Select
              variant="borderless"
              size="small"
              disabled
              value="全部品类"
              aria-label="产品地区品类筛选"
            />
          </div>
          <div className="product-search">
            <span>查找产品</span>
            <Input disabled placeholder="产品名称 / 款式编码" aria-label="查找产品名称或编码" />
          </div>
          <div className="shop-filter product-select">
            <span>产品</span>
            <Select
              variant="borderless"
              size="small"
              disabled
              value="全部产品"
              aria-label="产品地区产品筛选"
            />
          </div>
          <div className="region-date-filter">
            <span>业务日期</span>
            <DatePicker
              variant="borderless"
              size="small"
              disabled
              placeholder="尚未接入"
              aria-label="产品地区开始日期"
            />
            <span>至</span>
            <DatePicker
              variant="borderless"
              size="small"
              disabled
              placeholder="尚未接入"
              aria-label="产品地区结束日期"
            />
          </div>
          <Button disabled className="reset-filter">
            重置筛选
          </Button>
        </div>
        <div className="region-card">
          <div className="region-overview">
            <div>
              <span>实发金额（已知值合计）</span>
              <strong>—</strong>
              <small>尚未接入 · 全部店铺 · 全部品类</small>
            </div>
            <dl>
              {["覆盖地区", "实发件数", "实退金额", "实退件数"].map((title) => (
                <div key={title}>
                  <dt>{title}</dt>
                  <dd>—</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className="region-table">
            <EmptyTable
              headers={[
                "地区",
                "实发金额",
                "实发金额占比",
                "实发件数",
                "实退金额",
                "实退件数",
                "退款率",
              ]}
              label="聚水潭产品地区数据"
              minWidth={760}
            />
          </div>
          <p className="product-data-warning">
            字段缺失统计：实发金额 —、实发件数 —、实退金额 —、实退件数 —；未接入不等于零。
          </p>
        </div>
        <details className="product-region-notes">
          <summary>查看聚水潭口径</summary>
          <p>
            按数仓业务日期（dt）、店铺、省份汇总日粒度记录；品类取商品分类，产品按款式编码合并颜色及规格，款式未设置时按商品编码区分。金额占比以当前筛选范围已知实发总额为分母。
          </p>
          <p>
            退款率＝实退金额合计÷实发金额合计，不平均源比率；实发金额≤0或实退金额无已知值显示“—”，部分金额缺失标“*”。没有订单及买家字段；业务日期及实退归属尚待核验，不推断同批最终退款，不与后台成交GMV相加。排除包材、儿童赠品、分类未设置；未接入不生成选项。
          </p>
          <small>数据表：bwd_rpa_pdd_region_sales_summary_jst_f · 来源更新时间：— · 尚未接入</small>
        </details>
      </section>
    </>
  );
}
