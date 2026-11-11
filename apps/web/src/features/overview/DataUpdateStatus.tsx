import { memo, useId, useState } from "react";
import {
  DatabaseOutlined,
  CopyOutlined,
  ExportOutlined,
  MinusSquareOutlined,
  PlusSquareOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Alert, Badge, Button, Modal, Radio, Select, Table, Tabs, Tag, Tooltip } from "antd";
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
import {
  dataUpdateAlerts,
  sourceHasAlert,
  sourceHasCheckFailure,
  sourceHasSyncFailure,
  matchesSourceFilter,
  type SourceFilter,
} from "./dataUpdateAlerts";
import "./data-update-status.css";
import { HistoryCoverage, HistoryCoverageLabel } from "./HistoryCoverage";
import { UpdateTimeliness, UpdateTimelinessLabel } from "./UpdateTimeliness";

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
        deferred: "已暂缓",
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

const reconciliationLabels = {
  waiting: { text: "等待周对账", color: "default" },
  running: { text: "周对账进行中", color: "processing" },
  failed: { text: "周对账未完成", color: "warning" },
  deferred: { text: "周对账已暂缓", color: "warning" },
  succeeded: { text: "周对账完成", color: "success" },
};

function ReconciliationDetail({ row }: { row: SourceStatus }) {
  const check = row.reconciliation;
  if (!check) return null;
  const hasData = row.datasets.length > 0 && row.datasets.every((item) => item.version_id);
  const messages: Record<string, string> = {
    CHECK_FAILED: "对账未能确认来源更新标记。",
    WAREHOUSE_WEB_LOGIN_REQUIRED: "对账未能读取数仓网页更新时间。",
    SYNC_FAILED: "本次对账未完成数据接收或校验。",
    SOURCE_CHANGED_DURING_SYNC: "对账期间来源再次变化。",
    WORKER_LOST: "上次对账中断。",
    MAINTENANCE_DEFERRED: "项目维护期间暂缓对账。",
    DATASET_RESTORE_REQUIRED: "对账需恢复历史输入后继续。",
  };
  return (
    <>
      <div>
        <dt>每周对账</dt>
        <dd>
          {reconciliationLabels[check.status].text}
          {check.error_code && (
            <>
              {" · "}
              {messages[check.error_code] ?? "本次对账仍待完成。"}
              {hasData && "已有数据可用，请以平台数据日期为准。"}
            </>
          )}
        </dd>
      </div>
      {check.cycle_at && (
        <div>
          <dt>本轮计划时间</dt>
          <dd>{time(check.cycle_at)}</dd>
        </div>
      )}
      {check.last_attempt_at && (
        <div>
          <dt>最近对账尝试</dt>
          <dd>{time(check.last_attempt_at)}</dd>
        </div>
      )}
      {check.last_finished_at && (
        <div>
          <dt>最近对账结束</dt>
          <dd>{time(check.last_finished_at)}</dd>
        </div>
      )}
      <div>
        <dt>最近对账成功</dt>
        <dd>{time(check.last_success_at)}</dd>
      </div>
      {check.next_retry_at && (
        <div>
          <dt>下一次重试</dt>
          <dd>{time(check.next_retry_at)}</dd>
        </div>
      )}
      {check.next_scheduled_at && (
        <div>
          <dt>下一次对账</dt>
          <dd>{time(check.next_scheduled_at)}</dd>
        </div>
      )}
    </>
  );
}

function SyncStatus({ row }: { row: SourceStatus }) {
  const state = platformLabels[row.platform_state];
  const reconciliation = row.reconciliation && reconciliationLabels[row.reconciliation.status];
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
      {reconciliation && <Tag color={reconciliation.color}>{reconciliation.text}</Tag>}
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
  const syncFailed = sourceHasSyncFailure(row);
  const checkFailed = sourceHasCheckFailure(row);
  return (
    <div className="m6-update-detail">
      <UpdateTimeliness row={row} />
      <HistoryCoverage row={row} />
      {(syncFailed || checkFailed) && (
        <p>
          异常影响：{row.pages.map((page) => pageNames[page]).join("、")}。
          {row.upstream.state === "failed" && "上游检查未成功；"}
          {syncFailed && "平台接收未成功；"}
          {row.history?.state === "failed" && "记录覆盖检查未成功；"}
          请以各用途的数据日期和最近成功时间确认当前可用范围。
          {!row.upstream.error_code &&
            !row.datasets.some((item) => item.error_code) &&
            "具体失败原因尚未确认。"}
        </p>
      )}
      {(syncFailed || checkFailed) && (
        <p>
          处理建议：由平台维护人员先核对失败环节；上游采集失败转对应来源维护人员处理，平台接收失败只补跑对应接收任务。维护联系人未登记。补跑后需验证接收成功，已有数据继续保留。
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
              ? "数仓表更新时间"
              : observation.basis === "table_revision"
                ? "飞书表修订号"
                : observation.basis === "publication_content"
                  ? "官方发布时间及正文"
                  : observation.basis === "profile_projection"
                    ? "已授权资料字段内容"
                    : observation.basis === "snapshot_date"
                      ? "货品资料快照日期"
                      : row.reconciliation
                        ? "近7日记录数、业务日期及来源时间；历史修订按每周对账核对"
                        : "近7日记录数、业务日期及来源时间"}
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
              {row.upstream.error_code === "DATASET_RESTORE_REQUIRED"
                ? "继续展示最近可用数据；本次更新需要技术人员恢复历史输入后继续。"
                : row.upstream.error_code === "MAINTENANCE_DEFERRED"
                  ? "项目维护暂缓了本次更新，现有数据继续可用，稍后按计划重试。"
                  : row.upstream.error_code === "SOURCE_CHANGED_DURING_SYNC"
                    ? "同步期间来源再次变化，等待下一轮接收。"
                    : row.upstream.error_code === "SYNC_FAILED"
                      ? "本轮接收未成功，保留最近可用数据。"
                      : row.upstream.error_code === "WAREHOUSE_WEB_LOGIN_REQUIRED"
                        ? "数仓网页登录已失效，保留上次更新时间和可用数据。重新登录后按计划继续检查。"
                        : "本轮上游检查失败，保留上次观测。"}
            </dd>
          </div>
        )}
        <ReconciliationDetail row={row} />
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
    onCell: () =>
      ({ "data-label": "用途 / 上游来源表" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "23%",
    render: (_, row) => (
      <div className="m6-update-name">
        {sourceHasAlert(row) && (
          <span
            className="m6-update-row-dot"
            role="img"
            aria-label={`来源异常：${row.name}`}
            title="此来源检查或同步失败，展开明细查看"
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
    onCell: () => ({ "data-label": "上游最近更新" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "18%",
    render: (_, row) => (
      <div className="m6-update-cell-stack">
        <UpstreamUpdate row={row} />
        <UpdateTimelinessLabel row={row} />
      </div>
    ),
  },
  {
    title: "最近检查",
    onCell: () => ({ "data-label": "最近检查" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "18%",
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
  {
    title: "近30天记录",
    onCell: () => ({ "data-label": "近30天记录" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "12%",
    render: (_, row) => <HistoryCoverageLabel row={row} />,
  },
  {
    title: "平台数据日期",
    onCell: () => ({ "data-label": "平台数据日期" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "10%",
    render: (_, row) => <DatasetDates row={row} />,
  },
  {
    title: "平台同步",
    onCell: () => ({ "data-label": "平台同步" }) as React.HTMLAttributes<HTMLTableCellElement>,
    width: "15%",
    render: (_, row) => <SyncStatus row={row} />,
  },
];

function StatusContent({ query }: { query: ReturnType<typeof useDataUpdateStatus> }) {
  const [page, setPage] = useState("all");
  const [activeTab, setActiveTab] = useState<string>();
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [onlyProcessingFailed, setOnlyProcessingFailed] = useState(false);
  const [sourceKeys, setSourceKeys] = useState<React.Key[]>([]);
  const [processingKeys, setProcessingKeys] = useState<React.Key[]>([]);
  const data = query.data?.data;
  const alerts = dataUpdateAlerts(data);
  const rows = (data?.sources ?? []).filter(
    (row) => (page === "all" || row.pages.includes(page)) && matchesSourceFilter(row, sourceFilter),
  );
  const processingRows = (data?.processing ?? []).filter(
    (item) => !onlyProcessingFailed || item.state === "failed",
  );
  const locateSources = (filter: SourceFilter) => {
    setActiveTab("sources");
    setPage("all");
    setSourceFilter(filter);
    setSourceKeys(
      (data?.sources ?? []).filter((row) => matchesSourceFilter(row, filter)).map((row) => row.id),
    );
  };
  const locateProcessing = () => {
    setActiveTab("processing");
    setOnlyProcessingFailed(true);
    setProcessingKeys(alerts.processing.map((item) => item.code));
  };
  return (
    <>
      <div className="m6-update-intro">
        <span>
          状态更新于 {time(query.data?.meta.generated_at)}
          {hasRunningUpdate(data) && (
            <small className="m6-update-subtext">任务运行中，每15秒跟踪状态</small>
          )}
          {query.isError && (
            <small className="m6-update-subtext">状态读取失败，自动跟踪已暂停</small>
          )}
        </span>
        <div className="m6-update-actions">
          <Button
            size="small"
            icon={<ReloadOutlined aria-hidden="true" />}
            loading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            刷新状态
          </Button>
        </div>
      </div>
      {query.isError && (
        <Alert
          type="warning"
          showIcon
          title="状态读取失败"
          description={data ? "保留上次状态，请稍后刷新。" : "暂时无法读取更新状态，请稍后刷新。"}
        />
      )}
      {data && (
        <div className="m6-update-status-groups">
          <div className="m6-update-status-group">
            <strong>更新状态</strong>
            {alerts.sync.length > 0 && (
              <Button type="link" danger onClick={() => locateSources("sync")}>
                同步失败 {alerts.sync.length} 项
              </Button>
            )}
            {alerts.checks.length > 0 && (
              <Button type="link" danger onClick={() => locateSources("checks")}>
                检查失败 {alerts.checks.length} 项
              </Button>
            )}
            {alerts.reconciliation.length > 0 && (
              <Button type="link" danger onClick={() => locateSources("reconciliation")}>
                周对账失败 {alerts.reconciliation.length} 项
              </Button>
            )}
            {alerts.processing.length > 0 && (
              <Button type="link" danger onClick={locateProcessing}>
                处理失败 {alerts.processing.length} 项
              </Button>
            )}
            {!alerts.failed && <span>未发现检查、同步、周对账或处理失败</span>}
          </div>
          <div className="m6-update-status-group">
            <strong>更新时效</strong>
            {alerts.delayed.length > 0 ? (
              <Button
                type="link"
                className="m6-update-delay"
                onClick={() => locateSources("delayed")}
              >
                更新延迟 {alerts.delayed.length} 项
              </Button>
            ) : (
              <span>暂无已确认的更新延迟</span>
            )}
          </div>
          <div className="m6-update-status-group">
            <strong>记录情况</strong>
            {alerts.records.length > 0 ? (
              <Button type="link" onClick={() => locateSources("records")}>
                部分日期无记录：{alerts.records.length} 个来源
              </Button>
            ) : (
              <span>暂无无记录日期提示</span>
            )}
          </div>
          {(sourceFilter !== "all" || onlyProcessingFailed) && (
            <Button
              type="link"
              onClick={() => {
                setSourceFilter("all");
                setOnlyProcessingFailed(false);
              }}
            >
              显示全部
            </Button>
          )}
        </div>
      )}
      <details className="m6-update-explanation">
        <summary>更新说明</summary>
        <p>
          {!data
            ? "正在读取数据更新状态…"
            : data.monitor.source_schedule_enabled
              ? "按计划读取数仓网页更新时间，有变化或接收失败时同步对应来源。"
              : data.monitor.enabled
                ? `后台自动检查并同步数据；当前每 ${data.monitor.interval_seconds / 60} 分钟检查一次。`
                : "自动检查尚未启用。"}
        </p>
        {data?.monitor.source_schedule_enabled && (
          <p>
            数仓 {data.monitor.warehouse_times?.join(" / ")} · 飞书{" "}
            {data.monitor.feishu_times?.join(" / ")}（北京时间）
            <br />
            {data.monitor.warehouse_times?.includes("16:10") && "16:10 为普通检查。"}
            {data.monitor.reconciliation_time &&
              `数仓每周日 ${data.monitor.reconciliation_time} 对账。`}
            {data.monitor.reconciliation_retry_seconds &&
              `失败结束后 ${data.monitor.reconciliation_retry_seconds / 60} 分钟重试。`}
            聚水潭、天气沿用原计划。
          </p>
        )}
        <p>按昨天更新时间推到今天加一小时判断延迟；没有可靠时间依据则不判定。</p>
        <p>打开时读取状态，可手动刷新。刷新状态只读取平台；同步失败时保留最近成功的数据。</p>
      </details>
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
              <div className="m6-update-panel">
                <div className="m6-update-toolbar">
                  <h3>
                    上游来源{" "}
                    <span>
                      {rows.length} 项{page !== "all" && ` / 共 ${data?.sources.length ?? 0} 项`}
                    </span>
                  </h3>
                  <Select
                    className="m6-update-page-select"
                    aria-label="筛选看板"
                    value={page}
                    onChange={setPage}
                    options={[
                      { label: "全部看板", value: "all" },
                      ...Object.entries(pageNames).map(([value, label]) => ({ value, label })),
                    ]}
                  />
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
                <div
                  className="m6-update-list"
                  role="region"
                  aria-label="来源状态列表"
                  tabIndex={0}
                >
                  <Table
                    className="m6-update-table"
                    rowKey="id"
                    dataSource={rows}
                    columns={columns}
                    loading={query.isPending}
                    pagination={false}
                    size="middle"
                    tableLayout="fixed"
                    locale={{
                      emptyText: sourceFilter !== "all" ? "当前没有符合条件的来源" : "暂无来源状态",
                    }}
                    expandable={{
                      expandedRowKeys: sourceKeys,
                      onExpandedRowsChange: (keys) => setSourceKeys([...keys]),
                      columnTitle: "明细",
                      columnWidth: "4%",
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
                </div>
              </div>
            ),
          },
          {
            key: "processing",
            label: "平台处理",
            forceRender: true,
            children: (
              <div className="m6-update-list" role="region" aria-label="平台处理列表" tabIndex={0}>
                <Table
                  className="m6-update-table"
                  rowKey="code"
                  dataSource={processingRows}
                  locale={{ emptyText: onlyProcessingFailed ? "当前没有处理失败" : "暂无处理状态" }}
                  expandable={{
                    columnTitle: "明细",
                    columnWidth: "6%",
                    expandedRowKeys: processingKeys,
                    onExpandedRowsChange: (keys) => setProcessingKeys([...keys]),
                    expandedRowRender: (item) => (
                      <div className="m6-update-detail">
                        <p>
                          影响页面：
                          {item.pages.map((page) => pageNames[page]).join("、") || "未提供"}
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
                  tableLayout="fixed"
                  columns={[
                    {
                      title: "处理项目",
                      onCell: () =>
                        ({
                          "data-label": "处理项目",
                        }) as React.HTMLAttributes<HTMLTableCellElement>,
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
                      onCell: () =>
                        ({
                          "data-label": "输入数据截至",
                        }) as React.HTMLAttributes<HTMLTableCellElement>,
                      render: (_, item) =>
                        item.code === "refund_targets" ? "按确认配置" : date(item.date),
                    },
                    {
                      title: "最近生成",
                      onCell: () =>
                        ({
                          "data-label": "最近生成",
                        }) as React.HTMLAttributes<HTMLTableCellElement>,
                      render: (_, item) => time(item.generated_at),
                    },
                    {
                      title: "状态",
                      onCell: () =>
                        ({ "data-label": "状态" }) as React.HTMLAttributes<HTMLTableCellElement>,
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
              </div>
            ),
          },
        ]}
      />
    </>
  );
}

/** 入口与弹窗共用状态查询；全来源故障红点优先于延迟黄点，缺记录只在内部说明。 */
export const DataUpdateStatus = memo(function DataUpdateStatus() {
  const [open, setOpen] = useState(false);
  const query = useDataUpdateStatus(open);
  const alerts = dataUpdateAlerts(query.data?.data);
  const delayedOnly = !alerts.failed && alerts.delayed.length > 0;
  const statusId = useId();
  const summary = [
    alerts.failed ? `数据更新异常：${alerts.description}` : "",
    alerts.delayed.length
      ? `上游更新延迟：${alerts.delayed.map((row) => row.name).join("、")}`
      : "",
    query.isError ? "状态读取失败，暂时无法确认最新状态" : "",
    !query.isError && alerts.stale ? "部分来源检查已过期" : "",
  ]
    .filter(Boolean)
    .join("；");
  return (
    <>
      <Tooltip
        title={open ? undefined : summary || (query.isPending ? "正在读取更新状态" : undefined)}
      >
        <Badge
          className={`m6-update-badge${delayedOnly ? " m6-update-badge-delayed" : ""}`}
          dot={alerts.failed || delayedOnly}
          title=""
          offset={[-2, 2]}
        >
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
        {!alerts.failed && <span>{summary}</span>}
        {alerts.delayed.length > 0 && (
          <span aria-label="上游更新延迟">已核对{alerts.delayed.length}个来源更新延迟</span>
        )}
      </span>
      <Modal
        title="数据更新状态"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        closable={{ "aria-label": "关闭数据更新状态" }}
        destroyOnHidden
        width="calc(100vw - 32px)"
        className="m6-update-modal"
      >
        {open && <StatusContent query={query} />}
      </Modal>
    </>
  );
});
