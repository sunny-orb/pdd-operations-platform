-- Alembic 20261104_11 派生审阅SQL；运行请使用迁移入口。
BEGIN;
ALTER TABLE public.source_checkpoints ADD COLUMN observed_at TIMESTAMPTZ, ADD COLUMN web_updated_at TEXT, ADD COLUMN web_checked_at TIMESTAMPTZ, ADD CONSTRAINT source_web_time_pair CHECK ((web_updated_at IS NULL) = (web_checked_at IS NULL)), ADD CONSTRAINT source_web_time_format CHECK (web_updated_at IS NULL OR web_updated_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$');
UPDATE public.source_checkpoints SET observed_at=checked_at WHERE check_status='succeeded';
COMMIT;
