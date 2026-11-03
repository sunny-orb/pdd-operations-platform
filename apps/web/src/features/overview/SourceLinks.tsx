import { Link } from "react-router-dom";
import { Button, Popover } from "antd";
import { sourceLabel, type DomainId } from "./model";
/** 归并后保留各来源原始定位参数，重复地址只展示一次。 */
export function SourceLinks({
  members,
}: {
  members: readonly { domain?: DomainId; link: string }[];
}) {
  const links = [...new Map(members.map((item) => [item.link, item])).values()];
  const content = (
    <div className="m6-source-links">
      {links.map((item) => (
        <Link key={item.link} to={item.link}>
          {sourceLabel(item.domain)} →
        </Link>
      ))}
    </div>
  );
  return links.length > 2 ? (
    <Popover title="各来源定位入口" content={content} trigger="click">
      <Button type="link" className="m6-source-more">
        全部入口（{links.length}）
      </Button>
    </Popover>
  ) : (
    content
  );
}
