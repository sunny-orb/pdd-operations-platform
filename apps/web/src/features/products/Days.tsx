import { memo, useLayoutEffect, useRef, useState } from "react";
import { Button } from "antd";
import { dayPage, type DayPage } from "./contracts";
import { useResource, type ProductsState } from "./data";
import { format, percent } from "./format";
import { Filters } from "./Filters";
import { ProductSelectionSummary } from "./ProductSelectionSummary";
import { scopeError, type Scope } from "./scope";
import { formatProductLevel } from "../../shared/products/levels";
import "./Days.css";

const headings = [
  "日期",
  "商品",
  "款式对应运营",
  "品类",
  "等级",
  "访客数",
  "交易金额",
  "转化率",
  "交易金额同比",
];

/** 独立商品日明细；仅同版本、同筛选的翻页/换容量保留旧页，失败可重试。 */
export const Days = memo(function Days({ state, scope }: { state: ProductsState; scope: Scope }) {
  const result = useResource("days", dayPage, scope, state);
  const key = JSON.stringify([
    state.version,
    scope.from,
    scope.to,
    scope.shop,
    scope.operator,
    scope.category,
    scope.level,
    scope.products,
    scope.keyword,
  ]);
  const [snapshot, setSnapshot] = useState<{ key: string; data: DayPage | undefined }>({
    key,
    data: undefined,
  });
  const currentData = result.data;
  if (snapshot.key !== key || (currentData && snapshot.data !== currentData)) {
    setSnapshot({ key, data: currentData });
  }
  const data = currentData ?? (snapshot.key === key ? snapshot.data : undefined);
  const busy = !!state.version && result.isFetching;
  const current = data?.page ?? scope.page;
  const size = data?.size ?? scope.size;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / size));
  const error = result.error;
  const invalid = scopeError(scope, state.metadata.data?.date_from, state.metadata.data?.date_to);
  const failedPage = !!error && !!data && (current !== scope.page || size !== scope.size);
  const wrap = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (wrap.current) wrap.current.scrollTop = 0;
  }, [key, current, size]);
  const disabled = !data || busy || failedPage;
  return (
    <section className="detail-card m1-product-days" id="product-days">
      <div className="detail-heading">
        <small>商品逐日明细</small>
        <b>筛选单款数据</b>
        <em>按条件查看明细，不影响上方经营总览和新品评分</em>
      </div>
      <div className="detail-body">
        <Filters name="detail" scope={scope} state={state} />
        <div className="product-bar m1-detail-selection">
          <ProductSelectionSummary scope={scope} state={state} name="detail" ready={!!data} />
        </div>
        <div className="detail-status">
          <span>
            共 <b>{data?.total.toLocaleString("zh-CN") ?? "—"}</b> 条结果
          </span>
          <span>
            第 {current} / {pages} 页
          </span>
          <div className="page-track" aria-hidden="true">
            <i style={{ width: `${data?.total ? (current / pages) * 100 : 0}%` }} />
          </div>
          <span className="m1-days-feedback" role="status" aria-live="polite">
            {invalid ? (
              invalid
            ) : error ? (
              <>
                {failedPage
                  ? size !== scope.size
                    ? "每页条数切换失败，仍显示原页。"
                    : `第${scope.page}页加载失败，仍显示第${current}页。`
                  : "明细暂时无法加载。"}
                <Button
                  type="link"
                  size="small"
                  disabled={busy}
                  onClick={() => void result.refetch()}
                >
                  重试
                </Button>
              </>
            ) : busy ? (
              "正在加载…"
            ) : null}
          </span>
        </div>
        <div
          className="table-wrap m1-days-table"
          ref={wrap}
          aria-label="商品逐日明细表格"
          aria-busy={busy}
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                {headings.map((heading) => (
                  <th scope="col" key={heading}>
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={`${row.date}/${row.listing_id}`}>
                  <td>{row.date}</td>
                  <td className="m1-days-product">
                    <b>{row.product_name ?? "未命名商品"}</b>
                    <small>
                      {row.product_id} · {row.shop_name}
                    </small>
                  </td>
                  <td>
                    {row.operator ??
                      (row.field_status.operator === "conflict" ? "资料冲突" : "未匹配")}
                  </td>
                  <td>
                    {row.category ??
                      (row.field_status.category === "conflict" ? "资料冲突" : "未匹配")}
                  </td>
                  <td>
                    <span className="tag">{formatProductLevel(row.level)}</span>
                  </td>
                  <td>{format(row.visitors)}</td>
                  <td>{format(row.revenue, "currency")}</td>
                  <td>{format(row.conversion, "percent")}</td>
                  <td
                    className={
                      row.yoy === null
                        ? undefined
                        : row.yoy.startsWith("-")
                          ? "down-text"
                          : "up-text"
                    }
                  >
                    {row.yoy === null ? (
                      <span title="当日或去年同日金额不可用，或去年金额为0，暂不计算同比">—</span>
                    ) : (
                      `${row.yoy.startsWith("-") ? "" : "+"}${percent(row.yoy)}`
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.items.length && (
            <p className="m1-days-empty">
              {invalid
                ? "请调整筛选日期"
                : busy
                  ? "正在加载明细…"
                  : error
                    ? "请重试加载明细"
                    : data
                      ? "当前筛选范围暂无记录"
                      : "暂无可用明细"}
            </p>
          )}
        </div>
        <div className="m1-days-footer">
          <p>0表示实际为0；“—”表示未提供数值或无法计算。运营、品类和等级按当前商品资料展示。</p>
          <div className="detail-pager">
            <Button
              title="上一页"
              disabled={disabled || current <= 1}
              onClick={() => state.change("detail", { page: current - 1 })}
            >
              上一页
            </Button>
            <select
              aria-label="明细每页显示条数"
              value={size}
              disabled={disabled}
              onChange={(event) =>
                state.change("detail", { page: 1, size: Number(event.target.value) })
              }
            >
              {[25, 50, 100].map((value) => (
                <option key={value} value={value}>
                  每页 {value} 条
                </option>
              ))}
            </select>
            <Button
              title="下一页"
              disabled={disabled || current >= pages}
              onClick={() => state.change("detail", { page: current + 1 })}
            >
              下一页
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
});
