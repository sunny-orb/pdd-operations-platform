import { Empty, Table, Tabs } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Analysis as AnalysisData } from "./contracts";
import type { AftersalesState } from "./data";
import { views, type View } from "./state";
import { formatValue } from "./format";
export function Analysis({ state }: { state: AftersalesState }) {
  const data = state.analysis.data?.data;
  const scope = state.scope;
  const columns: ColumnsType<AnalysisData["rows"][number]> = (data?.columns ?? []).map(
    (column) => ({
      key: column.key,
      title: column.label,
      width:
        column.type === "list"
          ? 205
          : column.key === "category"
            ? 88
            : column.type === "text"
              ? 145
              : 98,
      render: (_, row) => {
        const cell = row.cells[column.key];
        return (
          <div className="m5-table-cell">
            {column.type === "list" ? (
              cell?.items?.length ? (
                <ul>
                  {cell.items.map((item, index) => (
                    <li key={`${item.name}-${index}`}>
                      {item.name} {formatValue(item.qty)}（{formatValue(item.share, "percent")}）
                    </li>
                  ))}
                </ul>
              ) : (
                <span>—</span>
              )
            ) : column.type === "text" ? (
              <span>{cell?.value ?? "—"}</span>
            ) : (
              <span>{formatValue(cell?.value, column.type)}</span>
            )}
            {cell?.detail && <small>{cell.detail}</small>}
          </div>
        );
      },
    }),
  );
  return (
    <section className="m5-analysis" id="aftersales-analysis">
      <Tabs
        type="card"
        activeKey={scope.view}
        onChange={(view) => state.change({ view: view as View })}
        items={views.map((view) => ({ key: view.key, label: view.label }))}
      />
      <div className="m5-panel m5-analysis-panel">
        <div className="m5-panel-heading">
          <div>
            <h2>{views.find((v) => v.key === scope.view)?.label}</h2>
            <p className="m5-scope-note">{data?.scope_note ?? "等待报告查询"}</p>
          </div>
          <span>{data?.total ?? 0} 条</span>
        </div>
        <Table
          className="m5-analysis-table"
          rowKey="id"
          columns={columns}
          dataSource={data?.rows ?? []}
          loading={state.analysis.isFetching}
          size="small"
          scroll={{
            x: Math.max(
              650,
              columns.reduce((total, column) => total + Number(column.width), 0),
            ),
            y: 480,
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  data?.status === "source_unavailable"
                    ? "来源尚不可用，未展示经营数字"
                    : "当前条件暂无记录"
                }
              />
            ),
          }}
          pagination={{
            current: scope.page,
            pageSize: scope.size,
            total: data?.total ?? 0,
            showSizeChanger: true,
            pageSizeOptions: [25, 50, 100],
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, size) => state.change({ page, size }),
          }}
        />
      </div>
    </section>
  );
}
