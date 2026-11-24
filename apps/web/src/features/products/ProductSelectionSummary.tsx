import { useState } from "react";
import { Button, Modal, Table } from "antd";
import { useOptions, type ProductsState } from "./data";
import { dimensionValues, type Scope, type ScopeName } from "./scope";
import { AssistantPageContext } from "../../shared/assistant/AssistantContext";

type Props = {
  scope: Scope;
  state: ProductsState;
  name: ScopeName;
  ready: boolean;
  count?: string;
};

/** 只展示已应用范围，复用完整轻量目录；不回写筛选或读取经营明细来拼名单。 */
export function ProductSelectionSummary({ scope, state, name, ready, count }: Props) {
  const [open, setOpen] = useState(false);
  const result = useOptions(scope, state, "", name);
  const directoryReady = !!result.data && !result.isPlaceholderData && !result.error;
  const selected = scope.products;
  const rows = (result.data?.products ?? []).filter(
    (item) => !selected.length || selected.includes(item.value),
  );
  const total = ready ? (count ?? (directoryReady ? String(rows.length) : undefined)) : undefined;
  const listReady = ready && directoryReady && total === String(rows.length);
  const selectedItem =
    selected.length === 1 && listReady && rows.length === 1
      ? result.catalog?.items.find((item) => item.value === selected[0])
      : undefined;
  const selectedShops = dimensionValues(scope.shop);
  const chosen = result.data?.selected_products ?? [];
  const title = selected.length
    ? `已选 ${selected.length} 个商品`
    : scope.keyword
      ? `关键词：${scope.keyword}`
      : "全部商品";
  const detail = selected.length && scope.keyword ? `关键词：${scope.keyword} · ` : "";
  return (
    <div
      className="m1-selection-summary"
      aria-label={`${name === "main" ? "总览" : "明细"}商品范围`}
    >
      {name === "main" && (
        <AssistantPageContext
          page="商品数据"
          subject={
            selectedItem
              ? {
                  listing_id: selectedItem.value,
                  shop_id: selectedItem.shop,
                  name: selectedItem.product_name ?? null,
                  shop_name:
                    result.catalog?.shops.find((s) => s.value === selectedItem.shop)?.label ?? null,
                }
              : null
          }
          context={
            ready
              ? {
                  date_from: scope.from,
                  date_to: scope.to,
                  shop_id: selectedShops.length === 1 ? selectedShops[0] : undefined,
                  shop_ids: selectedShops,
                  product_version: state.version,
                  domain: "products",
                  listing_ids: selected,
                  category_values: dimensionValues(scope.category),
                  operator_values: dimensionValues(scope.operator),
                  level_values: dimensionValues(scope.level),
                  keyword: scope.keyword || undefined,
                  category:
                    typeof scope.category === "string" ? scope.category || undefined : undefined,
                  operator:
                    typeof scope.operator === "string" ? scope.operator || undefined : undefined,
                  level: typeof scope.level === "string" ? scope.level || undefined : undefined,
                }
              : {}
          }
        />
      )}
      <span>商品范围</span>
      <b>{title}</b>
      {selected.length > 0 && directoryReady && (
        <small
          className="m1-selected-names"
          title={chosen.map((item) => item.product_name ?? item.label).join("；")}
        >
          {chosen
            .slice(0, 2)
            .map((item) => item.product_name ?? item.label)
            .join("；")}
          {chosen.length > 2 ? "…" : ""}
        </small>
      )}
      <small>
        {detail}
        {selected.length && scope.keyword ? "实际匹配" : scope.keyword ? "匹配" : "共"}{" "}
        {total ?? "—"} 个店铺商品链接
      </small>
      {total === "0" && <small>当前条件下没有匹配商品</small>}
      <Button
        type="link"
        size="small"
        disabled={!listReady || !rows.length}
        onClick={() => setOpen(true)}
      >
        查看商品
      </Button>
      {result.error && (
        <Button type="link" size="small" onClick={() => void result.refetch()}>
          重试商品名单
        </Button>
      )}
      <Modal
        title="实际统计商品"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={720}
        styles={{ body: { maxHeight: "calc(100dvh - 200px)", overflowY: "auto" } }}
        destroyOnHidden
      >
        <p>
          {title} · {detail}共 {total ?? "—"} 个店铺商品链接
        </p>
        <Table
          key={JSON.stringify(scope)}
          size="small"
          rowKey="value"
          dataSource={listReady ? rows : []}
          columns={[
            {
              title: "商品名称 · 店铺 · 商品 ID",
              dataIndex: "label",
              render: (label: string) => <span style={{ overflowWrap: "anywhere" }}>{label}</span>,
            },
          ]}
          pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
          scroll={{ y: "min(360px, 50vh)" }}
          locale={{ emptyText: ready ? "商品名单暂不可用" : "正在更新结果…" }}
        />
      </Modal>
    </div>
  );
}
