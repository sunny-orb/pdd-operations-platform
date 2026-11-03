import { useEffect, useState, type ReactNode } from "react";
import { Button, DatePicker, Input, Select, Spin } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { validDate, type Scope, type ScopeName } from "./scope";
import { useOptions, type ProductsState } from "./data";
import { catalogOptions } from "./catalog";
import { formatProductLevel } from "../../shared/products/levels";

export function Filters({
  scope,
  name,
  state,
  onBeforeChange,
  status,
}: {
  scope: Scope;
  name: ScopeName;
  state: ProductsState;
  onBeforeChange?: () => void;
  status?: ReactNode;
}) {
  const [needle, setNeedle] = useState("");
  const result = useOptions(scope, state, needle, name);
  const first = result.data;
  useEffect(() => {
    // 评分的新品条件由区块固定；空目录不能触发反复清除这个隐藏条件。
    const selection = first && {
      ...first.selection,
      ...(name === "score" ? { level: scope.level } : {}),
    };
    if (
      !result.isFetching &&
      !result.isPlaceholderData &&
      !result.error &&
      selection &&
      (selection.operator !== scope.operator ||
        selection.category !== scope.category ||
        selection.level !== scope.level ||
        selection.products.join() !== scope.products.join())
    ) {
      state.change(name, { ...selection, page: 1 });
    }
  }, [first, result.isFetching, result.isPlaceholderData, result.error, scope, state, name]);
  const products = first?.products ?? [];
  const change = (patch: Partial<Scope>) => {
    onBeforeChange?.();
    const sameDates =
      (patch.from ?? scope.from) === scope.from && (patch.to ?? scope.to) === scope.to;
    const selection =
      result.catalog && !result.isPlaceholderData && !result.error && sameDates
        ? catalogOptions(result.catalog, { ...scope, ...patch }).selection
        : {};
    state.change(name, {
      ...patch,
      ...selection,
      ...(name === "score" ? { level: scope.level } : {}),
      page: 1,
    });
  };
  const label = name === "main" ? "总览" : name === "score" ? "评分" : "明细";
  const preparing =
    state.metadata.isPending || (result.isFetching && (!result.data || result.isPlaceholderData));
  const empty = result.error ? (
    <Button
      size="small"
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => void result.refetch()}
    >
      选项加载失败，重试
    </Button>
  ) : preparing ? (
    <Spin size="small" />
  ) : (
    "暂无匹配选项"
  );
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
      popupMatchSelectWidth={field === "level" ? 240 : undefined}
      options={
        field === "level"
          ? values.map((item) => ({ ...item, label: formatProductLevel(item.value) }))
          : values
      }
      popupRender={
        field === "level"
          ? (menu) => (
              <>
                {menu}
                <div className="m1-option-footer">
                  <small>多等级单独归类，不计入A级、B级等单等级。</small>
                </div>
              </>
            )
          : undefined
      }
      showSearch={{ optionFilterProp: "label" }}
      loading={preparing}
      disabled={preparing}
      notFoundContent={empty}
      onChange={(value) => change({ [field]: value ?? "" })}
    />
  );
  const strings = (values: string[] | undefined) =>
    (values ?? []).map((value) => ({ value, label: value }));
  const reset = () => {
    onBeforeChange?.();
    setNeedle("");
    state.reset(name);
  };
  const shopSelect = select(
    "shop",
    "店铺",
    (first?.shops ?? []).map((v) => ({ ...v, label: v.label ?? v.value })),
  );
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
      {name === "detail" && shopSelect}
      {name !== "score" && (
        <div className="m1-date-range">
          <DatePicker
            aria-label={`${label}开始日期`}
            value={validDate(scope.from) ? dayjs(scope.from) : null}
            allowClear={false}
            minDate={state.metadata.data ? dayjs(state.metadata.data.date_from) : undefined}
            maxDate={state.metadata.data ? dayjs(state.metadata.data.date_to) : undefined}
            onChange={(value) => {
              if (value) {
                const from = value.format("YYYY-MM-DD");
                change({ from });
              }
            }}
          />
          <span className="m1-date-separator">至</span>
          <DatePicker
            aria-label={`${label}结束日期`}
            value={validDate(scope.to) ? dayjs(scope.to) : null}
            allowClear={false}
            minDate={state.metadata.data ? dayjs(state.metadata.data.date_from) : undefined}
            maxDate={state.metadata.data ? dayjs(state.metadata.data.date_to) : undefined}
            onChange={(value) => {
              if (value) {
                const to = value.format("YYYY-MM-DD");
                change({ to });
              }
            }}
          />
          {name === "main" && (
            <button
              type="button"
              className="m1-main-reset"
              aria-label="重置总览筛选"
              title="重置筛选"
              onClick={reset}
            >
              <ReloadOutlined />
            </button>
          )}
        </div>
      )}
      {name !== "detail" && shopSelect}
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
        showSearch={{ filterOption: false, searchValue: needle, onSearch: setNeedle }}
        value={scope.products}
        maxTagCount="responsive"
        placeholder="全部单款"
        options={products}
        optionRender={(option) => (
          <div>
            <div>{option.data.label}</div>
            {(option.data.level_details?.length ?? 0) > 1 && (
              <small>{formatProductLevel(option.data.level, option.data.level_details)}</small>
            )}
          </div>
        )}
        labelRender={(v) =>
          products.find((p) => p.value === v.value)?.label ??
          first?.selected_products.find((p) => p.value === v.value)?.label ??
          v.label ??
          String(v.value)
        }
        virtual
        loading={preparing}
        disabled={preparing}
        notFoundContent={empty}
        onOpenChange={(open) => {
          if (!open) setNeedle("");
        }}
        onChange={(products) => {
          setNeedle("");
          change({ products: products.slice(0, 20) });
        }}
        popupRender={(menu) => (
          <>
            {menu}
            <div className="m1-option-footer">
              <small>共 {first?.total ?? 0} 款，最多选择20款</small>
            </div>
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
      {name !== "main" && <Button onClick={reset}>重置</Button>}
      {status !== undefined && (
        <div className="m1-filter-status" role="status" aria-live="polite" aria-atomic="true">
          {status}
        </div>
      )}
    </div>
  );
}
