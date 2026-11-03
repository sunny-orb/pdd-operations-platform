import { memo, useLayoutEffect, useRef, useState } from "react";
import { Button, Select, Spin } from "antd";
import type { Days } from "./contracts";
import type { PromotionState } from "./data";
import { formatValue } from "./format";

export const Details = memo(function Details({ state }: { state: PromotionState }) {
  const product = state.mode === "product";
  const data = state.days.data?.data;
  const [progress, setProgress] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const current = data?.page ?? state.scope.page;
  const pages = Math.max(1, data?.total_pages ?? 1);
  const busy = !product && state.days.isFetching;
  const failedPage = !product && !!state.days.error && current !== state.scope.page;
  const visiblePage = data?.page;
  useLayoutEffect(() => {
    if (!product && visiblePage != null && wrap.current) wrap.current.scrollTop = 0;
  }, [product, visiblePage]);
  const columns = product
    ? ["日期", "总花费", "交易额", "净交易额", "ROI", "成交笔数", "点击率", "点击转化率"]
    : ["日期", "店铺", "渠道", "花费", "交易额", "净交易额", "成交", "曝光", "点击", "来源"];
  const cell = (row: Days["items"][number]) =>
    product
      ? [
          row.date,
          formatValue(row.spend, "currency", 0),
          formatValue(row.revenue, "currency", 0),
          formatValue(row.net_revenue, "currency", 0),
          formatValue(row.roi, "ratio"),
          formatValue(row.deals),
          formatValue(row.ctr, "percent"),
          formatValue(row.cvr, "percent"),
        ]
      : [
          row.date,
          row.shop_name ?? "—",
          row.channel_label ?? "—",
          formatValue(row.spend, "currency"),
          formatValue(row.revenue, "currency"),
          formatValue(row.net_revenue, "currency"),
          formatValue(row.deals),
          formatValue(row.impressions),
          formatValue(row.clicks),
          row.source_label ?? "—",
        ];
  return (
    <section
      className={`panel table-panel${product ? "" : " m2-store-details"}`}
      id="promotion-days"
      aria-label={product ? "每日推广数据" : "店铺渠道明细"}
    >
      <div className="detail-heading">
        {product && <span>推广明细</span>}
        <h2>{product ? "每日推广数据" : "店铺渠道明细"}</h2>
        {product && <small>按日期汇总；右侧进度条可拖动上下浏览</small>}
      </div>
      <div className="detail-toolbar">
        <div className="result-summary">
          <b>
            共 {data?.total ?? "—"} 条{product ? "结果" : "记录"}
          </b>
          <span>
            第 {current} / {pages} 页
          </span>
          {state.days.isFetching && <Spin size="small" />}
        </div>
        <div className="detail-actions">
          {product && (
            <label>
              每页显示{" "}
              <Select
                aria-label="每页显示"
                value={state.scope.size}
                options={[25, 50, 100].map((value) => ({ value, label: `${value} 条` }))}
                onChange={(size) => {
                  state.change({ size });
                  setProgress(0);
                  if (wrap.current) wrap.current.scrollTop = 0;
                }}
              />
            </label>
          )}
          <Button
            disabled={!data || current <= 1 || busy || failedPage}
            onClick={() => {
              state.change({ page: current - 1 });
              setProgress(0);
              if (product && wrap.current) wrap.current.scrollTop = 0;
            }}
          >
            上一页
          </Button>
          {product && (
            <div className="progress" aria-label={`第 ${current} 页，共 ${pages} 页`}>
              <i style={{ width: `${Math.min(100, (current / pages) * 100)}%` }} />
            </div>
          )}
          <Button
            disabled={!data || current >= pages || busy || failedPage}
            onClick={() => {
              state.change({ page: current + 1 });
              setProgress(0);
              if (product && wrap.current) wrap.current.scrollTop = 0;
            }}
          >
            下一页
          </Button>
        </div>
      </div>
      {!product && state.days.error && (
        <div className="m2-detail-error" role="status">
          <span>
            {failedPage
              ? `第${state.scope.page}页加载失败，仍显示第${current}页。`
              : "明细暂时无法加载，请重试。"}
          </span>
          <Button
            aria-label="重试"
            size="small"
            disabled={busy}
            onClick={() => void state.days.refetch()}
          >
            重试
          </Button>
        </div>
      )}
      <div className={product ? "table-shell" : "m2-store-table"}>
        <div
          className={`table-wrap${!product && data?.items.length ? " has-rows" : ""}`}
          aria-busy={state.days.isFetching}
          ref={wrap}
          onScroll={(event) => {
            if (!product) return;
            const element = event.currentTarget;
            setProgress(
              element.scrollHeight <= element.clientHeight
                ? 0
                : (element.scrollTop / (element.scrollHeight - element.clientHeight)) * 100,
            );
          }}
        >
          <table>
            <thead>
              <tr>
                {columns.map((label) => (
                  <th key={label} scope="col">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={`${row.date}:${row.shop_id ?? ""}:${row.channel ?? ""}`}>
                  {cell(row).map((value, index) => (
                    <td key={index}>{value}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.items.length && (product || !state.days.error) && (
            <p className="m2-empty">
              {state.days.isPending && state.version ? "正在读取明细…" : "当前筛选范围暂无记录"}
            </p>
          )}
        </div>
        {product && (
          <label className="side-scroll">
            <span>浏览进度</span>
            <input
              type="range"
              min="0"
              max="100"
              aria-label="推广明细上下浏览进度"
              value={progress}
              onChange={(event) => {
                const value = Number(event.target.value);
                setProgress(value);
                if (wrap.current)
                  wrap.current.scrollTop =
                    ((wrap.current.scrollHeight - wrap.current.clientHeight) * value) / 100;
              }}
            />
            <b>{Math.round(progress)}%</b>
          </label>
        )}
      </div>
      <p className="m2-detail-note">
        {product
          ? "0 表示实际为0；“—”表示未提供数据或无法计算。"
          : "0 表示实际为0；“—”表示未提供该项数据。"}
      </p>
    </section>
  );
});
