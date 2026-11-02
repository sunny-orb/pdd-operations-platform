import { Table } from "antd";
import { dayPage, type DayPage } from "./contracts";
import { useDebounced, useResource, type ProductsState } from "./data";
import { Filters } from "./Filters";
import { State } from "./State";
import { change, format } from "./format";
import type { Scope } from "./scope";
export function Days({ state, scope }: { state: ProductsState; scope: Scope }) {
  const keyword = useDebounced(scope.keyword);
  const result = useResource("days", dayPage, { ...scope, keyword }, state);
  type Row = DayPage["items"][number];
  const columns = [
    { title: "日期", dataIndex: "date", width: 110 },
    {
      title: "商品 / 商品 ID / 店铺",
      key: "product",
      width: 280,
      render: (_: unknown, r: Row) => (
        <>
          <b>{r.product_name ?? "未命名商品"}</b>
          <small>
            {r.product_id} · {r.shop_name}
          </small>
        </>
      ),
    },
    {
      title: "运营",
      dataIndex: "operator",
      width: 100,
      render: (v: string | null, r: Row) =>
        v ?? (r.field_status.operator === "conflict" ? "资料冲突" : "未匹配"),
    },
    {
      title: "品类",
      dataIndex: "category",
      width: 100,
      render: (v: string | null, r: Row) =>
        v ?? (r.field_status.category === "conflict" ? "资料冲突" : "未匹配"),
    },
    { title: "等级", dataIndex: "level", width: 85, render: (v: string | null) => v ?? "未匹配" },
    {
      title: "访客数",
      dataIndex: "visitors",
      width: 105,
      align: "right" as const,
      render: (v: string | null) => format(v),
    },
    {
      title: "交易金额",
      dataIndex: "revenue",
      width: 130,
      align: "right" as const,
      render: (v: string | null) => format(v, "currency"),
    },
    {
      title: "转化率",
      dataIndex: "conversion",
      width: 100,
      align: "right" as const,
      render: (v: string | null) => format(v, "percent"),
    },
    {
      title: "交易金额同比",
      dataIndex: "yoy",
      width: 130,
      align: "right" as const,
      render: (v: string | null) => change(v),
    },
  ];
  return (
    <section className="detail-card" id="product-days">
      <div className="detail-heading">
        <span>商品逐日明细</span>
        <b>独立筛选 · 逐日追踪商品数据</b>
        <small>按当前资料归属；与经营总览及新品评分筛选独立。真实零保留，缺值显示 —。</small>
      </div>
      <Filters name="detail" scope={scope} state={state} />
      <State
        pending={!!state.version && result.isPending}
        error={result.error}
        retry={() => void result.refetch()}
      />
      <div className="m1-detail-count">
        共 {result.data?.total ?? "—"} 条记录 · {scope.from || "—"} 至 {scope.to || "—"} · 店铺 ×
        商品 × 日期
      </div>
      <Table
        size="small"
        rowKey={(r) => `${r.date}/${r.listing_id}`}
        columns={columns}
        dataSource={result.data?.items ?? []}
        scroll={{ x: 1180 }}
        pagination={{
          current: scope.page,
          pageSize: scope.size,
          total: result.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [25, 50, 100],
          onChange: (page, size) => state.change("detail", { page, size }),
          showTotal: (total) => `共 ${total} 条`,
        }}
      />
    </section>
  );
}
