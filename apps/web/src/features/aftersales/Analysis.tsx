import { Empty, Pagination, Table, Tabs, type TableRef } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRef } from "react";
import type { Analysis as AnalysisData, Report } from "./contracts";
import type { AftersalesState } from "./data";
import { views, type View } from "./state";
import { formatValue, reportTypeLabels } from "./format";
import { Explanation } from "./Explanation";
import { StableContent } from "../../shared/components/StableContent";
export function Analysis({ state }: { state: AftersalesState }) {
  const data = state.analysis.data?.data;
  const scope = state.scope;
  const tableRef = useRef<TableRef>(null);
  const comparison = scope.view === "mom" || scope.view === "yoy";
  const history = scope.view === "history";
  const noteInEmpty =
    data?.total === 0 && (comparison || scope.view === "quality" || data.status === "rule_pending");
  const rowNotes = (row: AnalysisData["rows"][number]) => {
    const grouped = new Map<string, string[]>();
    for (const column of data?.columns ?? []) {
      const note = row.cells[column.key]?.detail;
      // 商品规格等文本副值仍在单元格显示；数值/问题分类的口径按行展开。
      if (note && (column.type !== "text" || column.key === "style_code")) {
        grouped.set(note, [...(grouped.get(note) ?? []), column.label]);
      }
    }
    return [...grouped].map(([note, labels]) => ({ note, label: labels.join("、") }));
  };
  const columns: ColumnsType<AnalysisData["rows"][number]> = (data?.columns ?? [])
    .filter((column) => !history || column.key !== "generated_at")
    .map((column) => ({
      key: column.key,
      title: column.label,
      width:
        column.type === "list"
          ? 205
          : history && column.key === "period"
            ? 200
            : column.key === "category"
              ? 88
              : column.type === "text"
                ? 145
                : 98,
      render: (_, row) => {
        const cell = row.cells[column.key];
        if (
          comparison &&
          row.cells.type?.value === "总览" &&
          (column.key === "rate_change" || column.key === "reasons")
        )
          return null;
        if (history && column.key === "type") {
          return reportTypeLabels[cell?.value as Report["report_type"]] ?? cell?.value ?? "—";
        }
        if (history && column.key === "period") return cell?.value?.replace("—", " 至 ") ?? "—";
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
            {cell?.detail && column.type === "text" && column.key !== "style_code" && (
              <small>{cell.detail}</small>
            )}
          </div>
        );
      },
    }));
  return (
    <section className="m5-analysis" id="aftersales-analysis">
      <Tabs
        type="card"
        activeKey={scope.view}
        onChange={(view) => {
          tableRef.current?.scrollTo({ top: 0 });
          state.change({ view: view as View });
        }}
        items={views.map((view) => ({ key: view.key, label: view.label }))}
      />
      <StableContent
        pending={!state.meta?.isError && (state.meta?.isPending || state.analysis.isPending)}
        ready={Boolean(data)}
        label="正在更新明细…"
      >
        <div className="m5-panel m5-analysis-panel">
          <div className="m5-panel-heading">
            <div>
              <h2>{views.find((v) => v.key === scope.view)?.label}</h2>
            </div>
            <span>{data?.total ?? 0} 条</span>
          </div>
          <div className="m5-analysis-note">
            {data?.scope_note && !noteInEmpty && (
              <Explanation key={scope.view} label="统计口径">
                <p>{data.scope_note}</p>
              </Explanation>
            )}
          </div>
          <Table
            ref={tableRef}
            className="m5-analysis-table"
            rowKey="id"
            columns={columns}
            dataSource={data?.rows ?? []}
            loading={state.analysis.isFetching}
            size="small"
            expandable={
              data?.rows.some((row) => rowNotes(row).length)
                ? {
                    columnTitle: "说明",
                    columnWidth: 48,
                    rowExpandable: (row) => rowNotes(row).length > 0,
                    expandedRowRender: (row) => (
                      <dl className="m5-row-notes">
                        {rowNotes(row).map(({ note, label }) => (
                          <div key={note}>
                            <dt>{label}</dt>
                            <dd>{note}</dd>
                          </div>
                        ))}
                      </dl>
                    ),
                  }
                : undefined
            }
            scroll={{
              x: Math.max(
                650,
                columns.reduce((total, column) => total + Number(column.width), 0),
              ),
              y: "var(--m5-analysis-body-height)",
            }}
            locale={{
              expand: "展开行说明",
              collapse: "收起行说明",
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    noteInEmpty && data?.scope_note
                      ? data.scope_note
                      : data?.status === "source_unavailable"
                        ? "来源暂不可用"
                        : "当前条件暂无记录"
                  }
                />
              ),
            }}
            pagination={false}
          />
          <nav className="m5-analysis-pagination" aria-label="分析明细分页">
            <Pagination
              size="small"
              current={scope.page}
              pageSize={scope.size}
              total={data?.total ?? 0}
              disabled={!data?.total}
              showSizeChanger
              showLessItems
              pageSizeOptions={[25, 50, 100]}
              onChange={(page, size) => {
                tableRef.current?.scrollTo({ top: 0 });
                state.change({ page, size });
              }}
            />
          </nav>
        </div>
      </StableContent>
    </section>
  );
}
