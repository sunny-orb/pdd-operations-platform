import { Alert, Card, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { DayRow, Days, Filters, Mode, Options, Scenario } from "../api/contracts";
import { useProductQuery } from "../api/queries";
import { FiltersBar } from "../components/FiltersBar";
import { SectionHeading } from "../components/SectionHeading";
import { SectionState } from "../components/SectionState";
import { displayChange, displayValue, isNegative } from "../components/display";

const columns: ColumnsType<DayRow> = [
  { title: "日期", dataIndex: "date", width: 115 },
  {
    title: "商品",
    width: 225,
    render: (_, row) => (
      <div className="table-product">
        <strong>{row.product.name}</strong>
        <small>
          {row.product.id} · {row.product.shopName}
        </small>
      </div>
    ),
  },
  { title: "款式对应运营", width: 115, render: (_, row) => row.product.operator || "未匹配" },
  { title: "品类", width: 100, render: (_, row) => row.product.category },
  {
    title: "等级",
    width: 90,
    render: (_, row) => (
      <Tag color={row.product.level === "新品" ? "blue" : undefined} variant="filled">
        {row.product.level}
      </Tag>
    ),
  },
  {
    title: "访客数",
    dataIndex: "visitors",
    align: "right",
    width: 100,
    render: (value) => displayValue(value),
  },
  {
    title: "交易金额",
    dataIndex: "revenue",
    align: "right",
    width: 135,
    render: (value) => <b>{displayValue(value, "money")}</b>,
  },
  {
    title: "支付转化率",
    dataIndex: "conversion",
    align: "right",
    width: 115,
    render: (value) => displayValue(value, "percent"),
  },
  {
    title: "交易金额同比",
    dataIndex: "yoy",
    align: "right",
    width: 130,
    render: (value) => (
      <span className={value == null ? "muted" : isNegative(value) ? "negative" : "positive"}>
        {displayChange(value)}
      </span>
    ),
  },
];
export function DaysSection({
  mode,
  scenario,
  filters,
  onChange,
  onReset,
  options,
  versionToken,
  optionsError,
}: {
  mode: Mode;
  scenario: Scenario;
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  onReset: () => void;
  options: Options | null;
  versionToken: number;
  optionsError?: Error | null;
}) {
  const query = useProductQuery<Days>("days", "days", filters, { mode, scenario, versionToken });
  return (
    <section id="product-days" className="product-section" aria-label="商品明细底表">
      <SectionHeading
        eyebrow="商品明细"
        title="筛选单款数据"
        description="按条件查看完整商品日明细，不影响上方经营总览。"
        extra={<Tag variant="filled">店铺 × 商品 × 自然日</Tag>}
      />
      <Card size="small">
        {optionsError && (
          <Alert
            type="error"
            showIcon
            title="明细筛选加载失败"
            description={optionsError.message}
          />
        )}
        <FiltersBar
          scope="days"
          filters={filters}
          options={options}
          onChange={onChange}
          onReset={onReset}
        />
        <SectionState query={query}>
          {(data) => (
            <Table<DayRow>
              size="middle"
              rowKey="key"
              dataSource={data.items}
              columns={columns}
              scroll={{ x: 1225 }}
              pagination={{
                current: filters.page,
                pageSize: filters.pageSize,
                total: data.total,
                showSizeChanger: true,
                pageSizeOptions: [10, 25, 50, 100],
                showTotal: (total) => `共 ${total} 条结果`,
                onChange: (page, pageSize) => onChange({ page, pageSize }),
              }}
            />
          )}
        </SectionState>
      </Card>
    </section>
  );
}
