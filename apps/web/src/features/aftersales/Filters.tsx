import { useState } from "react";
import { Button, Segmented, Select } from "antd";
import { SearchInput } from "../../shared/components/SearchInput";
import { ReloadOutlined } from "@ant-design/icons";
import type { AftersalesState } from "./data";
import type { Focus } from "./state";
import type { Report } from "./contracts";

type ReportOption = {
  value: string;
  label: string;
  name?: string;
  period?: string;
  "aria-label"?: string;
};

const reportTypeLabels: Record<Report["report_type"], string> = {
  weekly: "周报",
  monthly: "月报",
  custom: "自定义",
};

function ReportLabel({ option }: { option: ReportOption }) {
  return option.period ? (
    <span className="m5-report-select-label" title={option.label}>
      <span className="m5-report-select-name">{option.name}</span>
      <span className="m5-report-select-period">{option.period}</span>
    </span>
  ) : (
    option.label
  );
}

/** 原售后侧栏筛选进入正文；共享左栏仅承担页内目录。 */
export function Filters({ state }: { state: AftersalesState }) {
  const { scope } = state;
  const [searchReset, setSearchReset] = useState(0);
  const [selectedReport, setSelectedReport] = useState<ReportOption | null>(null);
  const data = state.options.data?.data;
  const [reportCatalog, setReportCatalog] = useState<Report[]>([]);
  // 仅缓存报告目录，让慢请求中仍可改选；品类与经营数据继续绑定当前报告。
  if (data && reportCatalog !== data.reports) setReportCatalog(data.reports);
  const reports: ReportOption[] = [
    { value: "", label: "最新报告" },
    ...(data?.reports ?? reportCatalog).map((report) => {
      const period = `${report.period_start} 至 ${report.period_end}`;
      const label = `${report.label} ${period}`;
      return {
        value: report.id,
        name: reportTypeLabels[report.report_type],
        label,
        period,
        "aria-label": label,
      };
    }),
  ];
  return (
    <section className="m5-panel m5-filters" id="aftersales-filters" aria-label="售后分析筛选条件">
      <label className="m5-report-filter">
        报告周期
        <Select<string, ReportOption>
          className="m5-report-select"
          styles={{ content: { opacity: 1 } }}
          aria-label="报告周期"
          value={scope.reportExplicit ? scope.report : ""}
          options={reports}
          virtual={false}
          loading={state.meta.isPending || state.options.isFetching}
          optionRender={(option) => <ReportLabel option={option.data} />}
          labelRender={(item) => {
            const option =
              reports.find((report) => report.value === item.value) ??
              (selectedReport?.value === item.value ? selectedReport : null);
            if (option) return <ReportLabel option={option} />;
            if (state.meta.isError || state.options.isError) return "报告加载失败";
            return data ? "报告不可用" : "正在加载报告…";
          }}
          onChange={(report) => {
            // 只保留选中项名称，跨报告请求时不复用旧指标或旧品类。
            setSelectedReport(reports.find((item) => item.value === report) ?? null);
            state.change({ report, category: "" });
          }}
        />
      </label>
      <label>
        品类
        <Select
          aria-label="品类"
          value={scope.category}
          showSearch
          optionFilterProp="label"
          disabled={state.meta.isPending || state.options.isPending}
          options={[
            { value: "", label: "全部品类" },
            ...(data?.categories ?? []).map((category) => ({ value: category, label: category })),
          ]}
          onChange={(category) => state.change({ category })}
        />
      </label>
      <label className="m5-search">
        单款/原因搜索
        <SearchInput
          type="search"
          aria-label="单款/原因搜索"
          placeholder="款式编码、商品名、原因"
          allowClear
          value={scope.keyword}
          resetKey={JSON.stringify([
            searchReset,
            state.searchRestoreKey,
            scope.report,
            scope.category,
            scope.focus,
          ])}
          onSearch={(keyword) => state.change({ keyword })}
        />
      </label>
      <div className="m5-focus">
        <span>问题重点</span>
        <Segmented
          aria-label="问题重点"
          options={[
            { label: "全部", value: "all" },
            { label: "尺码", value: "size" },
            { label: "质量", value: "quality" },
          ]}
          value={scope.focus}
          onChange={(focus) => state.change({ focus: focus as Focus })}
        />
      </div>
      <Button
        icon={<ReloadOutlined aria-hidden />}
        onClick={() => {
          setSearchReset((n) => n + 1);
          state.reset();
        }}
      >
        重置筛选
      </Button>
    </section>
  );
}
