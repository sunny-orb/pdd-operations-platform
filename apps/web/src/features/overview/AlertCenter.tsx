import { memo, useState } from "react";
import { Button, Empty, Pagination, Tag, Tooltip } from "antd";
import { Link } from "react-router-dom";
import { SourceLinks } from "./SourceLinks";
import { formatMetric, sourceLabel, type AlertObject } from "./model";
import { OVERVIEW_PAGE_SIZE as PAGE_SIZE } from "./presentation";

const levels = ["全部", "P0", "P1", "P2", "未评级"];

/** 来源优先级只筛本区对象，分页不影响全量统计；不创建中心正式评级。 */
export const AlertCenter = memo(function AlertCenter({
  observations,
  available,
  preview,
  showAll,
  onShowAllChange,
}: {
  observations: readonly AlertObject[];
  available: boolean;
  preview: readonly AlertObject[];
  showAll: boolean;
  onShowAllChange: (value: boolean) => void;
}) {
  const [level, setLevel] = useState("全部");
  const [page, setPage] = useState(1);
  const visible = !showAll
    ? preview
    : level === "全部"
      ? observations
      : observations.filter((item) =>
          level === "未评级"
            ? item.sourcePriority === null && item.members[0].domain !== "aftersales"
            : item.sourcePriority === level,
        );
  const currentPage = Math.min(page, Math.max(1, Math.ceil(visible.length / PAGE_SIZE)));
  // 同步保存缩页后的有效页码，后续数据增加时不回跳到已过期的请求页。
  if (page !== currentPage) setPage(currentPage);
  const items = visible.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  return (
    <section className="m6-section m6-alert-section" id="alerts">
      <div className="m6-section-title">
        <div>
          <p className="m6-eyebrow">ALERT CENTER</p>
          <h2>经营预警中心</h2>
        </div>
        <div className="m6-list-tools">
          {showAll && (
            <div className="m6-level-tabs" role="group" aria-label="来源优先级">
              {levels.map((value) => (
                <Button
                  key={value}
                  autoInsertSpace={false}
                  type={level === value ? "primary" : "default"}
                  aria-pressed={level === value}
                  onClick={() => {
                    setLevel(value);
                    setPage(1);
                  }}
                >
                  {value}
                </Button>
              ))}
            </div>
          )}
          <Button
            onClick={() => {
              setLevel("全部");
              setPage(1);
              onShowAllChange(!showAll);
            }}
            aria-expanded={showAll}
          >
            {showAll ? "返回首页关注" : "查看全部预警"}
          </Button>
        </div>
      </div>
      <p className="m6-alert-note">
        {showAll
          ? `已取得 ${available || observations.length ? observations.length : "—"} 条对象提示；按来源优先级筛选，未映射记录单列。`
          : "首页关注：按来源顺序最多展示六项，完整清单可按需查看。"}
        <a href="#definitions">查看口径 →</a>
      </p>
      <div className="m6-alert-list" role="list" aria-label="预警列表">
        {items.length ? (
          items.map((group) => {
            const item = group.members[0];
            const merged = group.members.length > 1;
            const priority = group.sourcePriority;
            return (
              <article className="m6-alert-row" key={group.id} role="listitem">
                <div className="m6-priority">
                  <Tag color={priority === "P0" ? "red" : priority === "P1" ? "gold" : "blue"}>
                    {item.domain === "aftersales" ? "观察" : (priority ?? "未评级")}
                  </Tag>
                </div>
                <Tooltip
                  trigger={["hover", "focus", "click"]}
                  title={
                    <>
                      {group.members.map((member, index) => (
                        <div className="m6-evidence-item" key={index}>
                          <b>
                            {sourceLabel(member.domain)} · {member.sourcePriority ?? "未评级"}
                          </b>
                          <div>{member.title}</div>
                          <div>{member.detail}</div>
                          <div>对应筛选：{member.scope}</div>
                          <div>
                            {formatMetric(member.impact, "currency")} · {member.impactKind}
                          </div>
                        </div>
                      ))}
                    </>
                  }
                >
                  <div className="m6-alert-description" tabIndex={0}>
                    <h3>
                      {item.title}
                      {merged ? ` · 共${group.members.length}条来源提示` : ""}
                    </h3>
                    <p>{item.detail}</p>
                    <small>
                      {item.domain !== "aftersales" && (
                        <>
                          {group.members
                            .map(
                              (member) =>
                                `${sourceLabel(member.domain)} · ${member.sourcePriority ?? "未评级"}`,
                            )
                            .join(" / ")}{" "}
                          ·{" "}
                        </>
                      )}
                      对应筛选：{item.scope}
                    </small>
                  </div>
                </Tooltip>
                <div className="m6-impact">
                  <span>{merged ? "来源金额（不合计）" : "影响金额 / 样本"}</span>
                  {merged ? (
                    group.members.slice(0, 2).map((member, index) => (
                      <b key={index} title={`${member.scope} · ${member.impactKind}`}>
                        {sourceLabel(member.domain)} {formatMetric(member.impact, "currency")}
                      </b>
                    ))
                  ) : (
                    <>
                      <b title={formatMetric(item.impact, "currency")}>
                        {formatMetric(item.impact, "currency")}
                      </b>
                      <small title={item.impactKind}>{item.impactKind}</small>
                    </>
                  )}
                  {group.members.length > 2 && (
                    <small>共 {group.members.length} 条金额，点击描述查看</small>
                  )}
                </div>
                {merged ? (
                  <SourceLinks members={group.members} />
                ) : (
                  <Link to={item.link}>定位处理 →</Link>
                )}
              </article>
            );
          })
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              level === "全部"
                ? "暂无已取得的观察提示；来源未齐备不代表没有风险"
                : "当前已接范围暂无该来源优先级项"
            }
          />
        )}
      </div>
      <div className="m6-alert-pagination">
        {showAll && visible.length > PAGE_SIZE && (
          <nav aria-label="预警分页">
            <Pagination
              current={currentPage}
              pageSize={PAGE_SIZE}
              total={visible.length}
              onChange={setPage}
              showSizeChanger={false}
              showLessItems
              responsive
              showTotal={(total) => `共 ${total} 条`}
            />
          </nav>
        )}
      </div>
    </section>
  );
});
