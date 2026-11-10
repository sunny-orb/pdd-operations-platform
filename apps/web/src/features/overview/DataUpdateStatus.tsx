import { memo, useId, useState } from "react";
import {
  DatabaseOutlined,
  CopyOutlined,
  ExportOutlined,
  MinusSquareOutlined,
  PlusSquareOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Alert, Badge, Button, Modal, Radio, Table, Tabs, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { localTime } from "./model";
import {
  hasRunningUpdate,
  pageNames,
  platformLabels,
  upstreamLabel,
  useDataUpdateStatus,
  type DatasetStatus,
  type SourceStatus,
} from "./sourceStatusApi";
import { dataUpdateAlerts, sourceHasFailure } from "./dataUpdateAlerts";
import "./data-update-status.css";

const systems = { warehouse: "数仓", feishu: "飞书", nmc: "气象台" };
const time = (value: string | null | undefined) => (value ? localTime(value) : "—");
const date = (value: string | null | undefined) =>
  value?.includes("T") ? localTime(value) : (value ?? "—");
function latest(values: (string | null)[]) {
  return values
    .filter((v): v is string => Boolean(v))
    .sort()
    .at(-1);
}
function DatasetDates({ row }: { row: SourceStatus }) {
  if (row.id === "jst_master") return <span>随售后数据接收</span>;
  if (row.kind === "reference") return <span>按资料修订</span>;
  return (
    <>
      {row.datasets.map((d) => (
        <div key={d.code}>
          {row.datasets.length > 1 && <small className="m6-update-subtext">{d.name}</small>}
          {row.kind === "target" ? (d.date?.slice(0, 7) ?? "—") : date(d.date)}
          {row.kind === "target" && (
            <small className="m6-update-subtext">目标月份截至，非更新日</small>
          )}
        </div>
      ))}
    </>
  );
}
function DatasetDetail({ item }: { item: DatasetStatus }) {
  const status = attemptLabel(item.attempt_status);
  return (
    <div className="m6-update-dataset">
      <strong>{item.name}</strong>
      <span>数据日期：{date(item.date)}</span>
      <span>最近成功：{time(item.last_success_at ?? item.received_at)}</span>
      <span>最近尝试：{time(item.last_attempt_at)}</span>
      <span>完成时间：{time(item.last_finished_at)}</span>
      <span>尝试结果：{status}</span>
      {["failed", "abandoned", "cancelled"].includes(item.attempt_status ?? "") && (
        <span>
          {item.error_code
            ? `失败记录：${item.error_code}；具体原因待核实。`
            : "失败原因尚未确认。"}
          {item.version_id
            ? "已有成功数据仍可使用，请以数据日期为准。"
            : "当前没有可用的成功数据。"}
        </span>
      )}
    </div>
  );
}

function attemptLabel(status: string | null) {
  return (
    (
      {
        succeeded: "成功",
        failed: "失败",
        abandoned: "已中断",
        cancelled: "已取消",
        running: "进行中",
        queued: "等待执行",
      } as Record<string, string>
    )[status ?? ""] ?? "尚无记录"
  );
}

function UpstreamUpdate({ row }: { row: SourceStatus }) {
  if (row.system === "warehouse") {
    return row.upstream.web_updated_at ? (
      <div className="m6-update-cell-stack">
        <span className="m6-update-time">{row.upstream.web_updated_at}</span>
        <small className="m6-update-subtext">
          {row.upstream.observation.basis === "warehouse_web_page"
            ? "网页自动读取"
            : row.upstream.observation.basis === "warehouse_web_metadata"
              ? "数仓更新时间"
              : "网页记录"}
        </small>
        <small className="m6-update-subtext">核对：{time(row.upstream.web_checked_at)}</small>
      </div>
    ) : (
      <span className="m6-update-muted">尚无网页更新记录</span>
    );
  }
  const observation = row.upstream.observation;
  if (row.kind === "publication") {
    return (
      <>
        <span className="m6-update-time">
          {date(typeof observation.date === "string" ? observation.date : null)}
        </span>
        <small className="m6-update-subtext">官方发布时间</small>
      </>
    );
  }
  return (
    <>
      <span className="m6-update-muted">未提供更新时间</span>
      <small className="m6-update-subtext">
        {typeof observation.revision === "string"
          ? `当前修订 ${observation.revision}`
          : "尚无修订记录"}
      </small>
    </>
  );
}

function SyncStatus({ row }: { row: SourceStatus }) {
  const state = platformLabels[row.platform_state];
  // 来源失败可能只涉及一个用途，摘要时间必须绑定该失败，不能取另一成功用途。
  const relevant = row.datasets.filter((d) =>
    row.platform_state === "failed"
      ? ["failed", "abandoned", "cancelled"].includes(d.attempt_status ?? "")
      : row.platform_state === "syncing"
        ? d.attempt_status === "running"
        : true,
  );
  const attempt = latest(relevant.map((d) => d.last_attempt_at));
  const groupFailure = row.platform_state === "failed" && relevant.length === 0;
  return (
    <div className="m6-update-cell-stack">
      <Tag color={state.color}>{state.text}</Tag>
      <small className="m6-update-subtext">
        {groupFailure ? "失败记录：" : "本次尝试："}
        {time(groupFailure ? row.upstream.checked_at : attempt)}
      </small>
      {row.datasets.map((d) => (
        <small key={d.code} className="m6-update-subtext">
          {row.datasets.length > 1 ? `${d.name}成功：` : "最近成功："}
          {time(d.last_success_at ?? d.received_at)}
        </small>
      ))}
    </div>
  );
}

function SourceDetail({ row }: { row: SourceStatus }) {
  const [copyState, setCopyState] = useState("");
  const observation = row.upstream.observation;
  return (
    <div className="m6-update-detail">
      {sourceHasFailure(row) && (
        <p>
          异常影响：{row.pages.map((page) => pageNames[page]).join("、")}。
          {row.upstream.state === "failed" ? "上游检查未成功；" : "平台接收未成功；"}
          请以各用途的数据日期和最近成功时间确认当前可用范围。
          {!row.upstream.error_code &&
            !row.datasets.some((item) => item.error_code) &&
            "具体失败原因尚未确认。"}
        </p>
      )}
      <dl>
        <div>
          <dt>上游原名</dt>
          <dd>{row.source_title}</dd>
        </div>
        <div>
          <dt>完整来源</dt>
          <dd className="m6-update-locator">
            <code>{row.locator}</code>
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined aria-hidden="true" />}
              aria-label={`复制完整来源：${row.name}`}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(row.locator);
                  setCopyState("已复制");
                } catch {
                  setCopyState("复制失败，请选择表名复制");
                }
              }}
            >
              复制
            </Button>
            <span role="status">{copyState}</span>
          </dd>
        </div>
        <div>
          <dt>网页入口</dt>
          <dd>
            <a href={row.source_url} target="_blank" rel="noopener noreferrer">
              打开上游来源 <ExportOutlined aria-hidden="true" />
            </a>
          </dd>
        </div>
        <div>
          <dt>使用页面</dt>
          <dd>{row.pages.map((p) => pageNames[p]).join("、")}</dd>
        </div>
        <div>
          <dt>最近成功检查</dt>
          <dd>{time(row.upstream.observed_at)}</dd>
        </div>
        <div>
          <dt>检查耗时</dt>
          <dd>{row.upstream.duration_ms === null ? "—" : `${row.upstream.duration_ms} ms`}</dd>
        </div>
        <div>
          <dt>观察到的数据</dt>
          <dd>
            {typeof observation.date === "string" ? date(observation.date) : "按来源修订"}
            {typeof observation.revision === "string" && (
              <span className="m6-update-revision">修订：{observation.revision}</span>
            )}
          </dd>
        </div>
        {typeof observation.source_updated === "string" && (
          <div>
            <dt>记录时间线索</dt>
            <dd>{date(observation.source_updated)}（来源记录字段，非网页更新时间）</dd>
          </div>
        )}
        <div>
          <dt>检查依据</dt>
          <dd>
            {observation.basis === "warehouse_web_metadata" ||
            observation.basis === "warehouse_web_page"
              ? "数仓表更新时间；16:10补查获准范围内的历史修订"
              : observation.basis === "table_revision"
                ? "飞书表修订号"
                : observation.basis === "publication_content"
                  ? "官方发布时间及正文"
                  : observation.basis === "profile_projection"
                    ? "已授权资料字段内容"
                    : observation.basis === "snapshot_date"
                      ? "货品资料快照日期"
                      : "近7日记录数、业务日期及来源时间；历史修订由每日全量对账补查"}
          </dd>
        </div>
        <div>
          <dt>时间说明</dt>
          <dd>
            更新时间保留上游原文，并注明实际读取时刻。业务数据日期表示已接收范围，不能单凭日期认定整批已齐。
          </dd>
        </div>
        {row.upstream.error_code && (
          <div>
            <dt>本次结果</dt>
            <dd>
              {row.upstream.error_code === "SOURCE_CHANGED_DURING_SYNC"
                ? "同步期间来源再次变化，等待下一轮接收。"
                : row.upstream.error_code === "SYNC_FAILED"
                  ? "本轮接收未成功，保留最近可用数据。"
                  : row.upstream.error_code === "WAREHOUSE_WEB_LOGIN_REQUIRED"
                    ? "数仓网页登录已失效，保留上次更新时间和可用数据。重新登录后按计划继续检查。"
                    : "本轮上游检查失败，保留上次观测。"}
            </dd>
          </div>
        )}
      </dl>
      <div className="m6-update-platform-details">
        <h4>平台接收明细</h4>
        {row.datasets.map((item) => (
          <DatasetDetail key={item.code} item={item} />
        ))}
      </div>
    </div>
  );
}

const columns: ColumnsType<SourceStatus> = [
  {
    title: "用途 / 上游来源表",
    width: 300,
    render: (_, row) => (
      <div className="m6-update-name">
        {sourceHasFailure(row) && (
          <span
            className="m6-update-row-dot"
            role="img"
            aria-label={`来源异常：${row.name}`}
            title="此来源更新异常，展开明细查看"
          />
        )}
        <a
          className="m6-update-source-link"
          href={row.source_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`打开来源：${row.name}`}
        >
          {row.name} <ExportOutlined aria-hidden="true" />
        </a>
        <Tooltip title={row.table_name} trigger={["hover", "focus"]}>
          <span
            tabIndex={0}
            className={`m6-update-table-name${row.system === "warehouse" ? " is-physical" : ""}`}
          >
            {row.table_name}
          </span>
        </Tooltip>
        <small>
          {systems[row.system]} · {row.pages.map((p) => pageNames[p]).join("、")}
        </small>
      </div>
    ),
  },
  {
    title: "上游最近更新",
    width: 210,
    render: (_, row) => <UpstreamUpdate row={row} />,
  },
  {
    title: "最近检查",
    width: 200,
    render: (_, row) => {
      const label = upstreamLabel(row);
      return (
        <div className="m6-update-cell-stack">
          <span className="m6-update-time">{time(row.upstream.checked_at)}</span>
          <Tag color={label.color}>{label.text}</Tag>
          {row.upstream.next_check_at && (
            <small className="m6-update-subtext">下次：{time(row.upstream.next_check_at)}</small>
          )}
          {row.upstream.stale && row.upstream.state === "failed" && (
            <small className="m6-update-subtext">检查已过期</small>
          )}
          {row.upstream.state === "failed" && (
            <small className="m6-update-subtext">上次成功：{time(row.upstream.observed_at)}</small>
          )}
        </div>
      );
    },
  },
  { title: "平台数据日期", width: 170, render: (_, row) => <DatasetDates row={row} /> },
  {
    title: "平台同步",
    width: 245,
    render: (_, row) => <SyncStatus row={row} />,
  },
];

function StatusContent({ query }: { query: ReturnType<typeof useDataUpdateStatus> }) {
  const [page, setPage] = useState("all");
  const [activeTab, setActiveTab] = useState<string>();
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [sourceKeys, setSourceKeys] = useState<React.Key[]>([]);
  const [processingKeys, setProcessingKeys] = useState<React.Key[]>([]);
  const data = query.data?.data;
  const alerts = dataUpdateAlerts(data);
  const rows = (data?.sources ?? []).filter(
    (row) => (page === "all" || row.pages.includes(page)) && (!onlyFailed || sourceHasFailure(row)),
  );
  const processingRows = (data?.processing ?? []).filter(
    (item) => !onlyFailed || item.state === "failed",
  );
  const locateFailures = (tab: "sources" | "processing") => {
    setActiveTab(tab);
    setPage("all");
    setOnlyFailed(true);
    if (tab === "sources") setSourceKeys(alerts.sources.map((row) => row.id));
    else setProcessingKeys(alerts.processing.map((item) => item.code));
  };
  return (
    <>
      <div className="m6-update-intro">
        <span>
          {!data
            ? "正在读取数据更新状态…"
            : data.monitor.source_schedule_enabled
              ? "按计划读取数仓网页更新时间，有变化或接收失败时同步对应来源。"
              : data.monitor.enabled
                ? `后台自动检查并同步数据；当前每 ${data.monitor.interval_seconds / 60} 分钟检查一次。`
                : "自动检查尚未启用。"}
          {data?.monitor.source_schedule_enabled && (
            <small className="m6-update-subtext">
              数仓 {data.monitor.warehouse_times?.join(" / ")} · 飞书{" "}
              {data.monitor.feishu_times?.join(" / ")}（北京时间）
              <br />
              16:10 数仓对账；聚水潭、天气沿用原计划。
            </small>
          )}
          <small className="m6-update-subtext">
            {query.isError
              ? "状态读取失败，自动跟踪已暂停"
              : hasRunningUpdate(data)
                ? "任务运行中，每15秒跟踪状态"
                : "打开时读取状态，可手动刷新"}{" "}
            · 更新于 {time(query.data?.meta.generated_at)}
          </small>
        </span>
        <Button
          icon={<ReloadOutlined aria-hidden="true" />}
          loading={query.isFetching}
          onClick={() => void query.refetch()}
        >
          刷新状态
        </Button>
      </div>
      {query.isError && (
        <Alert
          type="warning"
          showIcon
          title="状态读取失败"
          description={data ? "保留上次状态，请稍后刷新。" : "暂时无法读取更新状态，请稍后刷新。"}
        />
      )}
      {(alerts.failed || onlyFailed) && (
        <div className="m6-update-alert-summary">
          <span>共 {alerts.sources.length + alerts.processing.length} 项异常</span>
          {alerts.sources.length > 0 && (
            <Button type="link" onClick={() => locateFailures("sources")}>
              查看{alerts.sources.length}项来源异常
            </Button>
          )}
          {alerts.processing.length > 0 && (
            <Button type="link" onClick={() => locateFailures("processing")}>
              查看{alerts.processing.length}项处理异常
            </Button>
          )}
          {onlyFailed && (
            <>
              <span>仅显示异常</span>
              <Button type="link" onClick={() => setOnlyFailed(false)}>
                显示全部
              </Button>
            </>
          )}
        </div>
      )}
      <Tabs
        activeKey={
          activeTab ??
          (alerts.processing.length && !alerts.sources.length ? "processing" : "sources")
        }
        onChange={setActiveTab}
        className="m6-update-tabs"
        items={[
          {
            key: "sources",
            label: "来源数据",
            forceRender: true,
            children: (
              <>
                <div className="m6-update-toolbar">
                  <h3>
                    上游来源{" "}
                    <span>
                      {rows.length} 项{page !== "all" && ` / 共 ${data?.sources.length ?? 0} 项`}
                    </span>
                  </h3>
                  <Radio.Group
                    value={page}
                    onChange={(event) => setPage(event.target.value)}
                    optionType="button"
                    buttonStyle="solid"
                    options={[
                      { label: "全部看板", value: "all" },
                      ...Object.entries(pageNames).map(([value, label]) => ({ value, label })),
                    ]}
                  />
                </div>
                <Table
                  className="m6-update-table"
                  rowKey="id"
                  dataSource={rows}
                  columns={columns}
                  loading={query.isPending}
                  pagination={false}
                  size="middle"
                  scroll={{ x: 1171, y: "var(--m6-update-body-height)" }}
                  locale={{ emptyText: onlyFailed ? "当前没有异常来源" : "暂无来源状态" }}
                  expandable={{
                    expandedRowKeys: sourceKeys,
                    onExpandedRowsChange: (keys) => setSourceKeys([...keys]),
                    columnTitle: "明细",
                    columnWidth: 46,
                    expandIcon: ({ expanded, onExpand, record }) => (
                      <Button
                        type="text"
                        size="small"
                        aria-label={expanded ? "收起来源详情" : "展开来源详情"}
                        aria-expanded={expanded}
                        icon={
                          expanded ? (
                            <MinusSquareOutlined aria-hidden="true" />
                          ) : (
                            <PlusSquareOutlined aria-hidden="true" />
                          )
                        }
                        onClick={(event) => onExpand(record, event)}
                      />
                    ),
                    expandedRowRender: (row) => <SourceDetail row={row} />,
                  }}
                />
                <p className="m6-update-footnote">
                  每个实际来源一行。网页记录注明核对时刻；刷新状态只读取平台。同步失败时保留最近成功的数据。
                </p>
              </>
            ),
          },
          {
            key: "processing",
            label: "平台处理",
            forceRender: true,
            children: (
              <Table
                className="m6-update-table"
                rowKey="code"
                dataSource={processingRows}
                locale={{ emptyText: onlyFailed ? "当前没有处理异常" : "暂无处理状态" }}
                expandable={{
                  columnTitle: "明细",
                  columnWidth: 46,
                  expandedRowKeys: processingKeys,
                  onExpandedRowsChange: (keys) => setProcessingKeys([...keys]),
                  expandedRowRender: (item) => (
                    <div className="m6-update-detail">
                      <p>
                        影响页面：{item.pages.map((page) => pageNames[page]).join("、") || "未提供"}
                      </p>
                      {item.state === "failed" && (
                        <p>
                          最近计算未成功；
                          {item.error_code
                            ? `失败记录：${item.error_code}，具体原因待核实。`
                            : "失败原因尚未确认。"}
                          {item.version_id
                            ? "已有结果仍保留，请以生成时间和输入日期为准。"
                            : "当前没有可用的成功结果。"}
                        </p>
                      )}
                      <DatasetDetail item={item} />
                    </div>
                  ),
                }}
                pagination={false}
                size="middle"
                scroll={{ x: 700, y: "var(--m6-update-body-height)" }}
                columns={[
                  {
                    title: "处理项目",
                    render: (_, item) => (
                      <span>
                        {item.state === "failed" && (
                          <span
                            className="m6-update-row-dot"
                            role="img"
                            aria-label={`处理异常：${item.name}`}
                          />
                        )}
                        {item.name}
                      </span>
                    ),
                  },
                  {
                    title: "输入数据截至",
                    render: (_, item) =>
                      item.code === "refund_targets" ? "按确认配置" : date(item.date),
                  },
                  { title: "最近生成", render: (_, item) => time(item.generated_at) },
                  {
                    title: "状态",
                    render: (_, item) => (
                      <Tag
                        color={
                          item.state === "failed"
                            ? "error"
                            : item.state === "syncing"
                              ? "processing"
                              : item.state === "pending"
                                ? "warning"
                                : item.state === "available"
                                  ? "success"
                                  : "default"
                        }
                      >
                        {item.state === "failed"
                          ? "最近计算失败"
                          : item.state === "syncing"
                            ? "正在计算"
                            : item.state === "pending"
                              ? "等待重算"
                              : item.state === "available"
                                ? "结果可用"
                                : "尚无结果"}
                      </Tag>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
    </>
  );
}

/** 入口与弹窗共享一次状态查询；全来源失败持续提醒，只有实际恢复后才清除。 */
export const DataUpdateStatus = memo(function DataUpdateStatus() {
  const [open, setOpen] = useState(false);
  const query = useDataUpdateStatus(open);
  const alerts = dataUpdateAlerts(query.data?.data);
  const statusId = useId();
  const summary = [
    alerts.failed ? `数据更新异常：${alerts.description}` : "",
    query.isError ? "状态读取失败，暂时无法确认最新状态" : "",
    !query.isError && alerts.stale ? "部分来源检查已过期" : "",
  ]
    .filter(Boolean)
    .join("；");
  return (
    <>
      <Tooltip title={summary || (query.isPending ? "正在读取更新状态" : undefined)}>
        <Badge className="m6-update-badge" dot={alerts.failed} title="" offset={[-2, 2]}>
          <Button
            aria-describedby={statusId}
            className="m6-update-entry"
            icon={<DatabaseOutlined aria-hidden="true" />}
            onClick={() => setOpen(true)}
          >
            数据更新状态
            {(query.isError || (!alerts.failed && alerts.stale)) && (
              <span
                className="m6-update-warning"
                aria-label={query.isError ? "状态读取失败" : "检查已过期"}
              >
                {query.isError ? " · 读取失败" : " · 检查已过期"}
              </span>
            )}
          </Button>
        </Badge>
      </Tooltip>
      <span
        id={statusId}
        className="m6-update-sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {alerts.failed && <span aria-label="数据更新异常">{summary}</span>}
        {!alerts.failed && summary}
      </span>
      <Modal
        title="数据更新状态"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        width={1360}
        className="m6-update-modal"
      >
        {open && <StatusContent query={query} />}
      </Modal>
    </>
  );
});
