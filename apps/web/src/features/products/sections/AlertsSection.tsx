import { Card, Space, Table, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Line, LineChart } from "recharts";
import type { Alerts, Filters, Mode, ProductAlert, Scenario } from "../api/contracts";
import { useProductQuery } from "../api/queries";
import { SectionHeading } from "../components/SectionHeading";
import { SectionState } from "../components/SectionState";
import { displayChange, displayValue } from "../components/display";

const columns: ColumnsType<ProductAlert> = [
  {
    title: "优先级",
    dataIndex: "priority",
    width: 80,
    render: (value) => (
      <Tag color={value === "P0" ? "red" : value === "P1" ? "orange" : "blue"}>{value}</Tag>
    ),
  },
  {
    title: "商品 / 等级",
    width: 175,
    render: (_, row) => (
      <div className="table-product">
        <strong>{row.product.name}</strong>
        <small>{row.product.shopName}</small>
        <Tag variant="filled">{row.product.level}</Tag>
      </div>
    ),
  },
  { title: "异常指标", dataIndex: "signal", width: 145 },
  {
    title: "当前 / 基准",
    width: 115,
    render: (_, row) => (
      <span>
        {displayValue(row.current)} / {displayValue(row.baseline)}
      </span>
    ),
  },
  {
    title: "变化",
    dataIndex: "change",
    width: 95,
    render: (value) => <span className="negative">{displayChange(value)}</span>,
  },
  {
    title: "近7日趋势",
    dataIndex: "trend",
    width: 105,
    render: (values: number[]) => (
      <span role="img" aria-label={`7日趋势：${values.join("、")}`}>
        <LineChart width={85} height={30} data={values.map((value) => ({ value }))}>
          <Line
            dataKey="value"
            stroke="#d65659"
            strokeWidth={1.7}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </span>
    ),
  },
  {
    title: "累计交易额",
    dataIndex: "revenue",
    width: 125,
    render: (value) => displayValue(value, "money"),
  },
  {
    title: "估算影响金额",
    dataIndex: "impact",
    width: 125,
    render: (value) => (
      <Tooltip title="基于规则估算，并非已确认实际损失">
        <span className="negative">{displayValue(value, "money")}</span>
      </Tooltip>
    ),
  },
  {
    title: "原因与建议动作",
    width: 260,
    render: (_, row) => (
      <>
        <Space size={2} wrap>
          {row.reasons.map((reason) => (
            <Tag key={reason} variant="filled">
              {reason}
            </Tag>
          ))}
        </Space>
        <p className="table-action">{row.action}</p>
      </>
    ),
  },
  { title: "处理期限", dataIndex: "dueAt", width: 115, render: (value) => value || "—" },
  {
    title: "处理状态",
    dataIndex: "workflowState",
    width: 110,
    render: (value) => <Tag variant="filled">{value ?? "尚未启用"}</Tag>,
  },
];
export function AlertsSection({
  filters,
  mode,
  scenario,
  versionToken,
}: {
  filters: Filters;
  mode: Mode;
  scenario: Scenario;
  versionToken: number;
}) {
  const query = useProductQuery<Alerts>("alerts", "overview", filters, {
    mode,
    scenario,
    versionToken,
  });
  return (
    <section id="risk" className="product-section" aria-label="异常影响金额与行动闭环">
      <SectionHeading
        eyebrow="可执行风险清单"
        title="异常、影响金额与行动闭环"
        description="先看优先级与指标证据，再决定动作；估算金额不代表实际损失。"
        extra={
          <Space size={6} wrap>
            <Tag color="red">P0 紧急</Tag>
            <Tag color="orange">P1 优先</Tag>
            <Tag color="blue">P2 关注</Tag>
          </Space>
        }
      />
      <Card size="small">
        <SectionState query={query}>
          {(data) => (
            <Table<ProductAlert>
              size="small"
              columns={columns}
              dataSource={data.items}
              rowKey="id"
              pagination={false}
              scroll={{ x: 1460 }}
              locale={{ emptyText: "当前范围未发现达到规则门槛的异常" }}
            />
          )}
        </SectionState>
        <div className="table-footnote">
          处理闭环尚未启用 · 当前仅展示证据及建议，不会回写数仓或自动调整预算
        </div>
      </Card>
    </section>
  );
}
