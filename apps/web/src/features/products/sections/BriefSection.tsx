import { Button, Card, Empty, Space, Tag, Typography } from "antd";
import { ArrowRightOutlined, RiseOutlined } from "@ant-design/icons";
import { useProductQuery } from "../api/queries";
import type { Brief, Filters, Mode, Product, RankItem, Scenario } from "../api/contracts";
import { SectionState } from "../components/SectionState";
import { SectionHeading } from "../components/SectionHeading";
import { displayChange, displayValue, isNegative } from "../components/display";

function Ranking({
  items,
  onProduct,
  type,
}: {
  items: RankItem[];
  onProduct: (product: Product) => void;
  type: "hot" | "risk" | "new";
}) {
  if (!items.length)
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此范围暂无结果" />;
  return (
    <div className="rank-list">
      {items.map((item, index) => (
        <div className="rank-item" key={`${item.product?.key ?? item.name}-${index}`}>
          <span className={`rank-number rank-${type}`}>{String(index + 1).padStart(2, "0")}</span>
          <div className="rank-content">
            {item.product ? (
              <Button type="link" className="rank-product" onClick={() => onProduct(item.product!)}>
                {item.name}
              </Button>
            ) : (
              <strong>{item.name}</strong>
            )}
            <div className="rank-caption">{item.note}</div>
            <div className="rank-values">
              <span>
                {type === "risk" ? "累计交易" : "交易"} <b>{displayValue(item.value, "money")}</b>
              </span>
              {type !== "risk" && (
                <span className={isNegative(item.change) ? "negative" : "positive"}>
                  较前7天 {displayChange(item.change)}
                </span>
              )}
              {type === "new" && <span>转化 {displayValue(item.conversion, "percent")}</span>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
export function BriefSection({
  mode,
  scenario,
  filters,
  versionToken,
  onProduct,
}: {
  mode: Mode;
  scenario: Scenario;
  filters: Filters;
  versionToken: number;
  onProduct: (product: Product) => void;
}) {
  const query = useProductQuery<Brief>("brief", "overview", filters, {
    mode,
    scenario,
    versionToken,
  });
  return (
    <section id="product-brief" className="product-section" aria-label="经营核心结论">
      <SectionHeading
        eyebrow="商品经营分析"
        title="经营核心结论"
        extra={
          <Tag color="blue" icon={<RiseOutlined />}>
            聚焦增长、风险与机会
          </Tag>
        }
      />
      <SectionState query={query}>
        {(data) => (
          <>
            <div className="brief-grid">
              <Card size="small" title="近7天热卖品类Top3" extra={<Tag variant="filled">品类</Tag>}>
                <div className="muted rank-window">
                  {data.window.from} 至 {data.window.to}
                </div>
                <Ranking items={data.categories} onProduct={onProduct} type="hot" />
              </Card>
              <Card size="small" title="近7天热卖商品Top3" extra={<Tag variant="filled">商品</Tag>}>
                <div className="muted rank-window">
                  {data.window.from} 至 {data.window.to}
                </div>
                <Ranking items={data.products} onProduct={onProduct} type="hot" />
              </Card>
              <Card
                size="small"
                title="异常下滑Top3"
                extra={
                  <Tag color="red" variant="filled">
                    关注
                  </Tag>
                }
              >
                <Ranking items={data.declining} onProduct={onProduct} type="risk" />
              </Card>
              <Card
                size="small"
                title="新品起势Top3"
                extra={
                  <Tag color="green" variant="filled">
                    机会
                  </Tag>
                }
              >
                <Ranking items={data.rising} onProduct={onProduct} type="new" />
              </Card>
            </div>
            {data.unassignedCategory && (
              <p className="muted">
                未归属品类交易：{displayValue(data.unassignedCategory.value, "money")}
                ；保留在总额中，不参与品类排名。
              </p>
            )}
            <div className="brief-actions">
              <Tag color="blue" variant="filled">
                建议动作
              </Tag>
              <Space orientation="vertical" size={5}>
                {data.actions.map((action) => (
                  <Typography.Text key={action}>
                    <ArrowRightOutlined /> {action}
                  </Typography.Text>
                ))}
              </Space>
              <Tag variant="filled">
                {query.data?.meta.isPreview
                  ? "样例规则摘要 · 未调用AI"
                  : "预留位置 · 建议动作尚未接入AI"}
              </Tag>
            </div>
          </>
        )}
      </SectionState>
    </section>
  );
}
