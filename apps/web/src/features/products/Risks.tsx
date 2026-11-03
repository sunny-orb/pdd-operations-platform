import { useState } from "react";
import { Pagination, Table, Tag } from "antd";
import type { Board } from "./contracts";
import { change, format } from "./format";
import { MiniTrend } from "./Chart";
import { formatProductLevel } from "../../shared/products/levels";
import "./Risks.css";

const PAGE_SIZE = 5;

/** 只分页展示后端完整风险清单，不重排、不截断规则结果。 */
export function Risks({ data }: { data?: Board }) {
  const [paging, setPaging] = useState({ rows: data?.risks, page: 1 });
  // 筛选往返可能复用缓存数组；实际重置页码，避免返回旧筛选时跳回旧页。
  if (paging.rows !== data?.risks) setPaging({ rows: data?.risks, page: 1 });
  const page = paging.rows === data?.risks ? paging.page : 1;
  const rows = data?.risks ?? [];
  const visibleRows = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  type Row = Board["risks"][number];
  const columns = [
    {
      title: "优先级",
      key: "priority",
      width: 80,
      render: (_: unknown, r: Row) => (
        <span className={`priority ${r.alert.priority.toLowerCase()}`}>{r.alert.priority}</span>
      ),
    },
    {
      title: "商品 / 等级",
      key: "product",
      width: 265,
      render: (_: unknown, r: Row) => (
        <div
          className="m1-risk-product"
          title={`${r.product_name ?? r.product_id} · ${formatProductLevel(r.level)} · ${r.shop_name}`}
        >
          <b>{r.product_name ?? r.product_id}</b>
          <small>
            {formatProductLevel(r.level)} · {r.shop_name}
          </small>
        </div>
      ),
    },
    {
      title: "异常指标",
      key: "label",
      width: 150,
      render: (_: unknown, r: Row) => <span title={r.alert.basis}>{r.alert.label}</span>,
    },
    {
      title: "当前值",
      key: "now",
      width: 85,
      render: (_: unknown, r: Row) => format(r.alert.current, r.alert.type),
    },
    {
      title: "对比值",
      key: "before",
      width: 85,
      render: (_: unknown, r: Row) => format(r.alert.baseline, r.alert.type),
    },
    {
      title: "变化",
      key: "change",
      width: 85,
      render: (_: unknown, r: Row) => <span className="down-text">{change(r.alert.change)}</span>,
    },
    {
      title: "近7日趋势",
      key: "trend",
      width: 120,
      render: (_: unknown, r: Row) => (
        <div className="m1-risk-trend" title={r.alert.basis}>
          <MiniTrend values={r.alert.trend} />
          <small>
            {r.alert.type === "percent"
              ? "订单转化率"
              : r.alert.label === "有流量无成交"
                ? "支付订单数"
                : "商品访客数"}
          </small>
        </div>
      ),
    },
    {
      title: "累计交易额",
      key: "revenue",
      width: 130,
      render: (_: unknown, r: Row) => format(r.period_revenue, "currency"),
    },
    {
      title: "估算影响金额",
      key: "loss",
      width: 125,
      render: (_: unknown, r: Row) => (
        <span className="loss" title="规则估算，非已发生损失">
          {format(r.alert.expected_loss, "currency")}
        </span>
      ),
    },
    {
      title: "排查方向",
      key: "reasons",
      width: 215,
      render: (_: unknown, r: Row) => (
        <div className="reason-tags">
          {r.alert.reasons.map((v) => (
            <Tag key={v}>{v}</Tag>
          ))}
        </div>
      ),
    },
    {
      title: "建议动作",
      key: "action",
      width: 230,
      render: (_: unknown, r: Row) => <div className="m1-risk-action">{r.alert.action}</div>,
    },
  ];
  return (
    <section className="risk-card m1-risk-card" id="risk" aria-labelledby="risk-title">
      <div className="risk-heading">
        <div>
          <span>经营关注清单</span>
          <b id="risk-title">需要关注的商品与建议</b>
          <small>按规则筛出值得检查的商品，不代表已经确认经营异常。</small>
        </div>
        <div className="m1-risk-legend" aria-label="风险优先级说明">
          <div>
            <b className="priority p0">P0</b>
            <span>爆品且严重流量下滑</span>
          </div>
          <div>
            <b className="priority p1">P1</b>
            <span>有流量无成交、严重转化/流量下滑</span>
          </div>
          <div>
            <b className="priority p2">P2</b>
            <span>中度转化或流量下滑</span>
          </div>
        </div>
      </div>
      <div className="m1-risk-page">
        <Table
          className="m1-risk-table"
          rowKey="listing_id"
          size="small"
          columns={columns}
          dataSource={visibleRows}
          rowClassName={(row) => `m1-risk-row m1-risk-${row.alert.priority.toLowerCase()}`}
          tableLayout="fixed"
          pagination={false}
          scroll={{ x: 1640, scrollToFirstRowOnChange: false }}
          locale={{ emptyText: data ? "当前筛选范围没有达到风险条件的商品" : "正在加载风险清单" }}
        />
      </div>
      <div className="m1-risk-footer">
        <span role="status">共 {rows.length} 款</span>
        <Pagination
          aria-label="风险清单分页"
          current={page}
          total={rows.length}
          pageSize={PAGE_SIZE}
          showSizeChanger={false}
          showLessItems
          responsive
          onChange={(next) => setPaging({ rows: data?.risks, page: next })}
        />
      </div>
      <details className="m1-risk-guide">
        <summary>查看风险口径</summary>
        {data && (
          <p>
            观察日：{data.scope.end}；累计交易额：{data.scope.start} 至 {data.scope.end}。
          </p>
        )}
        <p>
          按P0、P1、P2及异常类型的固定优先顺序排列，不按跌幅或金额大小排名。排名前20%的商品在店铺、运营、品类、等级范围内确定，选单款或搜索不重新排名，同额并列一起纳入。
        </p>
        <p>
          当天达到门槛就提醒，不要求连续多天下滑，也不设最低估算影响金额。同一商品命中多个条件时，只展示按规则顺序最先命中的一项。
        </p>
        <p>近8天访客和订单有有效记录且达到样本门槛才判断；没有列入清单不代表没有风险。</p>
        <p>
          当天表现与前7天比较：流量看日均访客，转化看订单数÷访客数，有流量无成交看日均订单。近7日折线包含观察日。
        </p>
        <p>
          估算影响金额按过去7天的成交水平推算，不是已经损失的金额。排查方向和建议不代表原因已确认。
        </p>
      </details>
    </section>
  );
}
