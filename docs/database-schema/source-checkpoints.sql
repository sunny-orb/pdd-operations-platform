-- 由20261103_10_source_checkpoints.py离线生成；只经Alembic执行。
CREATE TABLE public.source_checkpoints (
      source_id TEXT PRIMARY KEY CHECK (btrim(source_id) <> ''),
      checked_at TIMESTAMPTZ NOT NULL,
      check_status TEXT NOT NULL CHECK (check_status IN ('checking','succeeded','failed')),
      observation JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(observation)='object'),
      platform_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(platform_snapshot)='object'),
      observed_fingerprint TEXT,
      accepted_fingerprint TEXT,
      accepted_at TIMESTAMPTZ,
      error_code TEXT,
      duration_ms BIGINT NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
      CHECK (check_status <> 'succeeded' OR observed_fingerprint IS NOT NULL),
      CHECK ((accepted_fingerprint IS NULL) = (accepted_at IS NULL))
    );

COMMENT ON TABLE public.source_checkpoints IS '实际来源的最近检查及已接收变化标记，不证明业务完整';

GRANT SELECT ON public.source_checkpoints TO pdd_app;

GRANT SELECT,INSERT,UPDATE ON public.source_checkpoints TO pdd_ingest;
