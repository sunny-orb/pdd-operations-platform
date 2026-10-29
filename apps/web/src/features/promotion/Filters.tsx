import { DatePicker, Select } from "antd";
import dayjs from "dayjs";
import type { PromotionState } from "./data";
import type { Scope } from "./state";

export function Filters({ state }: { state: PromotionState }) {
  const { scope, mode, change } = state;
  const data = state.options.data?.data;
  const product = mode === "product";
  const select = (key: keyof Scope, label: string, choices: { value: string; label: string }[]) => (
    <label>
      {label}
      <Select
        aria-label={label}
        value={scope[key] || ""}
        options={[
          {
            value: "",
            label: `全部${key === "shop" ? "店铺" : key === "level" ? "等级" : key === "operator" ? "运营" : "品类"}`,
          },
          ...choices,
        ]}
        onChange={(value) => change({ [key]: value, products: [] })}
        showSearch
        optionFilterProp="label"
      />
    </label>
  );
  const products = [...(data?.selected_products ?? []), ...(data?.products ?? [])].filter(
    (item, index, list) => list.findIndex((p) => p.value === item.value) === index,
  );
  return (
    <section
      className="filters"
      id="promotion-filters"
      aria-label={product ? "商品推广筛选条件" : "店铺推广筛选条件"}
    >
      <label>
        开始日期
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
      </label>
      <label>
        结束日期
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
      </label>
      {select("shop", product ? "店铺筛选" : "店铺", data?.shops ?? [])}
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
              onChange={(value) => {
                change({ products: value });
                state.setNeedle("");
              }}
              options={products}
              loading={state.options.isFetching}
              notFoundContent={state.options.isFetching ? "正在查找商品…" : "当前条件无候选商品"}
              popupRender={(menu) => (
                <>
                  {menu}
                  {data && data.total > data.products.length && (
                    <small className="m2-options-hint">
                      共 {data.total} 个候选，请输入更完整的商品ID或名称缩小范围
                    </small>
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
        </>
      ) : (
        <label>
          推广渠道
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
        </label>
      )}
    </section>
  );
}
