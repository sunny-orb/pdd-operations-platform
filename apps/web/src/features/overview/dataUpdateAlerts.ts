import type { DataStatus, SourceStatus } from "./sourceStatusApi";

/** 失败必须有实际任务依据；无记录、等待和已发布旧数据不推导为失败。 */
export function sourceHasSyncFailure(source: SourceStatus) {
  return (
    source.platform_state === "failed" ||
    source.datasets.some((item) =>
      ["failed", "abandoned", "cancelled"].includes(item.attempt_status ?? ""),
    )
  );
}
export function sourceHasCheckFailure(source: SourceStatus) {
  return source.upstream.state === "failed" || source.history?.state === "failed";
}
export function sourceHasReconciliationFailure(source: SourceStatus) {
  const reconciliation = source.reconciliation;
  return (
    reconciliation?.status === "failed" ||
    (reconciliation?.status === "running" &&
      Boolean(reconciliation.error_code) &&
      reconciliation.error_code !== "MAINTENANCE_DEFERRED" &&
      reconciliation.error_code !== "DATASET_RESTORE_REQUIRED")
  );
}
export function sourceHasFailure(source: SourceStatus) {
  return (
    sourceHasSyncFailure(source) ||
    sourceHasCheckFailure(source) ||
    sourceHasReconciliationFailure(source)
  );
}
export const sourceHasAlert = sourceHasFailure;
export function sourceHasNoRecords(source: SourceStatus) {
  return (source.history?.missing_count ?? 0) > 0;
}
export function sourceIsDelayed(source: SourceStatus) {
  return source.timeliness?.state === "delayed";
}
export type SourceFilter = "all" | "sync" | "checks" | "reconciliation" | "records" | "delayed";
export function matchesSourceFilter(source: SourceStatus, filter: SourceFilter) {
  return (
    filter === "all" ||
    (filter === "sync"
      ? sourceHasSyncFailure(source)
      : filter === "checks"
        ? sourceHasCheckFailure(source)
        : filter === "reconciliation"
          ? sourceHasReconciliationFailure(source)
          : filter === "delayed"
            ? sourceIsDelayed(source)
            : sourceHasNoRecords(source))
  );
}

export function dataUpdateAlerts(data: DataStatus | undefined) {
  const sources = data?.sources.filter(sourceHasFailure) ?? [];
  const processing = data?.processing.filter((item) => item.state === "failed") ?? [];
  return {
    sources,
    processing,
    sync: data?.sources.filter(sourceHasSyncFailure) ?? [],
    checks: data?.sources.filter(sourceHasCheckFailure) ?? [],
    reconciliation: data?.sources.filter(sourceHasReconciliationFailure) ?? [],
    delayed: data?.sources.filter(sourceIsDelayed) ?? [],
    records: data?.sources.filter(sourceHasNoRecords) ?? [],
    failed: sources.length + processing.length > 0,
    stale:
      data?.sources.some((source) => source.upstream.stale || source.history?.state === "stale") ??
      false,
    description: [
      ...sources.map((source) => source.name),
      ...processing.map((item) => item.name),
    ].join("、"),
  };
}
