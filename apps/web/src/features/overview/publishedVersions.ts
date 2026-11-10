import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  usePublishedVersions as useVersions,
  type PublishedVersions,
} from "../../shared/api/publishedVersions";
import { domains, type DomainMeta } from "./model";
const scope = (meta: DomainMeta | undefined) =>
  JSON.stringify([meta?.version, meta?.date, meta?.scopeKey]);
/** 首次建立基线，随后只刷新发布指纹改变的负责域。 */
export function usePublishedVersions() {
  const client = useQueryClient();
  const versions = useVersions();
  const previous = useRef<PublishedVersions | undefined>(undefined);
  useEffect(() => {
    if (!versions.data) return;
    const before = previous.current;
    previous.current = versions.data;
    if (!before && !versions.recoveredBaseline) return;
    const changed = domains.filter(({ id }) => !before || before[id] !== versions.data![id]);
    changed.forEach(({ id }) => {
      const metaKey = ["overview", id, "meta"];
      const beforeScope = scope(client.getQueryData<DomainMeta>(metaKey));
      void client.invalidateQueries({ queryKey: metaKey }).then(() => {
        const next = client.getQueryState<DomainMeta>(metaKey);
        // 新版/新范围由查询键自动加载；相同经营版本仍可能有目标等依赖修订。
        if (next?.status === "success" && scope(next.data) === beforeScope) {
          void client.invalidateQueries({
            predicate: (q) =>
              q.queryKey[0] === "overview" &&
              q.queryKey[1] === id &&
              (q.queryKey[2] === "facts" || q.queryKey[2] === "diagnostics"),
          });
        }
      });
    });
  }, [versions.data, versions.recoveredBaseline, client]);
}
