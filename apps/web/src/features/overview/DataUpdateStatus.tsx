import { useState } from "react";
import { DatabaseOutlined, ReloadOutlined, SyncOutlined } from "@ant-design/icons";
import { Button, Empty, Modal, Table, Tabs, Tag } from "antd";
import type { DomainState } from "./data";
import { domains, localTime } from "./model";
import { updateRows, updateState } from "./updateStatus";
import "./data-update-status.css";

/** 状态骨架复用本地元信息；上游检查、同步命令未接入时保持禁用。 */
export function DataUpdateStatus({ states }: { states: readonly DomainState[] }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("sources");
  const rows = updateRows(states);
  const refreshing = states.some((state) => state.refreshing);
  const missing = states.filter((state) => state.id !== "promotion" && !state.meta?.updates);
  return (
    <>
      <Button
        className="m6-update-entry"
        icon={<DatabaseOutlined aria-hidden="true" />}
        onClick={() => setOpen(true)}
      >
        数据更新状态
      </Button>
      <Modal
        title="数据更新状态"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={1200}
        className="m6-update-modal"
      >
        <div className="m6-update-intro">
          <span>初版 · 查看已接入的本地状态，上游检查与同步操作待接入。</span>
          <Button
            icon={<ReloadOutlined aria-hidden="true" />}
            loading={refreshing}
            onClick={() => states.forEach((state) => (state.refreshStatus ?? state.refresh)())}
          >
            刷新本地状态
          </Button>
        </div>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: "sources",
              label: "来源数据",
              children: (
                <>
                  <div className="m6-update-toolbar">
                    <div>
                      <h3>
                        来源数据 <span>{rows.length} 项</span>
                      </h3>
                      <p>显示当前已接收的数据；资料和目标按修订更新。</p>
                    </div>
                    <div className="m6-update-actions">
                      <Button disabled>检查上游更新</Button>
                      <Button type="primary" icon={<SyncOutlined aria-hidden="true" />} disabled>
                        全部同步
                      </Button>
                    </div>
                  </div>
                  {missing.length > 0 && (
                    <p className="m6-update-warning" role="status">
                      {missing.map((s) => domains.find((d) => d.id === s.id)?.label).join("、")}：
                      {missing.some((s) => s.error) ? "状态未能读取" : "状态尚未载入"}。
                    </p>
                  )}
                  <Table
                    className="m6-update-table"
                    rowKey="key"
                    dataSource={rows}
                    pagination={false}
                    scroll={{ x: 980, y: "min(440px, 42vh)" }}
                    size="middle"
                    locale={{ emptyText: "暂无来源状态" }}
                    columns={[
                      {
                        title: "数据名称",
                        dataIndex: "name",
                        width: 220,
                        render: (_, row) => (
                          <div className="m6-update-name">
                            {row.name}
                            {row.kind && <small>按修订更新</small>}
                          </div>
                        ),
                      },
                      {
                        title: "上游最新日期",
                        width: 140,
                        render: () => <span className="m6-update-muted">尚未检查</span>,
                      },
                      {
                        title: "看板数据日期",
                        width: 160,
                        render: (_, row) => (
                          <>
                            {(row.kind === "target" ? row.date?.slice(0, 7) : row.date) ?? "—"}
                            {row.kind === "target" && row.date && (
                              <small className="m6-update-subtext">目标月份截至</small>
                            )}
                          </>
                        ),
                      },
                      {
                        title: "最近同步 / 接收",
                        width: 210,
                        render: (_, row) => (
                          <>
                            {row.success || row.received
                              ? localTime(row.success ?? row.received)
                              : "—"}
                            {(row.success || row.received) && (
                              <small className="m6-update-subtext">
                                {row.success ? "同步成功" : "数据接收"}
                              </small>
                            )}
                          </>
                        ),
                      },
                      {
                        title: "更新状态",
                        width: 150,
                        render: (_, row) => {
                          const state = updateState(row);
                          return <Tag color={state.color}>{state.label}</Tag>;
                        },
                      },
                    ]}
                    expandable={{
                      columnTitle: "明细",
                      columnWidth: 64,
                      expandedRowRender: (row) => (
                        <div className="m6-update-detail">
                          <dl>
                            <div>
                              <dt>来源标识</dt>
                              <dd>{row.id}</dd>
                            </div>
                            {row.locator && (
                              <div>
                                <dt>来源位置</dt>
                                <dd>{row.locator}</dd>
                              </div>
                            )}
                            <div>
                              <dt>最近同步尝试</dt>
                              <dd>{row.attempt ? localTime(row.attempt) : "未提供"}</dd>
                            </div>
                            <div>
                              <dt>上游最近检查</dt>
                              <dd>尚未检查</dd>
                            </div>
                            {row.sourceUpdated && (
                              <div>
                                <dt>已接收的来源更新时间</dt>
                                <dd>{row.sourceUpdated}</dd>
                              </div>
                            )}
                          </dl>
                          {row.queryError && (
                            <p role="alert">状态读取失败，当前保留上次读取的记录。</p>
                          )}
                          <Button size="small" disabled>
                            重试同步
                          </Button>
                        </div>
                      ),
                    }}
                  />
                  <p className="m6-update-footnote">
                    当前已列出店铺销售、商品与售后接口提供的来源；天气及其他来源状态待接入。刷新只读取本地状态。
                  </p>
                </>
              ),
            },
            {
              key: "analysis",
              label: "评分与分析",
              children: (
                <div className="m6-update-analysis">
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="评分与分析更新状态待接入"
                  />
                  <p>后续在这里查看商品评分、售后分析的计算时间、输入数据日期与执行结果。</p>
                  <Button disabled>重新计算</Button>
                </div>
              ),
            },
          ]}
        />
      </Modal>
    </>
  );
}
