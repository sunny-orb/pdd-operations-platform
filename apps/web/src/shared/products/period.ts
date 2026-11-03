import dayjs from "dayjs";
/** 商品主区与中心风险共用默认31个日历日，来源不足31日时从实际起日开始。 */
export function defaultProductPeriod(meta: { date_from: string; date_to: string }) {
  const from = dayjs(meta.date_to).subtract(30, "day").format("YYYY-MM-DD");
  return { start: from < meta.date_from ? meta.date_from : from, end: meta.date_to };
}
