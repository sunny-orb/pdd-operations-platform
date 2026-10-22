import { BulbOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { Card, Tag, Typography } from "antd";
import type { Diagnosis } from "../api/contracts";
import { SectionState, type QueryView } from "../components/SectionState";

export function DiagnosisSection({ query }: { query: QueryView<Diagnosis> }) {
  return (
    <section id="product-diagnosis" className="product-diagnosis-section" aria-label="经营诊断">
      <Card
        className="diagnosis-card"
        title={
          <span>
            <BulbOutlined /> 经营诊断
          </span>
        }
        extra={<Tag color="blue">当前筛选范围</Tag>}
      >
        <SectionState query={query}>
          {(data) => (
            <>
              <Tag variant="filled">
                {data.method === "ai" ? "AI诊断" : "规则诊断"}
                {query.data?.meta.isPreview ? " · 开发样例，未调用AI" : ""}
              </Tag>
              <h3>{data.headline}</h3>
              <div className="diagnosis-evidence">
                {data.evidence.map((evidence, i) => (
                  <div key={evidence}>
                    <span className="diagnosis-index">0{i + 1}</span>
                    <Typography.Paragraph>{evidence}</Typography.Paragraph>
                  </div>
                ))}
              </div>
              <div className="diagnosis-action">
                <span>
                  <CheckCircleOutlined /> 优先动作
                </span>
                <p>{data.action}</p>
              </div>
              <small className="muted">
                生成时间：{data.generatedAt.replace("T", " ").slice(0, 16)} · 不代表因果已证实
              </small>
            </>
          )}
        </SectionState>
      </Card>
    </section>
  );
}
