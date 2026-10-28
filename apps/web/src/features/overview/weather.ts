import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { ApiError, getJson } from "../../shared/api/client";
const publicationOption = z.object({
  id: z.string(),
  issue_date: z.string(),
  revision_no: z.number(),
  issue_time: z.string(),
});
export const weatherMeta = z.object({
  data_version: z.string(),
  status: z.enum(["ready", "partial", "source_unavailable"]),
  default_publication_id: z.string().nullable(),
  publications: z.array(publicationOption),
  last_success_at: z.string().nullable(),
  last_attempt_at: z.string().nullable(),
  last_attempt_status: z.string().nullable(),
  error_code: z.string().nullable(),
});
const weatherBlock = z.object({
  regions: z.array(z.string()),
  detail: z.string(),
  status: z.string(),
});
export const weatherDocument = z.object({
  title: z.string(),
  issue_time: z.string(),
  focus_regions: z.array(z.string()),
  today_rain: weatherBlock,
  today_temperature: weatherBlock,
  next_7_days: z.array(
    z.object({
      period: z.string(),
      regions: z.array(z.string()),
      detail: z.string(),
      status: z.string(),
    }),
  ),
  sources: z.array(
    z.object({ name: z.string(), url: z.string(), issue_time: z.string(), text: z.string() }),
  ),
  forecast_url: z.string().nullable(),
  reference_url: z.string().nullable(),
  group_card_link: z.null(),
  note: z.string(),
});
const publication = z.object({
  id: z.string(),
  issue_date: z.string(),
  revision_no: z.number(),
  document: weatherDocument,
});
export type WeatherDocument = z.infer<typeof weatherDocument>;
/** 默认选择跟随最新发布；用户明确选择后保留不可变发布ID，天气不改变经营范围。 */
export function useWeather() {
  const [selected, setSelected] = useState<z.infer<typeof publicationOption> | null>(null);
  const meta = useQuery({
    queryKey: ["overview", "weather", "meta"],
    queryFn: async ({ signal }) =>
      z.object({ data: weatherMeta }).parse(await getJson("/api/v1/weather/meta", signal)).data,
    retry: false,
    refetchInterval: 45000,
    refetchOnWindowFocus: "always",
  });
  const version = meta.data?.data_version ?? "";
  const id = selected?.id ?? meta.data?.default_publication_id ?? "";
  const doc = useQuery({
    queryKey: ["overview", "weather", "publication", version, id],
    queryFn: async ({ signal }) => {
      const r = z
        .object({ data: publication.nullable(), meta: weatherMeta })
        .parse(
          await getJson(
            `/api/v1/weather/publication?${new URLSearchParams({ publication_id: id, data_version: version })}`,
            signal,
          ),
        );
      if (r.meta.data_version !== version) throw new ApiError("天气发布版本变化", 409);
      return r.data;
    },
    enabled: Boolean(version && id) && meta.isSuccess,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const retried = useRef(new Set<string>());
  useEffect(() => {
    if (
      doc.error instanceof ApiError &&
      doc.error.status === 409 &&
      !retried.current.has(version)
    ) {
      retried.current.add(version);
      void meta.refetch();
    }
  }, [doc.error, version, meta]);
  const options = [...(meta.data?.publications ?? [])];
  if (selected && !options.some((p) => p.id === selected.id)) options.push(selected);
  const select = (next: string) => {
    if (next === "latest") {
      setSelected(null);
      return;
    }
    const option = options.find((p) => p.id === next);
    if (option) setSelected(option);
  };
  const refresh = () => {
    void meta.refetch().then((next) => {
      if (next.data?.data_version === version) void doc.refetch();
    });
  };
  return {
    meta: meta.data,
    publication: doc.data,
    selectedId: id,
    selection: selected?.id ?? "latest",
    options,
    select,
    refresh,
    loading: meta.isPending || (Boolean(id) && doc.isPending),
    refreshing: meta.isFetching || doc.isFetching,
    error: meta.error?.message ?? doc.error?.message ?? null,
  };
}
export type WeatherState = ReturnType<typeof useWeather>;
