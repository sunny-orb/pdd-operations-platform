import type { Board } from "./contracts";
import { change, format } from "./format";

/** 固定周期展示；金额、涨跌及比较日期均取同一次接口响应。 */
export function PeriodWindows({ data, metric }: { data?: Board; metric: string }) {
  const definition = data?.metrics[metric];
  const kind = definition?.type ?? "currency";
  return (
    <div className="product-periods">
      <section className="period-band" aria-label="近7、14、30日周期表现">
        <div>
          <span>近 7 / 14 / 30 日趋势</span>
          <b>{definition?.label ?? "交易金额"}周期表现</b>
          <small>环比为相邻等长周期对比</small>
        </div>
        {[7, 14, 30].map((days) => {
          const value = data?.windows.find((w) => w.days === days)?.metrics[metric];
          return (
            <div className="period-stat" key={days}>
              <span>近 {days} 日</span>
              <b>{format(value?.value, kind)}</b>
              <small>
                {value?.change != null
                  ? `较前${days}日 ${change(value.change)}`
                  : (value?.message ?? "—")}
              </small>
            </div>
          );
        })}
      </section>
      <details className="product-period-details">
        <summary>查看比较日期与说明</summary>
        <div className="product-period-table">
          <table>
            <thead>
              <tr>
                <th>周期</th>
                <th>本期</th>
                <th>前期</th>
                <th>前期数值</th>
              </tr>
            </thead>
            <tbody>
              {data?.windows.map((w) => (
                <tr key={w.days}>
                  <th>近{w.days}日</th>
                  <td>
                    {w.start} 至 {w.end}
                  </td>
                  <td>
                    {w.previous_start} 至 {w.previous_end}
                  </td>
                  <td>{format(w.metrics[metric]?.previous, kind)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          截至所选结束日，按上面的固定日期分别统计两期商品，不受开始日限制。没有记录的日期不补0，也不往前凑天数；前期为0时不算涨跌。
        </p>
      </details>
    </div>
  );
}
