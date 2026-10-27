import { Card, Radio, Space, Tag, Tooltip } from "antd";
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  InfoCircleOutlined,
  RightOutlined,
} from "@ant-design/icons";
import type { Filters, MetricKey, Options, Overview } from "../api/contracts";
import { SectionState, type QueryView } from "../components/SectionState";
import { FiltersBar } from "../components/FiltersBar";
import { SectionHeading } from "../components/SectionHeading";
import { displayChange, displayValue, isNegative } from "../components/display";

export function OverviewSection({
  query,
  filters,
  options,
  onChange,
  onReset,
  metric,
  onMetric,
}: {
  query: QueryView<Overview>;
  filters: Filters;
  options: Options | null;
  onChange: (patch: Partial<Filters>) => void;
  onReset: () => void;
  metric: MetricKey;
  onMetric: (key: MetricKey) => void;
}) {
  const product = options?.products.find(
    (p) => filters.products.length === 1 && p.key === filters.products[0],
  );
  return (
    <section id="overview" className="product-section" aria-label="经营总览">
      <SectionHeading
        eyebrow="经营总览"
        title="全店指标与商品流量表现"
        extra={<Tag variant="filled">筛选仅影响经营总览</Tag>}
      />
      <Card size="small" className="filters-card">
        <FiltersBar
          scope="overview"
          filters={filters}
          options={options}
          onChange={onChange}
          onReset={onReset}
        />
      </Card>
      <div className="scope-summary">
        <div>
          <span className="muted">当前商品范围</span>
          <strong>
            {product?.name ??
              (filters.products.length
                ? `已选 ${filters.products.length} 件商品`
                : "筛选范围内全部商品")}
          </strong>
        </div>
        <Space wrap size={24}>
          <span>
            店铺 <b>{options?.shops.find((s) => s.value === filters.shop)?.label ?? "全部店铺"}</b>
          </span>
          <span>
            运营 <b>{filters.operator || "全部运营"}</b>
          </span>
          <span>
            品类 <b>{filters.category || "全部品类"}</b>
          </span>
          <span>
            等级 <b>{filters.level || "全部等级"}</b>
          </span>
        </Space>
      </div>
      <SectionState query={query}>
        {(data) => (
          <>
            <Radio.Group
              value={metric}
              onChange={(e) => onMetric(e.target.value as MetricKey)}
              className="metric-grid"
              aria-label="选择趋势指标"
            >
              {data.metrics.map((item) => (
                <Radio.Button key={item.key} value={item.key} className="metric-tile">
                  <div className="metric-label">
                    {item.label}
                    <InfoCircleOutlined />
                  </div>
                  <strong className="metric-value">{displayValue(item.value, item.format)}</strong>
                  <div className="metric-last">
                    去年同期 {displayValue(item.yearAgo, item.format)}
                  </div>
                  {item.missingReasons?.length ? (
                    <div className="metric-last">{item.missingReasons.join("；")}</div>
                  ) : null}
                  <div className="metric-changes">
                    <span className={isNegative(item.yoy) ? "negative" : "positive"}>
                      {item.yoy != null &&
                        (isNegative(item.yoy) ? <ArrowDownOutlined /> : <ArrowUpOutlined />)}{" "}
                      同比 {displayChange(item.yoy)}
                    </span>
                    <span className={isNegative(item.mom) ? "negative" : "positive"}>
                      环比 {displayChange(item.mom)}
                    </span>
                  </div>
                </Radio.Button>
              ))}
            </Radio.Group>
            <Card size="small" className="period-card">
              <div className="period-grid">
                <div>
                  <span className="product-eyebrow">近 7 / 14 / 30 日表现</span>
                  <h3>交易金额 · 周期表现</h3>
                  <span className="muted">环比为相邻等长周期对比</span>
                </div>
                {data.periods.map((period) => (
                  <div className="period-stat" key={period.days}>
                    <span className="muted">近 {period.days} 日</span>
                    {period.window && <small className="muted">{period.window}</small>}
                    {period.status && period.status !== "valid" && (
                      <Tag color="gold">{period.value === null ? "暂无可用值" : "已观察小计"}</Tag>
                    )}
                    <strong>{displayValue(period.value, "money")}</strong>
                    {period.coverage && (
                      <small className="muted">
                        本期已观察{period.coverage.observedDays}/{period.coverage.expectedDays}
                        日；未入库{period.coverage.notImportedDays}日，缺店铺记录
                        {period.coverage.missingRecordDays}日，缺商品记录
                        {period.coverage.missingProductCount}项
                      </small>
                    )}
                    {period.previousCoverage && (
                      <small className="muted">
                        前期已观察{period.previousCoverage.observedDays}/
                        {period.previousCoverage.expectedDays}日
                      </small>
                    )}
                    {!!period.missingReasons?.length && (
                      <small className="muted">{period.missingReasons.join("；")}</small>
                    )}
                    <span className={isNegative(period.change) ? "negative" : "positive"}>
                      较前{period.days}日 {displayChange(period.change)}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
            <Card
              size="small"
              title="商品流量与支付表现"
              extra={
                <Tooltip title="各节点并非严格同一用户群体；浏览量不是广告曝光，收藏不包含加购。">
                  <span className="muted">
                    <InfoCircleOutlined /> 口径说明
                  </span>
                </Tooltip>
              }
            >
              <div className="traffic-chain">
                {data.funnel.map((node, i) => (
                  <div className="traffic-node" key={node.key}>
                    <span>{node.label}</span>
                    <strong>{displayValue(node.value)}</strong>
                    <small>{node.note}</small>
                    {i < data.funnel.length - 1 && <RightOutlined className="traffic-arrow" />}
                  </div>
                ))}
              </div>
            </Card>
          </>
        )}
      </SectionState>
    </section>
  );
}
