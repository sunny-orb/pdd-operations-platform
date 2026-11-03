import type { z } from "zod";
import { supplementSchema } from "./contracts";
export function HistoricalSupplements({ rows }: { rows?: z.infer<typeof supplementSchema>[] }) {
  if (!rows?.length) return <p>历史补齐尚未接入；缺记录不视为零。</p>;
  const labels = { supplement: "补齐生效", warehouse: "数仓已替换", outside_range: "当前范围外" };
  return (
    <div>
      <p>
        历史补齐 {rows.length}{" "}
        条：有同店、同日、同渠道的数仓记录时优先用数仓，不重复相加。停投零值沿用原版确认记录；未提供的净额和成交笔数保持缺失。
      </p>
      <ul>
        {rows.map((row) => (
          <li key={`${row.shop_id}/${row.date}`}>
            {row.shop_name} · {row.date} ·{" "}
            {row.source_url ? (
              <a href={row.source_url} target="_blank" rel="noreferrer">
                {row.source}
              </a>
            ) : (
              row.source
            )}
            {" · 花费 "}
            {row.spend} 元 · 推广成交额 {row.revenue} 元 · {labels[row.status]}
          </li>
        ))}
      </ul>
    </div>
  );
}
