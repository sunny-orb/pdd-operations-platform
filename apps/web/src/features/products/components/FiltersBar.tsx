import { Button, DatePicker, Input, Select, Space, Tag } from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { reconcileSelection } from "./selection";
import type { Filters, Options, Scope } from "../api/contracts";

const optionsOf = (values: string[]) =>
  values.map((value) => ({
    value,
    label: value === "__unassigned__" ? "未匹配 / 资料冲突" : value,
  }));
/** 筛选控件只更新所属scope；日历/选择/搜索统一使用Ant Design，商品值含店铺。 */
export function FiltersBar({
  scope,
  filters,
  onChange,
  options,
  score = false,
  onReset,
}: {
  scope: Scope;
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  options: Options | null;
  score?: boolean;
  onReset: () => void;
}) {
  const [search, setSearch] = useState(filters.keyword);
  const [lastKeyword, setLastKeyword] = useState(filters.keyword);
  if (lastKeyword !== filters.keyword) {
    setLastKeyword(filters.keyword);
    setSearch(filters.keyword);
  }
  useEffect(() => {
    if (search === filters.keyword) return;
    const timer = setTimeout(() => onChange({ keyword: search }), 300);
    return () => clearTimeout(timer);
  }, [search, filters.keyword, onChange]);
  const selectedOptions =
    options?.products.filter(
      (p) =>
        options?.retainSelection ||
        ((!filters.shop || p.shopId === filters.shop) &&
          (!filters.operator || p.operator === filters.operator) &&
          (!filters.category || p.category === filters.category) &&
          (!filters.level || p.level === filters.level)),
    ) ?? [];
  useEffect(() => {
    if (!options) return;
    const patch = reconcileSelection(filters, options);
    if (patch) onChange(patch);
  }, [options, filters, onChange]);
  const label = scope === "overview" ? "经营总览" : scope === "scores" ? "新品评分" : "商品明细";
  return (
    <div className="product-filters" role="group" aria-label={`${label}筛选`}>
      {!score && (
        <DatePicker.RangePicker
          aria-label={`${label}日期`}
          format="YYYY-MM-DD"
          value={
            filters.dateFrom
              ? [dayjs(filters.dateFrom), dayjs(filters.dateTo || filters.dateFrom)]
              : null
          }
          onChange={(_, dates) =>
            onChange({
              dateFrom: dates[0] || (options?.retainSelection ? options.dateRange.from : ""),
              dateTo: dates[1] || (options?.retainSelection ? options.dateRange.to : ""),
            })
          }
          allowClear
        />
      )}
      <Select
        aria-label={`${label}店铺`}
        placeholder="全部店铺"
        allowClear
        value={filters.shop || undefined}
        options={options?.shops ?? []}
        onChange={(v) => onChange({ shop: v ?? "" })}
      />
      <Select
        aria-label={`${label}运营`}
        placeholder="全部运营"
        allowClear
        value={filters.operator || undefined}
        options={optionsOf(options?.operators ?? [])}
        onChange={(v) => onChange({ operator: v ?? "" })}
      />
      <Select
        aria-label={`${label}品类`}
        placeholder="全部品类"
        allowClear
        value={filters.category || undefined}
        options={optionsOf(options?.categories ?? [])}
        onChange={(v) => onChange({ category: v ?? "" })}
      />
      {score ? (
        <Select
          aria-label="新品判断筛选"
          placeholder="全部判断"
          allowClear
          value={filters.judgment || undefined}
          options={
            options?.decisions ?? [
              { value: "complete", label: "完整验证" },
              { value: "partial", label: "待验证" },
              { value: "unscored", label: "暂未评分" },
            ]
          }
          onChange={(v) => onChange({ judgment: v ?? "" })}
        />
      ) : (
        <Select
          aria-label={`${label}等级`}
          placeholder="全部等级"
          allowClear
          value={filters.level || undefined}
          options={optionsOf(options?.levels ?? [])}
          onChange={(v) => onChange({ level: v ?? "" })}
        />
      )}
      <Select
        className="product-picker"
        aria-label={`${label}商品`}
        mode="multiple"
        maxTagCount="responsive"
        maxCount={20}
        placeholder="全部商品"
        showSearch
        optionFilterProp="label"
        value={filters.products}
        options={[
          ...selectedOptions.map((p) => ({ value: p.key, label: `${p.name} · ${p.shopName}` })),
          ...filters.products
            .filter((key) => !selectedOptions.some((p) => p.key === key))
            .map((key) => ({
              value: key,
              label: `商品 ${JSON.parse(key)[1]} · 店铺 ${JSON.parse(key)[0]}`,
            })),
        ]}
        onChange={(products) => onChange({ products })}
        allowClear
      />
      {options?.retainSelection && (
        <span className="muted">
          商品目录共 {options.productTotal} 款，先展示前100款；可按名称或ID搜索。
        </span>
      )}
      <Input
        aria-label={`${label}搜索`}
        prefix={<SearchOutlined />}
        placeholder="搜索商品名称 / ID"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        allowClear
      />
      <Button
        icon={<ReloadOutlined />}
        onClick={() => {
          setSearch("");
          onReset();
        }}
      >
        重置
      </Button>
      <Space size={4} wrap className="filter-scope-note">
        <Tag variant="filled">{label}独立范围</Tag>
        {!options && <span className="muted">来源未接入</span>}
      </Space>
    </div>
  );
}
