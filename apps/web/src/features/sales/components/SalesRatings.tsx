import { useMemo, useState } from "react";
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
import { fixed } from "../format";
const colors = ["#2070bf", "#15805d", "#d58e20", "#9266bf", "#df6c6c", "#3c9da3"];
export function SalesRatings({
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
  const latest = source?.observed_through ?? "";
  const [selected, setSelected] = useState(["spigf", "spihw", "spifs"]);
  const [range, setRange] = useState<{ from: string; to: string } | null>(null);
  const from =
    range?.from ?? (latest ? dayjs(latest).subtract(29, "day").format("YYYY-MM-DD") : "");
  const to = range?.to ?? latest;
  const version = filters?.data.capabilities.ratings ? filters.meta.data_version : undefined;
  const { data, error, retry } = useSalesDetail(
    detailPath("ratings", version, { date_from: from, date_to: to }),
    ratingsSchema,
    onConflict,
  );
  const points = data?.data.points;
  const chart = useMemo(() => {
    const rows: Record<string, string | null>[] = [];
    if (!from || !to) return rows;
    for (let d = dayjs(from); d.isBefore(dayjs(to).add(1, "day")); d = d.add(1, "day")) {
      const date = d.format("YYYY-MM-DD");
      const row: Record<string, string | null> = { date };
      for (const id of selected)
        row[id] = points?.find((p) => p.date === date && p.shop_id === id)?.score ?? null;
      rows.push(row);
    }
    return rows;
  }, [from, to, selected, points]);
  const values = chart.flatMap((r) =>
    selected.flatMap((id) => (r[id] == null ? [] : [Number(r[id])])),
  );
  const lo = values.length ? Math.max(0, Math.floor((Math.min(...values) - 2) / 5) * 5) : 0;
  const hi = values.length ? Math.min(100, Math.ceil((Math.max(...values) + 2) / 5) * 5) : 100;
  const ticks = Array.from({ length: 6 }, (_, i) => lo + ((hi - lo) * i) / 5);
  const shown = shop ? shops.filter((s) => s.shop_id === shop) : shops;
  return (
    <>
      <div className="rating-heading">
        <h3>店铺评分</h3>
        <p>最新评分日期：{data?.data.latest_date ?? "—"} · 不随经营日期筛选变化</p>
      </div>
      <div className="store-ratings">
        {(shown.length
          ? shown
          : [
              { shop_id: "spigf", display_name: "官方店" },
              { shop_id: "spihw", display_name: "户外店" },
              { shop_id: "spifs", display_name: "服饰店" },
              { shop_id: "spiyj", display_name: "雨具店" },
              { shop_id: "spixb", display_name: "箱包店" },
              { shop_id: "spitztx", display_name: "童装童鞋店" },
            ]
        ).map((s) => {
          const r = data?.data.latest.find((p) => p.shop_id === s.shop_id);
          return (
            <Card className="store-rating-card" key={s.shop_id}>
              <span>{s.display_name}</span>
              <strong>
                {r?.state === "legacy_basis" ? `${fixed(r.raw_score)}（旧口径）` : fixed(r?.score)}
              </strong>
              <small>
                评分日期：{r?.date ?? "—"} ·{" "}
                {r?.state === "observed"
                  ? "百分制"
                  : r?.state === "legacy_basis"
                    ? "旧口径，不连线"
                    : "缺记录或未接入"}
              </small>
            </Card>
          );
        })}
      </div>
      <p className="rating-source">
        来源：ods_rpa_pdd_evaluation_overview_f · 字段 shop_rating_score · 来源创建时间原文：
        {data?.data.latest.map((r) => r.source_created_time_raw).find(Boolean) ?? "—"}
        （时区未确认）。0–1 原值乘100展示；旧口径保留原值，空值不补零。
      </p>
      <div className="rating-trend-panel">
        <div className="rating-heading">
          <h3>评分趋势</h3>
          <p>默认最近 30 天 · 日期筛选独立于经营数据</p>
        </div>
        <div className="rating-trend-controls">
          <fieldset>
            <legend>选择店铺</legend>
            {shops.map((s, i) => (
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
                <i style={{ background: colors[i] }} />
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
              disabled={!version}
              allowClear={false}
              value={from ? dayjs(from) : null}
              minDate={source?.observed_from ? dayjs(source.observed_from) : undefined}
              maxDate={latest ? dayjs(latest) : undefined}
              onChange={(d) => {
                if (d) {
                  const v = d.format("YYYY-MM-DD");
                  setRange({ from: v, to: v > to ? v : to });
                }
              }}
            />
            <span>至</span>
            <DatePicker
              aria-label="评分趋势结束日期"
              size="small"
              variant="borderless"
              disabled={!version}
              allowClear={false}
              value={to ? dayjs(to) : null}
              minDate={source?.observed_from ? dayjs(source.observed_from) : undefined}
              maxDate={latest ? dayjs(latest) : undefined}
              onChange={(d) => {
                if (d) {
                  const v = d.format("YYYY-MM-DD");
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
        ) : !version ? (
          <p className="rating-empty">店铺评分尚未接入，暂无可展示的趋势。</p>
        ) : (
          <div className="rating-chart" style={{ width: "100%", height: 310 }}>
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
                <Tooltip formatter={(v) => fixed(String(v))} labelFormatter={(v) => String(v)} />
                {shops
                  .filter((s) => selected.includes(s.shop_id))
                  .map((s) => (
                    <Line
                      key={s.shop_id}
                      name={s.display_name}
                      dataKey={s.shop_id}
                      stroke={colors[shops.indexOf(s)]}
                      dot={false}
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
        <p className="rating-source">
          百分制日评分，不做平均；纵轴按所选数据范围缩放。空值、缺失日期及旧口径断线展示，不补零。悬浮或键盘方向键查看日期；详细原值可在下方展开查看。
        </p>
        <details className="rating-values">
          <summary>查看评分趋势明细</summary>
          <div className="rating-values-scroll">
            <Table
              rowKey="date"
              size="small"
              pagination={false}
              scroll={{ x: 620 }}
              dataSource={chart}
              components={{
                table: (p: React.HTMLAttributes<HTMLTableElement>) => (
                  <table {...p} aria-label="评分趋势明细" />
                ),
              }}
              columns={[
                { title: "日期", dataIndex: "date" },
                ...shops
                  .filter((s) => selected.includes(s.shop_id))
                  .map((s) => ({
                    title: s.display_name,
                    key: s.shop_id,
                    render: (_: unknown, r: Record<string, string | null>) => {
                      const p = points?.find((p) => p.date === r.date && p.shop_id === s.shop_id);
                      return p?.state === "legacy_basis"
                        ? `${fixed(p.raw_score)}（旧口径）`
                        : fixed(p?.score);
                    },
                  })),
              ]}
              locale={{ emptyText: "暂无当前条件评分" }}
            />
          </div>
        </details>
      </div>
    </>
  );
}
