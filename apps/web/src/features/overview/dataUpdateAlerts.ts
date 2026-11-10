import type { DataStatus, SourceStatus } from "./sourceStatusApi";

/** 汇总已知失败，不把等待、无记录或旧可用版本当成异常；同来源多个用途只计一次。 */
export function sourceUpdateFailed(source: SourceStatus) {
  return (
    source.upstream.state === "failed" ||
    source.platform_state === "failed" ||
    source.datasets.some((dataset) =>
      ["failed", "abandoned", "cancelled"].includes(dataset.attempt_status ?? ""),
    )
  );
}

export function sourceHasFailure(source: SourceStatus) {
  const reconciliation = source.reconciliation;
  return (
    sourceUpdateFailed(source) ||
    reconciliation?.status === "failed" ||
    (reconciliation?.status === "running" &&
      Boolean(reconciliation.error_code) &&
      reconciliation.error_code !== "MAINTENANCE_DEFERRED" &&
      reconciliation.error_code !== "DATASET_RESTORE_REQUIRED")
  );
}

export function dataUpdateAlerts(data: DataStatus | undefined) {
  const sources = data?.sources.filter(sourceHasFailure) ?? [];
  const processing = data?.processing.filter((item) => item.state === "failed") ?? [];
  return {
    sources,
    processing,
    failed: sources.length + processing.length > 0,
    stale: data?.sources.some((source) => source.upstream.stale) ?? false,
    description: [
      ...sources.map((source) => source.name),
      ...processing.map((item) => item.name),
    ].join("、"),
  };
}
