import { Button, DatePicker, Select } from "antd";
import { memo, useMemo } from "react";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import type { PromotionState } from "./data";
import type { Scope } from "./state";
import { FilterField } from "./FilterField";
import { formatProductLevel } from "./format";

export const Filters = memo(function Filters({ state }: { state: PromotionState }) {
  const { scope, mode, change } = state;
  const data = state.options.data?.data;
  const product = mode === "product";
  const unavailable = !data;
  const emptyText = state.options.error
    ? "筛选选项加载失败"
    : state.options.isFetching
      ? "正在加载选项…"
      : "当前条件无可选项";
  const select = (key: keyof Scope, label: string, choices: { value: string; label: string }[]) => (
    <FilterField label={label}>
      <Select
        aria-label={label}
        value={scope[key] || ""}
        placeholder={emptyText}
        disabled={unavailable}
        loading={state.options.isFetching}
        notFoundContent={emptyText}
        options={[
          {
            value: "",
            label: `全部${key === "shop" ? "店铺" : key === "level" ? "等级" : key === "operator" ? "运营" : "品类"}`,
          },
          ...choices,
        ]}
        onChange={(value) => change({ [key]: value })}
        showSearch
        optionFilterProp="label"
      />
    </FilterField>
  );
  const products = useMemo(
    () => [
      ...new Map(
        [
          ...(state.selectedProducts ?? []),
          ...(data?.selected_products ?? []),
          ...(data?.products ?? []),
        ].map((item) => [item.value, item]),
      ).values(),
    ],
    [state.selectedProducts, data?.selected_products, data?.products],
  );
  const filtered =
    product &&
    Boolean(
      scope.shop ||
      scope.category ||
      scope.level ||
      scope.operator ||
      scope.keyword ||
      scope.products.length ||
      state.needle,
    );
  return (
    <section
      className="filters"
      id="promotion-filters"
      aria-label={product ? "商品推广筛选条件" : "店铺推广筛选条件"}
    >
      <FilterField label="开始日期">
        <DatePicker
          aria-label="开始日期"
          value={scope.from && !Number.isNaN(Date.parse(scope.from)) ? dayjs(scope.from) : null}
          minDate={
            state.meta.data?.data.date_from ? dayjs(state.meta.data.data.date_from) : undefined
          }
          maxDate={state.meta.data?.data.date_to ? dayjs(state.meta.data.data.date_to) : undefined}
          format="YYYY-MM-DD"
          allowClear={false}
          onChange={(_, value) => change({ from: String(value) })}
        />
      </FilterField>
      <FilterField label="结束日期">
        <DatePicker
          aria-label="结束日期"
          value={scope.to && !Number.isNaN(Date.parse(scope.to)) ? dayjs(scope.to) : null}
          minDate={
            state.meta.data?.data.date_from ? dayjs(state.meta.data.data.date_from) : undefined
          }
          maxDate={state.meta.data?.data.date_to ? dayjs(state.meta.data.data.date_to) : undefined}
          format="YYYY-MM-DD"
          allowClear={false}
          onChange={(_, value) => change({ to: String(value) })}
        />
      </FilterField>
      {select("shop", product ? "店铺筛选" : "店铺", data?.shops ?? state.shopOptions ?? [])}
      {product ? (
        <>
          <label>
            商品筛选
            <Select
              aria-label="输入商品 ID 或商品名称 筛选"
              mode="multiple"
              maxCount={20}
              maxTagCount="responsive"
              allowClear
              showSearch
              filterOption={false}
              placeholder="输入商品 ID 或商品名称"
              value={scope.products}
              searchValue={state.needle}
              onSearch={state.setNeedle}
              onOpenChange={(open) => {
                if (!open) state.setNeedle("");
              }}
              onChange={(value) => {
                change({ products: value });
                state.setNeedle("");
              }}
              options={products}
              optionRender={(option) =>
                option.data.level === "多等级" ? (
                  <div>
                    {option.label}
                    <small className="m2-option-level">
                      {formatProductLevel(option.data.level, option.data.level_details)}
                    </small>
                  </div>
                ) : (
                  option.label
                )
              }
              loading={state.options.isFetching || state.selectedOptions?.isFetching}
              disabled={unavailable}
              notFoundContent={emptyText}
              popupRender={(menu) => (
                <>
                  {menu}
                  {data && data.total > 0 && (
                    <small className="m2-options-hint">可选 {data.total} 个商品</small>
                  )}
                </>
              )}
            />
          </label>
          {select(
            "category",
            "品类筛选",
            (data?.categories ?? []).map((value) => ({ value, label: value })),
          )}
          {select(
            "level",
            "产品等级",
            (data?.levels ?? []).map((value) => ({ value, label: value })),
          )}
          {select(
            "operator",
            "运营筛选",
            (data?.operators ?? []).map((value) => ({ value, label: value })),
          )}
          {filtered && (
            <div className="m2-filter-actions">
              {scope.keyword && (
                <span className="m2-keyword-filter">
                  商品搜索：{scope.keyword}
                  <Button
                    type="text"
                    size="small"
                    aria-label="清除商品搜索"
                    onClick={() => change({ keyword: "" })}
                  >
                    ×
                  </Button>
                </span>
              )}
              <Button
                type="link"
                size="small"
                onClick={() =>
                  change({
                    shop: "",
                    category: "",
                    level: "",
                    operator: "",
                    keyword: "",
                    products: [],
                  })
                }
              >
                清空筛选
              </Button>
            </div>
          )}
        </>
      ) : (
        <FilterField label="推广渠道">
          <Select
            aria-label="推广渠道"
            value={scope.channel}
            options={[
              { value: "", label: "合计（含官方店明星店铺）" },
              ...(data?.channels ?? [
                { value: "product_promotion", label: "商品推广" },
                { value: "star_shop", label: "明星店铺" },
              ]),
            ]}
            onChange={(value) => change({ channel: value })}
          />
        </FilterField>
      )}
      {state.options.error && (
        <div className="m2-filter-options-error" role="alert">
          筛选选项暂时无法加载。
          <Button size="small" onClick={() => void state.options.refetch()}>
            重试
          </Button>
        </div>
      )}
      {product && state.selectedOptions?.error && (
        <div className="m2-filter-options-error" role="alert">
          已选商品名称暂时无法加载。
          <Button size="small" onClick={() => void state.selectedOptions.refetch()}>
            重试商品名称
          </Button>
        </div>
      )}
    </section>
  );
});
