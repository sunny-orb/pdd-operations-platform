import { memo, useMemo, useState } from "react";
import { Button, Card, Checkbox, DatePicker, Table } from "antd";
import dayjs from "dayjs";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FiltersResponse } from "../api/contracts";
import { detailPath, ratingsSchema, useSalesDetail } from "../api/details";
import { fixed, localTime } from "../format";
import { SalesExplanation } from "./SalesExplanation";
import "./sales-rating-trend.css";
const colors: Record<string, string> = {
  spigf: "#2070bf",
  spihw: "#15805d",
  spifs: "#d58e20",
  spiyj: "#9266bf",
  spixb: "#df6c6c",
  spitztx: "#3c9da3",
};
const ratingNotes: Record<string, string> = {
  field_null: "当日评分为空",
  no_records: "当日暂无评分记录",
  invalid_score: "当日评分异常",
};

/** 展示后端已判定的评分；真实零保留，异常与缺失都不伪装成分数。 */
function ratingValue(point?: { state: string; score: string | null; raw_score: string | null }) {
  if (point?.state === "legacy_basis" && point.raw_score !== null)
    return `${fixed(point.raw_score)} 分（旧口径）`;
  return point?.state === "observed" && point.score !== null ? `${fixed(point.score)} 分` : "—";
}

/** 趋势提示和明细共用已返回的状态；没有店铺日记录与有记录但评分为空分别说明。 */
function trendValue(point?: { state: string; score: string | null; raw_score: string | null }) {
  if (!point || point.state === "no_records") return "—（暂无记录）";
  if (point.state === "field_null") return "—（评分为空）";
  if (point.state === "invalid_score") return "—（评分异常）";
  if (point.state === "legacy_basis") return `${fixed(point.raw_score)}（旧口径）`;
  return fixed(point.score);
}
export const SalesRatings = memo(function SalesRatings({
  filters,
  shop,
  onConflict,
}: {
  filters: FiltersResponse | null;
  shop: string;
  onConflict: () => void;
}) {
  const shops = filters?.data.shops ?? [];
  const source = filters?.data.sources?.shop_ratings;
  const earliest = source?.observed_from ?? "";
  const latest = source?.observed_through ?? "";
  const [selected, setSelected] = useState(["spigf", "spihw", "spifs"]);
  const [range, setRange] = useState<{ from: string; to: string } | null>(null);
  // 初始窗口及来源版本变化后的已选窗口，都限制在实际评分历史内。
  const clampDate = (value: string) =>
    earliest && value < earliest ? earliest : value > latest ? latest : value;
  const from = latest
    ? clampDate(range?.from ?? dayjs(latest).subtract(29, "day").format("YYYY-MM-DD"))
    : "";
  const to = latest ? clampDate(range?.to ?? latest) : "";
  const version = filters?.data.capabilities.ratings ? filters.meta.data_version : undefined;
  const { data, error, retry } = useSalesDetail(
    detailPath("ratings", from && to ? version : undefined, { date_from: from, date_to: to }),
    ratingsSchema,
    onConflict,
  );
  const points = data?.data.points;
  const pointByDay = useMemo(
    () => new Map(points?.map((point) => [`${point.date}/${point.shop_id}`, point])),
    [points],
  );
  const selectedShops = shops.filter((s) => selected.includes(s.shop_id));
  const chart = useMemo(() => {
    const rows: Record<string, string | null>[] = [];
    if (!from || !to) return rows;
    for (let d = dayjs(from); d.isBefore(dayjs(to).add(1, "day")); d = d.add(1, "day")) {
      const date = d.format("YYYY-MM-DD");
      const row: Record<string, string | null> = { date };
      for (const id of selected) row[id] = pointByDay.get(`${date}/${id}`)?.score ?? null;
      rows.push(row);
    }
    return rows;
  }, [from, to, selected, pointByDay]);
  const values = chart.flatMap((r) =>
    selected.flatMap((id) => (r[id] == null ? [] : [Number(r[id])])),
  );
  const lo = values.length ? Math.max(0, Math.floor((Math.min(...values) - 2) / 5) * 5) : 0;
  const hi = values.length ? Math.min(100, Math.ceil((Math.max(...values) + 2) / 5) * 5) : 100;
  const ticks = Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * i) / 5);
  const shown = shop ? shops.filter((s) => s.shop_id === shop) : shops;
  const checkedAt = (source ?? data?.data.source)?.last_attempt_at;
  const sourceFailed = ["failed", "abandoned"].includes(source?.last_attempt_status ?? "");
  const showTrendDetails = !!version && !!latest && !!data && !error && selectedShops.length > 0;
  return (
    <>
      <div className="rating-heading">
        <h3>店铺评分</h3>
        <p>最新评分日期：{data?.data.latest_date ?? "—"} · 不随经营日期筛选变化</p>
      </div>
      {sourceFailed && (
        <p className="rating-source" role="status">
          {source?.last_success_at
            ? "评分最近检查未成功，当前显示上次成功数据。"
            : "评分最近检查未成功，暂未取得评分数据。"}
        </p>
      )}
      <div className="store-ratings">
        {(shown.length
          ? shown
          : [
              { shop_id: "spigf", display_name: "官方店" },
              { shop_id: "spihw", display_name: "户外店" },
              { shop_id: "spifs", display_name: "服饰店" },
              { shop_id: "spiyj", display_name: "雨具店" },
              { shop_id: "spixb", display_name: "箱包店" },
              { shop_id: "spitztx", display_name: "青少年服饰店" },
            ]
        ).map((s) => {
          const r = data?.data.latest.find((p) => p.shop_id === s.shop_id);
          const note =
            !version || !latest
              ? "暂无评分数据"
              : error
                ? "评分暂未取得"
                : !data
                  ? "评分读取中"
                  : r?.state === "observed" || r?.state === "legacy_basis"
                    ? null
                    : (ratingNotes[r?.state ?? ""] ?? "评分暂未取得");
          return (
            <Card className="store-rating-card" key={s.shop_id}>
              <span>{s.display_name}</span>
              <strong>{ratingValue(r)}</strong>
              <small>评分日期：{r?.date ?? "—"}</small>
              {note && <small>{note}</small>}
            </Card>
          );
        })}
      </div>
      <SalesExplanation title="查看评分说明">
        <p>“—”不代表0分；旧口径评分不与百分制评分直接比较。</p>
        <p>最近检查时间：{checkedAt ? localTime(checkedAt) : "—"}</p>
      </SalesExplanation>
      <div className="rating-trend-panel">
        <div className="rating-heading">
          <h3>评分趋势</h3>
          <p>默认最近 30 天 · 日期筛选独立于经营数据</p>
        </div>
        <div className="rating-trend-controls">
          <fieldset>
            <legend>选择店铺</legend>
            {shops.map((s) => (
              <Checkbox
                key={s.shop_id}
                checked={selected.includes(s.shop_id)}
                onChange={(e) =>
                  setSelected((previous) =>
                    e.target.checked
                      ? [...previous, s.shop_id]
                      : previous.filter((x) => x !== s.shop_id),
                  )
                }
              >
                <i
                  className="rating-shop-dot"
                  style={{ background: colors[s.shop_id] }}
                  aria-hidden="true"
                />
                {s.display_name}
              </Checkbox>
            ))}
          </fieldset>
          <div className="region-date-filter">
            <span>趋势日期</span>
            <DatePicker
              aria-label="评分趋势开始日期"
              size="small"
              variant="borderless"
              disabled={!version || !latest}
              allowClear={false}
              value={from ? dayjs(from) : null}
              minDate={source?.observed_from ? dayjs(source.observed_from) : undefined}
              maxDate={latest ? dayjs(latest) : undefined}
              onChange={(d) => {
                if (d) {
                  const v = clampDate(d.format("YYYY-MM-DD"));
                  setRange({ from: v, to: v > to ? v : to });
                }
              }}
            />
            <span>至</span>
            <DatePicker
              aria-label="评分趋势结束日期"
              size="small"
              variant="borderless"
              disabled={!version || !latest}
              allowClear={false}
              value={to ? dayjs(to) : null}
              minDate={source?.observed_from ? dayjs(source.observed_from) : undefined}
              maxDate={latest ? dayjs(latest) : undefined}
              onChange={(d) => {
                if (d) {
                  const v = clampDate(d.format("YYYY-MM-DD"));
                  setRange({ from: v < from ? v : from, to: v });
                }
              }}
            />
          </div>
        </div>
        {error ? (
          <p role="status">
            {error}
            <Button size="small" onClick={retry}>
              重试评分
            </Button>
          </p>
        ) : !version || !latest ? (
          <p className="rating-empty" role="status">
            暂无评分数据。
          </p>
        ) : !selectedShops.length ? (
          <p className="rating-empty" role="status">
            请选择店铺。
          </p>
        ) : !data ? (
          <p className="rating-empty" role="status">
            正在加载评分趋势…
          </p>
        ) : !values.length ? (
          <p className="rating-empty" role="status">
            所选店铺在这段日期暂无可绘制的评分，可展开明细查看。
          </p>
        ) : (
          <div className="rating-chart" style={{ width: "100%", height: 310 }}>
            <span className="rating-chart-unit">评分（分）</span>
            <ResponsiveContainer width="100%" height={310}>
              <LineChart
                data={chart}
                margin={{ top: 40, right: 60, left: 0, bottom: 25 }}
                accessibilityLayer
              >
                <CartesianGrid vertical={false} stroke="#dce4ee" />
                <XAxis
                  dataKey="date"
                  ticks={chart
                    .filter(
                      (_, i) =>
                        i === 0 ||
                        i === chart.length - 1 ||
                        i % Math.max(1, Math.ceil((chart.length - 1) / 5)) === 0,
                    )
                    .slice(0, 6)
                    .map((r) => r.date!)}
                  tickFormatter={(s) => String(s).slice(5)}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis domain={[lo, hi]} ticks={ticks} tickLine={false} axisLine={false} />
                <Tooltip
                  filterNull={false}
                  content={({ active, label }) =>
                    active && label ? (
                      <div className="rating-trend-tooltip">
                        <b>{String(label)}</b>
                        {selectedShops.map((s) => (
                          <div key={s.shop_id}>
                            <i
                              className="rating-shop-dot"
                              style={{ background: colors[s.shop_id] }}
                              aria-hidden="true"
                            />
                            <span>{s.display_name}</span>
                            <strong>{trendValue(pointByDay.get(`${label}/${s.shop_id}`))}</strong>
                          </div>
                        ))}
                      </div>
                    ) : null
                  }
                />
                {selectedShops.map((s) => (
                  <Line
                    key={s.shop_id}
                    name={s.display_name}
                    dataKey={s.shop_id}
                    stroke={colors[s.shop_id]}
                    dot={{ r: 2.5, fill: colors[s.shop_id], strokeWidth: 0 }}
                    activeDot={{ r: 4 }}
                    strokeWidth={2}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {showTrendDetails && <p className="rating-source">每日评分；缺失或异常处断线。</p>}
        <details className="rating-values" hidden={!showTrendDetails}>
          <summary>查看评分趋势明细</summary>
          {showTrendDetails && (
            <div className="rating-values-scroll">
              <Table
                rowKey="date"
                size="small"
                pagination={false}
                tableLayout="fixed"
                scroll={{ x: Math.max(620, 140 + selectedShops.length * 160) }}
                dataSource={chart}
                components={{
                  table: (p: React.HTMLAttributes<HTMLTableElement>) => (
                    <table {...p} aria-label="评分趋势明细" />
                  ),
                }}
                columns={[
                  { title: "日期", dataIndex: "date", width: 140, align: "left" },
                  ...selectedShops.map((s) => ({
                    title: s.display_name,
                    key: s.shop_id,
                    align: "center" as const,
                    render: (_: unknown, r: Record<string, string | null>) =>
                      trendValue(pointByDay.get(`${r.date}/${s.shop_id}`)),
                  })),
                ]}
                locale={{ emptyText: "暂无当前条件评分" }}
              />
            </div>
          )}
        </details>
      </div>
    </>
  );
});
