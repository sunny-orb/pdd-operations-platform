import { useState } from "react";
import { Button, DatePicker, Input, Select, Space } from "antd";
import dayjs from "dayjs";
import type { Scope, ScopeName } from "./scope";
import { useOptions, type ProductsState } from "./data";

export function Filters({
  scope,
  name,
  state,
}: {
  scope: Scope;
  name: ScopeName;
  state: ProductsState;
}) {
  const [needle, setNeedle] = useState("");
  const result = useOptions(scope, state, needle, name);
  const first = result.data?.pages[0];
  const products = result.data?.pages.flatMap((p) => p.products) ?? [];
  const change = (patch: Partial<Scope>) => state.change(name, { ...patch, page: 1 });
  const label = name === "main" ? "总览" : name === "score" ? "评分" : "明细";
  const select = (
    field: "shop" | "operator" | "category" | "level",
    placeholder: string,
    values: { value: string; label: string }[],
  ) => (
    <Select
      aria-label={`${label}${placeholder}`}
      allowClear
      value={scope[field] || undefined}
      placeholder={`全部${placeholder}`}
      options={values}
      loading={result.isFetching}
      onChange={(value) =>
        change({
          [field]: value ?? "",
          products: [],
          ...(field === "shop"
            ? { operator: "", category: "", level: "" }
            : field === "operator"
              ? { category: "", level: "" }
              : field === "category"
                ? { level: "" }
                : {}),
        })
      }
    />
  );
  const strings = (values: string[] | undefined) =>
    (values ?? []).map((value) => ({ value, label: value }));
  const reset = () => {
    setNeedle("");
    state.reset(name);
  };
  return (
    <div
      className={
        name === "score"
          ? "new-potential-filters"
          : name === "detail"
            ? "detail-filters"
            : "filters"
      }
      aria-label={`${label}筛选`}
    >
      {name !== "score" && (
        <>
          <DatePicker
            aria-label={`${label}开始日期`}
            value={scope.from ? dayjs(scope.from) : null}
            allowClear={false}
            minDate={state.metadata.data ? dayjs(state.metadata.data.date_from) : undefined}
            maxDate={state.metadata.data ? dayjs(state.metadata.data.date_to) : undefined}
            onChange={(value) => {
              if (value) {
                const from = value.format("YYYY-MM-DD");
                change({ from, ...(from > scope.to ? { to: from } : {}) });
              }
            }}
          />
          <DatePicker
            aria-label={`${label}结束日期`}
            value={scope.to ? dayjs(scope.to) : null}
            allowClear={false}
            minDate={state.metadata.data ? dayjs(state.metadata.data.date_from) : undefined}
            maxDate={state.metadata.data ? dayjs(state.metadata.data.date_to) : undefined}
            onChange={(value) => {
              if (value) {
                const to = value.format("YYYY-MM-DD");
                change({ to, ...(to < scope.from ? { from: to } : {}) });
              }
            }}
          />
        </>
      )}
      {select(
        "shop",
        "店铺",
        (first?.shops ?? []).map((v) => ({ ...v, label: v.label ?? v.value })),
      )}
      {select("operator", "运营", strings(first?.operators))}
      {select("category", "品类", strings(first?.categories))}
      {name !== "score" && select("level", "等级", strings(first?.levels))}
      {name === "score" && (
        <Select
          aria-label="评分判断"
          allowClear
          value={scope.judgement || undefined}
          placeholder="全部判断"
          options={[
            { value: "unscored", label: "暂未评分" },
            { value: "pending_validation", label: "待验证" },
            { value: "small_scale", label: "小额放量" },
          ]}
          onChange={(v) => change({ judgement: v ?? "" })}
        />
      )}
      <Select
        mode="multiple"
        aria-label={`${label}商品`}
        allowClear
        showSearch={{ filterOption: false, onSearch: setNeedle }}
        value={scope.products}
        maxTagCount="responsive"
        placeholder="全部单款"
        options={products}
        labelRender={(v) =>
          products.find((p) => p.value === v.value)?.label ??
          first?.selected_products.find((p) => p.value === v.value)?.label ??
          v.label ??
          String(v.value)
        }
        loading={result.isFetching}
        onChange={(products) => change({ products: products.slice(0, 20) })}
        popupRender={(menu) => (
          <>
            {menu}
            <Space className="m1-option-footer">
              <small>
                候选 {products.length} / {first?.total ?? 0}，最多选择20款
              </small>
              {result.hasNextPage && (
                <Button
                  size="small"
                  loading={result.isFetchingNextPage}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => void result.fetchNextPage()}
                >
                  加载更多
                </Button>
              )}
            </Space>
          </>
        )}
      />
      {name !== "score" && (
        <Input
          aria-label={`${label}搜索商品`}
          value={scope.keyword}
          allowClear
          placeholder="搜索商品名称或商品 ID"
          onChange={(e) => change({ keyword: e.target.value })}
        />
      )}
      <Button onClick={reset}>重置</Button>
    </div>
  );
}
