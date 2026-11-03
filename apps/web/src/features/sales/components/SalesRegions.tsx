import { ProductRegionNotes } from "./ProductRegionNotes";
import { formatFixedDecimal } from "../../../shared/format/decimal";
import { SalesRegionNotes } from "./SalesRegionNotes";
import { memo, useState } from "react";
import { Button, DatePicker, Input, Select, Table } from "antd";
import dayjs from "dayjs";
import type { FiltersResponse } from "../api/contracts";
import { detailPath, productFiltersSchema, regionSchema, useSalesDetail } from "../api/details";
import { count, fixed, money, percent } from "../format";
import "./sales-regions.css";
import "./product-regions.css";

/** 产品地区沿线上展示元与两位小数；仅格式化API十进制串，不重算金额。 */
function productValue(value: string | null | undefined, currency = false) {
  if (value == null) return "—";
  const formatted = formatFixedDecimal(value, 2, 0, true)!;
  return currency ? `¥${formatted}` : formatted.replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
}
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
            format="YYYY/MM/DD"
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
export const SalesRegions = memo(function SalesRegions({ filters, onConflict }: Props) {
  return (
    <>
      <RegionPanel filters={filters} onConflict={onConflict} />
      <RegionPanel filters={filters} onConflict={onConflict} product />
    </>
  );
});
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
    product ? productValue(value, field.includes("amount")) : count(value);
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
                {shop && (
                  <Button className="reset-filter" onClick={() => setShop("")}>
                    显示全部店铺
                  </Button>
                )}
                {(range.from !== latest || range.to !== latest) && (
                  <Button className="reset-filter" onClick={() => setDates(null)}>
                    恢复最新日
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <p>
          {product ? (
            <>
              可筛选 {source?.observed_from ?? "—"} 至 {latest || "—"} · 默认展示最新数据日
            </>
          ) : (
            <>
              可筛选 {source?.observed_from ?? "—"} 至 {latest || "—"}{" "}
              的地区交易数据，默认展示最新数据日（不含当天）。
            </>
          )}
        </p>
      </div>
      {product && (
        <div className="product-region-filters" aria-label="产品地区筛选">
          {shopFilter}
          <div className="shop-filter product-category">
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
              classNames={{ popup: { root: "m3-product-region-options" } }}
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
          <div className="product-search">
            <span>查找产品</span>
            <Input
              disabled={!version}
              variant="borderless"
              placeholder="名称 / 编码"
              aria-label="查找产品名称或编码"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setProductKey("");
                setOffset(0);
              }}
            />
          </div>
          <div className="product-date-actions">
            {dateFilter}
            <Button
              disabled={!version}
              className="reset-filter"
              aria-label="重置筛选"
              title="重置本区全部筛选"
              onClick={() => {
                setShop("");
                setCategory("");
                setSearch("");
                setProductKey("");
                setOffset(0);
                setDates(null);
              }}
            >
              重置
            </Button>
          </div>
        </div>
      )}
      {product && search && (
        <p className="product-filter-hint" role="status">
          {options.error
            ? "产品选项读取失败，请重试。"
            : options.data
              ? `找到 ${options.data.data.total} 个产品选项，请在“产品”中选择。`
              : "正在查找产品…"}
          {!productKey && `当前仍汇总${category ? "该品类" : ""}全部产品。`}
        </p>
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
            <strong>{product ? productValue(total?.[main], true) : money(total?.[main])}</strong>
            <small>
              {product ? (
                <>
                  {range.from || "—"}
                  {range.to !== range.from && ` 至 ${range.to || "—"}`} ·{" "}
                  {shops.find((x) => x.value === shop)?.label} · {category || "全部品类"}{" "}
                  {productKey ? "· 已选产品" : ""}
                </>
              ) : (
                <>
                  {shop
                    ? `${shops.find((x) => x.value === shop)?.label} · 地区汇总`
                    : "全部店铺汇总"}{" "}
                  · {range.from || "—"}
                  {range.to !== range.from && ` 至 ${range.to || "—"}`}
                </>
              )}
            </small>
          </div>
          <dl>
            <div>
              <dt>覆盖地区</dt>
              <dd>
                {d?.region_count ?? "—"}
                {d && " 个"}
              </dd>
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
            scroll={product ? { x: 880, y: 452 } : { x: 760, y: 412 }}
            components={{
              table: (p: React.HTMLAttributes<HTMLTableElement>) => (
                <table {...p} aria-label={product ? "聚水潭产品地区数据" : "店铺后台地区数据"} />
              ),
            }}
            locale={{
              emptyText: product
                ? error
                  ? "产品地区数据读取失败"
                  : !version
                    ? "产品地区数据暂不可用"
                    : !d
                      ? "正在读取产品地区数据…"
                      : "所选店铺、品类、产品及时间段暂无记录，请调整筛选条件。"
                : error
                  ? "地区交易数据读取失败"
                  : !version
                    ? "地区交易数据暂不可用"
                    : !d
                      ? "正在读取地区交易数据…"
                      : d.source.state !== "observed"
                        ? "地区交易数据暂不可用"
                        : "暂无地区交易记录",
            }}
            columns={
              product
                ? [
                    { title: "地区", dataIndex: "region", width: 100, render: (v) => <b>{v}</b> },
                    {
                      title: "实发金额",
                      dataIndex: "actual_shipped_amount",
                      width: 160,
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_shipped_amount ?? 0, productValue(v, true)),
                    },
                    {
                      title: "实发金额占比",
                      dataIndex: "share",
                      width: 140,
                      render: (v, r) =>
                        v != null &&
                        ((r.null_counts.actual_shipped_amount ?? 0) > 0 ||
                          (d?.null_counts.actual_shipped_amount ?? 0) > 0) ? (
                          <span
                            title={`占比按已知实发金额计算；分母缺失${d?.null_counts.actual_shipped_amount ?? 0}条，本地区缺失${r.null_counts.actual_shipped_amount ?? 0}条`}
                          >
                            {percent(v)}*
                          </span>
                        ) : (
                          percent(v)
                        ),
                    },
                    {
                      title: "实发件数",
                      dataIndex: "actual_shipped_qty",
                      width: 110,
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_shipped_qty ?? 0, productValue(v)),
                    },
                    {
                      title: "实退金额",
                      dataIndex: "actual_returned_amount",
                      width: 150,
                      render: (v, r) =>
                        partial(
                          v,
                          r.null_counts.actual_returned_amount ?? 0,
                          productValue(v, true),
                        ),
                    },
                    {
                      title: "实退件数",
                      dataIndex: "actual_returned_qty",
                      width: 110,
                      render: (v, r) =>
                        partial(v, r.null_counts.actual_returned_qty ?? 0, productValue(v)),
                    },
                    {
                      title: "退款率",
                      dataIndex: "refund_rate",
                      width: 110,
                      render: (v, r) => (
                        <span title="所选期间实退金额÷实发金额；实退与实发可能来自不同批次的订单">
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
                    {
                      title: "地区",
                      dataIndex: "region",
                      width: "11.5%",
                      render: (v) => <b>{v}</b>,
                    },
                    {
                      title: "成交金额",
                      dataIndex: "transaction_amount",
                      width: "29.5%",
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
                    {
                      title: "销售额占比",
                      dataIndex: "share",
                      width: "14.7%",
                      render: (v) => percent(v, 1),
                    },
                    {
                      title: "成交订单数",
                      dataIndex: "transaction_orders",
                      width: "14.7%",
                      render: count,
                    },
                    {
                      title: "成交买家数",
                      dataIndex: "transaction_buyers",
                      width: "14.7%",
                      render: count,
                    },
                    {
                      title: "成交客单价",
                      dataIndex: "aov",
                      render: (v) => (v == null ? "—" : `¥${fixed(v)}`),
                    },
                  ]
            }
          />
        </div>
        {product && d && fields.some((f) => d.null_counts[f] > 0) && (
          <p className="product-data-warning">
            部分记录未提供：
            {fields
              .map((f, i) =>
                d.null_counts[f] > 0
                  ? `${["实发金额", "实发件数", "实退金额", "实退件数"][i]} ${count(String(d.null_counts[f]))}条`
                  : null,
              )
              .filter(Boolean)
              .join("、")}
            。“—”表示无已知值，“*”表示部分缺失；仅累计已有数值。
          </p>
        )}
      </div>
      {product ? (
        <ProductRegionNotes data={d} shops={filters?.data.shops ?? []} source={source} />
      ) : (
        <SalesRegionNotes data={d} shops={filters?.data.shops ?? []} source={source ?? d?.source} />
      )}
    </section>
  );
}
