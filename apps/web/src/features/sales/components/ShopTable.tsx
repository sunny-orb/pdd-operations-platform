import { Button, Table } from "antd";
import type { ShopItem } from "../api/contracts";
import { count, fixed, money, percent } from "../format";

export function ShopTable({
  shops,
  selected,
  onSelect,
}: {
  shops: ShopItem[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <section id="shop-detail" className="dashboard-section shop-table-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">STORE DETAIL</p>
          <h2>查看店铺完整数据</h2>
        </div>
        <p>销售占比以所选日期区间全部店铺交易金额为基数计算。</p>
      </div>
      <div className="shop-table">
        <Table<ShopItem>
          rowKey="shop_id"
          dataSource={shops}
          pagination={false}
          scroll={{ x: 1040 }}
          size="small"
          rowClassName={(row) => (row.shop_id === selected ? "selected" : "")}
          locale={{ emptyText: "本地店铺日数据尚不可用；未使用样例替代" }}
          components={{
            table: (props: React.HTMLAttributes<HTMLTableElement>) => (
              <table {...props} aria-label="店铺完整数据" />
            ),
          }}
          columns={[
            {
              title: "店铺",
              key: "shop",
              render: (_, row) => (
                <Button type="link" onClick={() => onSelect(row.shop_id)}>
                  {row.display_name}
                </Button>
              ),
            },
            { title: "交易额", dataIndex: "shop_revenue", render: (value) => money(value, true) },
            { title: "销售额占比", dataIndex: "sales_share", render: (value) => percent(value, 1) },
            { title: "访客", dataIndex: "shop_visitors", render: count },
            {
              title: "成交转化率",
              dataIndex: "buyer_conversion",
              render: (value) => percent(value),
            },
            {
              title: "客单价",
              dataIndex: "aov",
              render: (value) => (value == null ? "—" : `¥${fixed(value)}`),
            },
            {
              title: "退款占 GMV",
              key: "refund",
              dataIndex: "refund_rate",
              render: (value) => (
                <span title="同店同日统计参考，日期及订单范围待核">{percent(value)}</span>
              ),
            },
            { title: "点击率", dataIndex: "promotion_ctr", render: (value) => percent(value) },
            {
              title: "推广花费",
              dataIndex: "promotion_spend",
              render: (value) => money(value, true),
            },
            {
              title: "ROI",
              key: "roi",
              dataIndex: "promotion_roi",
              render: (value) => (
                <span title="单渠道来源参考；跨渠道成交归因待核">{fixed(value)}</span>
              ),
            },
          ]}
        />
      </div>
    </section>
  );
}
