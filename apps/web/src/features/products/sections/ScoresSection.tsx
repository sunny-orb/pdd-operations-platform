import { useState } from "react";
import { BarChartOutlined, InfoCircleOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Modal,
  Pagination,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type {
  Filters,
  Mode,
  Options,
  Scenario,
  Score,
  ScoreMetric,
  Scores,
} from "../api/contracts";
import { useProductQuery } from "../api/queries";
import { FiltersBar } from "../components/FiltersBar";
import { SectionState } from "../components/SectionState";
import { displayValue } from "../components/display";
import { buildDefaultFilters } from "../state/filters";

type Props = {
  mode: Mode;
  scenario: Scenario;
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  options: Options | null;
  versionToken: number;
  onVersionConflict?: () => void;
};

const groups = [
  { key: "complete", title: "完整验证 · 放量建议", color: "green" },
  { key: "partial", title: "待验证 · 仅展示已验证得分", color: "gold" },
  { key: "unscored", title: "暂未评分 · 继续观察", color: "default" },
] as const;

/** 复用统一展示格式，保留null，不在浏览器计算评分。 */
const metricValue = displayValue;

function ScoreCard({ item, open }: { item: Score; open: () => void }) {
  const group = groups.find(({ key }) => key === item.status)!;
  return (
    <Card size="small" className="score-card">
      <div className="score-meta">
        <span>
          {item.product.category} · {item.product.shopName}
        </span>
        <Tag color={group.color}>
          {item.status === "complete"
            ? "完整验证"
            : item.status === "partial"
              ? "待验证"
              : "未评分"}
        </Tag>
      </div>
      <Typography.Title level={5}>{item.product.name}</Typography.Title>
      <div className="muted">
        商品ID：{item.product.id} · {item.product.operator}
      </div>
      <div className="score-value">
        <div>
          <span className="muted">{item.status === "complete" ? "完整评分" : "已验证得分"}</span>
          <strong>
            {item.status === "unscored"
              ? "—"
              : item.status === "complete"
                ? (item.score ?? "—")
                : item.verifiedScore}
            <small> 分</small>
          </strong>
        </div>
        <div>
          <span className="muted">待验证权重</span>
          <b>{item.pendingWeight} 分</b>
        </div>
      </div>
      <Typography.Paragraph className="score-recommendation">
        {item.recommendation}
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary" className="score-notes">
        {item.reason || `${item.stage} · 上市${item.age}天`}
      </Typography.Paragraph>
      <Button
        block
        icon={<BarChartOutlined />}
        onClick={open}
        aria-label={`查看${item.product.name}档位`}
      >
        查看表现档位
      </Button>
    </Card>
  );
}

function ScoreDetails({ score, isPreview }: { score: Score; isPreview: boolean }) {
  const columns: ColumnsType<ScoreMetric> = [
    {
      title: "指标 / 历史参照",
      dataIndex: "label",
      key: "label",
      width: 190,
      render: (_, item) => (
        <>
          <strong>{item.label}</strong>
          <div className="muted">{item.reference}</div>
          <div className="muted">
            本组 {item.sampleSize} 款{item.reason ? ` · ${item.reason}` : ""}
          </div>
        </>
      ),
    },
    { title: "权重", dataIndex: "weight", key: "weight", width: 60 },
    {
      title: "实际值",
      key: "value",
      width: 95,
      render: (_, item) => metricValue(item.value, item.format),
    },
    ...["基础", "良好", "优秀", "突出"].map((title, index) => ({
      title,
      key: `tier-${index}`,
      width: 80,
      render: (_: unknown, item: ScoreMetric) => metricValue(item.tiers[index], item.format),
    })),
    {
      title: "当前档位",
      dataIndex: "grade",
      key: "grade",
      width: 95,
      render: (grade: string) => (
        <Tag color={grade === "待验证" ? "gold" : "blue"}>{grade || "待验证"}</Tag>
      ),
    },
    {
      title: "得分",
      key: "points",
      width: 80,
      render: (_, item) => (item.points === null ? "—" : `${item.points} / ${item.weight}`),
    },
  ];
  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Tag color={score.status === "complete" ? "green" : "gold"}>
          {score.status === "complete"
            ? "完整评分"
            : score.status === "partial"
              ? "待验证"
              : "暂未评分"}
        </Tag>
        {isPreview && <Tag color="orange">开发样例 · 非真实评分</Tag>}
        <strong>{score.recommendation}</strong>
      </Space>
      <Descriptions
        size="small"
        column={{ xs: 1, sm: 2, md: 3 }}
        items={[
          { key: "shop", label: "店铺", children: score.product.shopName },
          { key: "operator", label: "负责运营", children: score.product.operator },
          { key: "category", label: "品类", children: score.product.category },
          { key: "age", label: "上市阶段", children: `${score.age}天 · ${score.stage}` },
          { key: "model", label: "模型版本", children: score.modelVersion },
          { key: "date", label: "评分截至", children: score.date },
          {
            key: "verified",
            label: "已验证得分",
            children: score.status === "unscored" ? "—" : `${score.verifiedScore}分`,
          },
          { key: "pending", label: "待验证权重", children: `${score.pendingWeight}分` },
          {
            key: "complete",
            label: "完整分数",
            children: score.score === null ? "—（条件未齐）" : `${score.score}分`,
          },
        ]}
      />
      {score.reason && (
        <Typography.Paragraph type="secondary">
          <InfoCircleOutlined /> {score.reason}
        </Typography.Paragraph>
      )}
      <Table<ScoreMetric>
        size="small"
        columns={columns}
        dataSource={score.metrics}
        rowKey="key"
        pagination={false}
        scroll={{ x: 950 }}
        locale={{ emptyText: "暂无可评分指标，等待完整数据" }}
      />
      <Typography.Paragraph type="secondary">
        分数、参照档位与建议来自评分接口。缺项不折算成完整总分；建议不代表已自动调整预算。
      </Typography.Paragraph>
    </Space>
  );
}

/** 独立新品筛选与分页；点击后才请求详情，不阻塞商品经营总览。 */
export function ScoresSection({
  mode,
  scenario,
  filters,
  onChange,
  options,
  versionToken,
  onVersionConflict,
}: Props) {
  const [selected, setSelected] = useState<Score | null>(null);
  const requestOptions = { mode, scenario, versionToken };
  const query = useProductQuery<Scores>("scores", "scores", filters, requestOptions);
  const details = useProductQuery<Score>(
    `scores/${selected?.id ?? "unselected"}`,
    "scores",
    filters,
    requestOptions,
    selected !== null,
  );
  return (
    <section id="product-scores" className="product-section" aria-labelledby="scores-heading">
      <div className="section-heading">
        <div>
          <span className="section-eyebrow">新品潜力四档评分 · 放量决策</span>
          <h2 id="scores-heading">查看表现档位与放量建议</h2>
          <p className="muted">固定分析等级为“新品”的商品，独立筛选，不影响下方经营总览。</p>
        </div>
        <Tag icon={<BarChartOutlined />} color="blue">
          评分与放量建议
        </Tag>
      </div>
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "policy",
            label: "查看评分与放量口径",
            children: (
              <Typography.Paragraph type="secondary">
                按品类、季节和生命周期比较指标档位，区分完整验证、待验证与暂未评分。评分由后端模型任务提供；当前开发样例用于验证交互，不代表模型已验收。缺少推广或历史窗口时展示原因，不自动补零或放大得分。
              </Typography.Paragraph>
            ),
          },
        ]}
      />
      <FiltersBar
        scope="scores"
        score
        filters={filters}
        onChange={onChange}
        options={options}
        onReset={() => onChange(buildDefaultFilters())}
      />
      <SectionState query={query} onVersionConflict={onVersionConflict}>
        {(data) => (
          <>
            <div className="section-count">
              <Typography.Text type="secondary">
                共 {data.total} 款新品 · 完整验证 {data.counts.complete} · 待验证{" "}
                {data.counts.partial} · 未评分 {data.counts.unscored}
              </Typography.Text>
              <Typography.Text type="secondary">
                评分截至：{query.data?.meta.dataThrough ?? "—"}
              </Typography.Text>
            </div>
            {groups.map((group) => {
              const items = data.items.filter((item) => item.status === group.key);
              return items.length ? (
                <div className="score-group" key={group.key}>
                  <Typography.Title level={5}>{group.title}</Typography.Title>
                  <div className="score-grid">
                    {items.map((item) => (
                      <ScoreCard key={item.id} item={item} open={() => setSelected(item)} />
                    ))}
                  </div>
                </div>
              ) : null;
            })}
            <Pagination
              align="end"
              size="small"
              current={filters.page}
              pageSize={filters.pageSize}
              total={data.total}
              pageSizeOptions={[6, 12, 25]}
              showSizeChanger
              showTotal={(total) => `共 ${total} 款`}
              onChange={(page, pageSize) => onChange({ page, pageSize })}
            />
          </>
        )}
      </SectionState>
      <Modal
        open={selected !== null}
        title={selected ? `${selected.product.name} · 表现档位与放量建议` : "表现档位与放量建议"}
        onCancel={() => setSelected(null)}
        footer={<Button onClick={() => setSelected(null)}>关闭</Button>}
        width={1100}
        destroyOnHidden
        keyboard
        focusable={{ focusTriggerAfterClose: true }}
      >
        <SectionState query={details} onVersionConflict={onVersionConflict}>
          {(score) => (
            <ScoreDetails score={score} isPreview={details.data?.meta.isPreview ?? false} />
          )}
        </SectionState>
      </Modal>
    </section>
  );
}
