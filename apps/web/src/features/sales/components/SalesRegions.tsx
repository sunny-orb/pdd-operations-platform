import { SourceState } from "./SourceState";
import { useState } from "react";
import { Button, DatePicker, Input, Select, Table } from "antd";
import dayjs from "dayjs";
import type { FiltersResponse } from "../api/contracts";
import { detailPath, productFiltersSchema, regionSchema, useSalesDetail } from "../api/details";
import { count, fixed, money, percent } from "../format";
type Props = { filters: FiltersResponse | null; onConflict: () => void };
type Dates = { from: string; to: string };
function DatesFilter({
  range,
  source,
  onChange,
  label,
  disabled,
}: {
  range: Dates;
  source: { observed_from: string | null; observed_through: string | null } | undefined;
  onChange: (d: Dates) => void;
  label: string;
  disabled: boolean;
}) {
  return (
    <div className="region-date-filter">
      <span>{label === "产品地区" ? "业务日期" : "时间筛选"}</span>
      {(["from", "to"] as const).map((key, i) => (
        <span key={key}>
          {i > 0 && "至"}
          <DatePicker
            size="small"
            variant="borderless"
            allowClear={false}
            disabled={disabled}
            aria-label={`${label}${i ? "结束" : "开始"}日期`}
            value={range[key] ? dayjs(range[key]) : null}
            minDate={source?.observed_from ? dayjs(source.observed_from) : undefined}
            maxDate={source?.observed_through ? dayjs(source.observed_through) : undefined}
            onChange={(d) => {
              if (d) {
                const v = d.format("YYYY-MM-DD");
                onChange(
                  key === "from"
                    ? { from: v, to: v > range.to ? v : range.to }
                    : { from: v < range.from ? v : range.from, to: v },
                );
              }
            }}
          />
        </span>
      ))}
    </div>
  );
}
export function SalesRegions({ filters, onConflict }: Props) {
  return (
    <>
      <RegionPanel filters={filters} onConflict={onConflict} />
      <RegionPanel filters={filters} onConflict={onConflict} product />
    </>
  );
}
function RegionPanel({ filters, onConflict, product = false }: Props & { product?: boolean }) {
  const code = product ? "product_regions" : "shop_regions";
  const source = filters?.data.sources?.[code];
  const version = filters?.data.capabilities[product ? "product_regions" : "regions"]
    ? filters.meta.data_version
    : undefined;
  const latest = source?.observed_through ?? "";
  const [dates, setDates] = useState<Dates | null>(null);
  const range = dates ?? { from: latest, to: latest };
  const [shop, setShop] = useState("");
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [productKey, setProductKey] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, retry } = useSalesDetail(
    detailPath(product ? "product-regions" : "regions", version, {
      date_from: range.from,
      date_to: range.to,
      ...(shop ? { shop_id: shop } : {}),
      ...(product ? { category, product_key: productKey } : {}),
    }),
    regionSchema,
    onConflict,
  );
  const options = useSalesDetail(
    product
      ? detailPath("product-regions/filters", version, {
          category,
          search,
          offset: String(offset),
          limit: "100",
        })
      : null,
    productFiltersSchema,
    onConflict,
  );
  const d = data?.data;
  const total = d?.totals;
  const fields = product
    ? [
        "actual_shipped_amount",
        "actual_shipped_qty",
        "actual_returned_amount",
        "actual_returned_qty",
      ]
    : ["transaction_amount", "transaction_orders", "transaction_buyers"];
  const main = product ? "actual_shipped_amount" : "transaction_amount";
  const shops = [
    { value: "", label: "全部店铺" },
    ...(filters?.data.shops.map((s) => ({ value: s.shop_id, label: s.display_name })) ?? []),
  ];
  const shopFilter = (
    <div className="shop-filter">
      <span>{product ? "店铺" : "店铺筛选"}</span>
      <Select
        size="small"
        variant="borderless"
        disabled={!version}
        value={shop}
        options={shops}
        aria-label={product ? "产品地区店铺筛选" : "地区交易店铺筛选"}
        onChange={setShop}
      />
    </div>
  );
  const dateFilter = (
    <DatesFilter
      range={range}
      source={source}
      disabled={!version}
      onChange={setDates}
      label={product ? "产品地区" : "地区交易"}
    />
  );
  const partial = (value: string | null | undefined, missing: number, rendered: string) =>
    value != null && missing > 0 ? (
      <span title={`仅已知值合计，${missing}条来源记录缺此字段`}>{rendered}*</span>
    ) : (
      rendered
    );
  const formatted = (field: string, value: string | null | undefined) =>
    field.includes("amount") ? money(value, true) : count(value);
  return (
    <section
      className={`dashboard-section region-section ${product ? "product-region-section" : ""}`}
      id={product ? "product-regions" : "regions"}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">{product ? "PRODUCT REGION TRANSACTION" : "REGION TRANSACTION"}</p>
          <div className="performance-title-row">
            <h2>{product ? "产品地区交易分布（来源聚水潭）" : "地区交易分布（来源店铺后台）"}</h2>
            {!product && (
              <>
                {shopFilter}
                {dateFilter}
                {(shop || range.from !== latest || range.to !== latest) && (
                  <Button
                    className="reset-filter"
                    onClick={() => {
                      setShop("");
                      setDates(null);
                    }}
                  >
                    恢复最新日
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <p>
          {source?.observed_from ?? "—"} 至 {latest || "—"} · 默认最新已观察日 · 完整日未确认
        </p>
      </div>
      {product && (
        <div className="product-region-filters">
          {shopFilter}
          <div className="shop-filter">
            <span>品类</span>
            <Select
              variant="borderless"
              size="small"
              disabled={!version}
              aria-label="产品地区品类筛选"
              value={category}
              options={[
                { value: "", label: "全部品类" },
                ...(options.data?.data.categories.map((x) => ({ value: x, label: x })) ?? []),
              ]}
              onChange={(v) => {
                setCategory(v);
                setSearch("");
                setProductKey("");
                setOffset(0);
              }}
            />
          </div>
          <div className="product-search">
            <span>查找产品</span>
            <Input
              disabled={!version}
              placeholder="产品名称 / 款式编码"
              aria-label="查找产品名称或编码"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setProductKey("");
                setOffset(0);
              }}
            />
          </div>
          <div className="shop-filter product-select">
            <span>产品</span>
            <Select
              variant="borderless"
              size="small"
              disabled={!version}
              aria-label="产品地区产品筛选"
              value={productKey}
              options={[
                { value: "", label: "全部产品" },
                ...(options.data?.data.products.map((x) => ({
                  value: x.key,
                  label: `${x.name} · ${x.code}`,
                })) ?? []),
              ]}
              onChange={setProductKey}
              popupMatchSelectWidth={360}
              popupRender={(menu) => (
                <>
                  {menu}
                  <div className="product-option-pagination">
                    {(options.data?.data.total ?? 0) > 100 && (
                      <>
                        <Button
                          size="small"
                          disabled={offset === 0}
                          onClick={() => setOffset((n) => Math.max(0, n - 100))}
                        >
                          上一批
                        </Button>
                        <span>
                          {offset + 1}–{Math.min(offset + 100, options.data!.data.total)} /{" "}
                          {options.data!.data.total}
                        </span>
                        <Button
                          size="small"
                          disabled={offset + 100 >= (options.data?.data.total ?? 0)}
                          onClick={() => setOffset((n) => n + 100)}
                        >
                          下一批
                        </Button>
                      </>
                    )}
                  </div>
                </>
              )}
            />
          </div>
          {dateFilter}
          <Button
            disabled={!version}
            className="reset-filter"
            onClick={() => {
              setShop("");
              setCategory("");
              setSearch("");
              setProductKey("");
              setOffset(0);
              setDates(null);
            }}
          >
            重置筛选
          </Button>
        </div>
      )}
      {(error || options.error) && (
        <p role="status">
          {error ?? options.error}
          <Button
            size="small"
            onClick={() => {
              retry();
              options.retry();
            }}
          >
            重试地区数据
          </Button>
        </p>
      )}
      <div className="region-card">
        <div className="region-overview">
          <div>
            <span>{product ? "实发金额（已知值合计）" : "地区成交金额"}</span>
            <strong>{money(total?.[main])}</strong>
            <small>
              {range.from || "—"} 至 {range.to || "—"} ·{" "}
              {shops.find((x) => x.value === shop)?.label} · {product ? category || "全部品类" : ""}{" "}
              {productKey ? "· 已选产品" : ""}
            </small>
          </div>
          <dl>
            <div>
              <dt>覆盖地区</dt>
              <dd>{d?.region_count ?? "—"}</dd>
            </div>
            {fields.slice(1).map((f, i) => (
              <div key={f}>
                <dt>
                  {product ? ["实发件数", "实退金额", "实退件数"][i] : ["成交订单", "成交买家"][i]}
                </dt>
                <dd>{formatted(f, total?.[f])}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="region-table">
          <Table
            size="small"
            rowKey="region"
            dataSource={d?.items ?? []}
            pagination={false}
            scroll={{ x: 760 }}
            components={{
              table: (p: React.HTMLAttributes<HTMLTableElement>) => (
                <table {...p} aria-label={product ? "聚水潭产品地区数据" : "店铺后台地区数据"} />
              ),
            }}
            locale={{ emptyText: version ? "当前条件暂无记录，缺失不补零" : "尚未接入" }}
            columns={
              product
                ? [
                    { title: "地区", dataIndex: "region" },
                    {
                      title: "实发金额",
                      dataIndex: "actual_shipped_amount",
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_shipped_amount ?? 0, money(v, true)),
                    },
                    {
                      title: "实发金额占比",
                      dataIndex: "share",
                      render: (v, r) =>
                        v != null &&
                        ((r.null_counts.actual_shipped_amount ?? 0) > 0 ||
                          (d?.null_counts.actual_shipped_amount ?? 0) > 0) ? (
                          <span
                            title={`占比按已知实发金额计算；分母缺失${d?.null_counts.actual_shipped_amount ?? 0}条，本地区缺失${r.null_counts.actual_shipped_amount ?? 0}条`}
                          >
                            {percent(v, 1)}*
                          </span>
                        ) : (
                          percent(v, 1)
                        ),
                    },
                    {
                      title: "实发件数",
                      dataIndex: "actual_shipped_qty",
                      render: (v, r) => partial(v, r.null_counts.actual_shipped_qty ?? 0, count(v)),
                    },
                    {
                      title: "实退金额",
                      dataIndex: "actual_returned_amount",
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_returned_amount ?? 0, money(v, true)),
                    },
                    {
                      title: "实退件数",
                      dataIndex: "actual_returned_qty",
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_returned_qty ?? 0, count(v)),
                    },
                    {
                      title: "退款率",
                      dataIndex: "refund_rate",
                      render: (v, r) => (
                        <span title="来源期间参考占比，日期及实退归属待核">
                          {percent(v)}
                          {v != null &&
                          (r.null_counts.actual_shipped_amount > 0 ||
                            r.null_counts.actual_returned_amount > 0)
                            ? "*"
                            : ""}
                        </span>
                      ),
                    },
                  ]
                : [
                    { title: "地区", dataIndex: "region" },
                    {
                      title: "成交金额",
                      dataIndex: "transaction_amount",
                      render: (v, r) => (
                        <span className="region-sales">
                          <i>
                            <em
                              style={{
                                width: `${Math.min(100, Math.max(0, Number(r.share ?? 0) * 100))}%`,
                              }}
                            />
                          </i>
                          {money(v, true)}
                        </span>
                      ),
                    },
                    { title: "销售额占比", dataIndex: "share", render: (v) => percent(v, 1) },
                    { title: "成交订单数", dataIndex: "transaction_orders", render: count },
                    { title: "成交买家数", dataIndex: "transaction_buyers", render: count },
                    {
                      title: "成交客单价",
                      dataIndex: "aov",
                      render: (v) => (v == null ? "—" : `¥${fixed(v)}`),
                    },
                  ]
            }
          />
        </div>
        {product && (
          <p className="product-data-warning">
            字段缺失统计：
            {fields
              .map(
                (f, i) =>
                  `${["实发金额", "实发件数", "实退金额", "实退件数"][i]} ${d?.null_counts[f] ?? "—"}`,
              )
              .join("、")}
            ；仅合计已知值，不补零。
            <br />
            <SourceState source={d?.source ?? source} label="聚水潭地区" />
          </p>
        )}
      </div>
      {product ? (
        <details className="product-region-notes">
          <summary>查看聚水潭口径</summary>
          <p>
            按数仓业务日期（dt）、店铺、省份汇总日粒度记录；品类取商品分类，产品按款式编码合并颜色及规格，款式未设置时按商品编码区分。缺码记录保留在全部产品汇总，不合成一个可选产品。
          </p>
          <p>
            退款率＝实退金额合计÷实发金额合计，不平均源比率；实发金额≤0或无已知值显示“—”，部分金额缺失标“*”。业务日期及实退归属待核，不推断同批最终退款，不与后台GMV相加。排除包材、儿童赠品、分类未设置。产品搜索只影响候选，选择产品后才影响汇总。
          </p>
          <small>
            数据表：bwd_rpa_pdd_region_sales_summary_jst_f · 来源更新时间：
            {d?.source.source_updated_text ?? "—"} · 当前筛选无记录店铺日{" "}
            {d?.missing_shop_days.length ?? "—"}
          </small>
        </details>
      ) : (
        <p className="sales-footer">
          来源：bwd_rpa_pdd_region_sales_summary_pddht_f · 来源更新时间：
          {d?.source.source_updated_text ?? "—"}
          <br />
          <SourceState source={d?.source ?? source} label="后台地区" />
          。买家数按店铺、日期、地区累加，不跨日去重。客单价仅用成交金额/买家均有值的记录计算；当前筛选无记录店铺日{" "}
          {d?.missing_shop_days.length ?? "—"}，不补零。
        </p>
      )}
    </section>
  );
}
