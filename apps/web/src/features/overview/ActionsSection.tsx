import { memo, useState } from "react";
import { Button, Drawer, Pagination, Table, Tag, Tooltip, type TableColumnsType } from "antd";
import { Link } from "react-router-dom";
import { formatMetric, sourceLabel, type ActionObject, type ObservationCoverage } from "./model";
import { SourceLinks } from "./SourceLinks";
import { OVERVIEW_PAGE_SIZE as PAGE_SIZE } from "./presentation";

function TextCell({ value }: { value: string }) {
  return (
    <Tooltip title={value} trigger={["hover", "focus", "click"]}>
      <span className="m6-action-cell" tabIndex={0}>
        {value}
      </span>
    </Tooltip>
  );
}

const columns: TableColumnsType<ActionObject> = [
  {
    title: "问题",
    key: "issue",
    width: "13%",
    render: (_, row) => (
      <div>
        <Tooltip
          title={row.members.map((item) => item.issue).join("；")}
          trigger={["hover", "focus", "click"]}
        >
          <Link className="m6-action-cell m6-action-issue" to={row.members[0].link}>
            {row.members[0].issue}
          </Link>
        </Tooltip>
        {row.members.length > 1 && <SourceLinks members={row.members} />}
      </div>
    ),
  },
  {
    title: "影响金额",
    key: "impact",
    width: "9%",
    render: (_, row) => (
      <Tooltip
        title={row.members
          .map(
            (item) =>
              `${sourceLabel(item.domain)}：${formatMetric(item.impact, "currency")} · ${item.impactKind}`,
          )
          .join("；")}
        trigger={["hover", "focus", "click"]}
      >
        <span className="m6-action-amount" tabIndex={0}>
          {row.members.length > 1 ? (
            <span className="m6-action-cell">
              {[
                ...new Set(
                  row.members.map(
                    (item) =>
                      `${sourceLabel(item.domain)} ${formatMetric(item.impact, "currency")}`,
                  ),
                ),
              ].join("；")}
            </span>
          ) : (
            <>
              <span>{formatMetric(row.members[0].impact, "currency")}</span>
              <small className="m6-table-note">{row.members[0].impactKind}</small>
            </>
          )}
        </span>
      </Tooltip>
    ),
  },
  {
    title: "原因假设",
    key: "hypothesis",
    width: "15%",
    render: (_, row) => (
      <TextCell value={[...new Set(row.members.map((item) => item.hypothesis))].join("；")} />
    ),
  },
  {
    title: "动作",
    key: "suggestion",
    width: "24%",
    render: (_, row) => <TextCell value={row.suggestions.join("；")} />,
  },
  { title: "负责人", key: "assignee", width: "8%", render: (_, row) => row.members[0].assignee },
  { title: "截止时间", key: "due", width: "7%", render: (_, row) => row.members[0].due },
  {
    title: "当前进度",
    key: "progress",
    width: "8%",
    render: (_, row) => <Tag>{row.members[0].progress}</Tag>,
  },
  {
    title: "验证指标",
    key: "verification",
    width: "16%",
    render: (_, row) => (
      <TextCell value={[...new Set(row.members.map((item) => item.verification))].join("；")} />
    ),
  },
];

/** 对完整来源建议做展示分页；不改建议内容、处理状态、定位或全量待办数。 */
export const ActionsSection = memo(function ActionsSection({
  actions,
  total,
  coverage,
}: {
  coverage: readonly ObservationCoverage[];
  actions: readonly ActionObject[];
  total: number | null;
}) {
  const failed = coverage.filter((item) => item.status === "failed");
  const retained = coverage.filter((item) => item.status === "retained");
  const loading = coverage.some((item) => item.status === "loading");
  const emptyText = failed.length
    ? "来源建议读取失败，当前清单不代表没有待处理事项"
    : loading
      ? "正在读取来源建议"
      : "尚未取得可核来源建议";
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = actions.find((item) => item.id === detailId);
  const [page, setPage] = useState(1);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(actions.length / PAGE_SIZE)));
  if (page !== currentPage) setPage(currentPage);
  const rows = actions.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  return (
    <section className="m6-section m6-actions-section" id="actions">
      <div className="m6-section-title">
        <div>
          <p className="m6-eyebrow">DAILY ACTIONS</p>
          <h2>本日经营动作</h2>
        </div>
      </div>
      <p className="m6-alert-note">
        {`当前清单 ${total ?? "—"} 条归并建议；`}
        核查后安排执行，未指定负责人及日期显示待分配。
        {failed.length > 0 &&
          ` ${failed.map((item) => sourceLabel(item.domain)).join("、")}来源建议读取失败。`}
        {retained.length > 0 &&
          ` ${retained.map((item) => sourceLabel(item.domain)).join("、")}建议保留上次可用结果。`}
      </p>
      <Table
        className="m6-action-table"
        columns={columns.map((column) =>
          column.key === "suggestion"
            ? {
                ...column,
                render: (_: unknown, row: ActionObject) => (
                  <>
                    <TextCell value={row.suggestions.join("；")} />
                    {row.members.some(
                      (member) => member.domain === "products" || member.domain === "promotion",
                    ) && (
                      <Button type="link" size="small" onClick={() => setDetailId(row.id)}>
                        查看详情
                      </Button>
                    )}
                  </>
                ),
              }
            : column,
        )}
        dataSource={rows}
        rowKey="id"
        tableLayout="fixed"
        pagination={false}
        scroll={{ x: 1000 }}
        locale={{ emptyText }}
      />
      <div className="m6-action-pagination">
        {actions.length > PAGE_SIZE && (
          <nav aria-label="动作分页">
            <Pagination
              current={currentPage}
              pageSize={PAGE_SIZE}
              total={actions.length}
              onChange={setPage}
              showSizeChanger={false}
              showLessItems
              responsive
              showTotal={(total) => `共 ${total} 条`}
            />
          </nav>
        )}
      </div>
      <Drawer
        title="行动依据与安排"
        open={!!detail}
        onClose={() => setDetailId(null)}
        size="large"
        className="m6-action-detail"
      >
        {detail?.members.map((item, index) => (
          <article key={index}>
            <h3>{item.issue}</h3>
            <dl>
              <dt>来源与周期</dt>
              <dd>
                {sourceLabel(item.domain)} · {item.context?.scope ?? "沿来源记录"}
              </dd>
              <dt>触发依据</dt>
              <dd>
                {item.context?.sourceRules?.join("；") ??
                  `来源 ${item.context?.sourcePriority ?? "观察"}，中心等级未确认`}
                <small>规则版本：{item.context?.ruleVersion ?? "来源未提供"}</small>
              </dd>
              <dt>证据</dt>
              <dd>
                {item.context?.detail ?? item.hypothesis}
                <br />
                {formatMetric(item.impact, "currency")} · {item.impactKind}
              </dd>
              <dt>原因假设（待验证）</dt>
              <dd>{item.hypothesis}</dd>
              <dt>建议动作</dt>
              <dd>{item.suggestion}</dd>
              <dt>目标与验证</dt>
              <dd>{item.verification}</dd>
              <dt>负责人 / 截止日 / 复盘日</dt>
              <dd>
                {item.assignee} / {item.due} / {item.reviewDate ?? "待分配"}
              </dd>
              <dt>执行状态 / 复盘结果</dt>
              <dd>{item.progress} / 尚未登记</dd>
            </dl>
            <Link to={item.link}>查看来源证据 →</Link>
          </article>
        ))}
      </Drawer>
    </section>
  );
});
