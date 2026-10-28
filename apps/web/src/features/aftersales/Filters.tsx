import { Button, Input, Segmented, Select } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { AftersalesState } from "./data";
import type { Focus } from "./state";
/** 原售后侧栏筛选进入正文；共享左栏仅承担页内目录。 */
export function Filters({ state }: { state: AftersalesState }) {
  const { scope } = state;
  const data = state.options.data?.data;
  return (
    <section className="m5-panel m5-filters" id="aftersales-filters" aria-label="售后分析筛选条件">
      <label>
        报告周期
        <Select
          aria-label="报告周期"
          value={scope.report || ""}
          options={[
            { value: "", label: "最新报告" },
            ...(data?.reports ?? []).map((report) => ({
              value: report.id,
              label: `${report.label}（${report.period_start} 至 ${report.period_end}）`,
            })),
          ]}
          onChange={(report) => state.change({ report, category: "" })}
        />
      </label>
      <label>
        品类
        <Select
          aria-label="品类"
          value={scope.category}
          showSearch
          optionFilterProp="label"
          options={[
            { value: "", label: "全部品类" },
            ...(data?.categories ?? []).map((category) => ({ value: category, label: category })),
          ]}
          onChange={(category) => state.change({ category })}
        />
      </label>
      <label className="m5-search">
        单款/原因搜索
        <Input
          type="search"
          aria-label="单款/原因搜索"
          placeholder="款式编码、商品名、原因"
          allowClear
          value={scope.keyword}
          onChange={(event) => state.change({ keyword: event.target.value })}
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
      <Button icon={<ReloadOutlined aria-hidden />} onClick={state.reset}>
        重置筛选
      </Button>
      {data?.note && <p className="m5-filter-note">{data.note}</p>}
    </section>
  );
}
