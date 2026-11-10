import { useEffect, useRef } from "react";
import { usePublishedVersions } from "./publishedVersions";

type Domain = "products" | "promotion" | "sales" | "aftersales";

/** 首次读取由页面负责；仅后续发布token变化触发该域刷新。 */
export function usePublishedDomainRefresh(domain: Domain, refresh: () => void) {
  const versions = usePublishedVersions();
  const token = versions.data?.[domain];
  const previous = useRef<string | undefined>(undefined);
  const callback = useRef(refresh);
  useEffect(() => {
    callback.current = refresh;
  }, [refresh]);
  useEffect(() => {
    if (token === undefined) return;
    const changed =
      previous.current !== undefined ? previous.current !== token : versions.recoveredBaseline;
    previous.current = token;
    if (changed) callback.current();
  }, [token, versions.recoveredBaseline]);
}
