"""全国天气发布文档扩展；复用真实来源版本、任务和当前指针，不改30表结构。

回退先停天气任务；有归档拒绝破坏性降级，应撤运行权限后另做保留方案。
"""

from alembic import op

revision = "20261028_07"
down_revision = "20261028_06"
branch_labels = None
depends_on = None

UPGRADE_SQL = (
    """
CREATE TABLE public.weather_publications (
 id TEXT PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
 issue_date DATE NOT NULL,
 revision_no BIGINT NOT NULL CHECK (revision_no >= 1),
 source_system TEXT NOT NULL CHECK (source_system = 'nmc_official'),
 content_sha256 TEXT NOT NULL UNIQUE CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
 parser_revision TEXT NOT NULL CHECK (btrim(parser_revision) <> ''),
 document JSONB NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(issue_date, revision_no),
 CONSTRAINT ck_weather_document_closed CHECK (
  jsonb_typeof(document) = 'object'
  AND document ?& ARRAY['title','issue_time','focus_regions','today_rain','today_temperature',
      'next_7_days','sources','forecast_url','reference_url','group_card_link','note']
  AND document - ARRAY['title','issue_time','focus_regions','today_rain','today_temperature',
      'next_7_days','sources','forecast_url','reference_url','group_card_link','note'] = '{}'::jsonb
  AND jsonb_typeof(document->'title') = 'string'
  AND jsonb_typeof(document->'issue_time') = 'string'
  AND jsonb_typeof(document->'focus_regions') = 'array'
  AND jsonb_typeof(document->'today_rain') = 'object'
  AND jsonb_typeof(document->'today_temperature') = 'object'
  AND jsonb_typeof(document->'next_7_days') = 'array'
  AND jsonb_typeof(document->'sources') = 'array'
  AND jsonb_array_length(document->'sources') = 2
  AND document->'group_card_link' = 'null'::jsonb
  AND jsonb_typeof(document->'note') = 'string'
 )
)
""",
    "CREATE INDEX ix_weather_publications_history ON public.weather_publications "
    "(issue_date DESC, revision_no DESC, id)",
    "COMMENT ON TABLE public.weather_publications IS 'NMC两官文及确定解释的不可变发布文档'",
    """
CREATE FUNCTION public.pdd_weather_document_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $body$
DECLARE parent public.dataset_versions%ROWTYPE;
BEGIN
 IF TG_OP <> 'INSERT' THEN
  RAISE EXCEPTION '天气发布文档不可更改或删除' USING ERRCODE = '23514';
 END IF;
 SELECT * INTO parent FROM public.dataset_versions WHERE id=NEW.id FOR SHARE;
 IF NOT FOUND OR parent.state <> 'draft' OR parent.dataset_code <> 'weather_publication'
 OR parent.origin_kind <> 'source' OR parent.source_system <> 'nmc_official'
 OR parent.period_start IS DISTINCT FROM NEW.issue_date
 OR parent.period_end IS DISTINCT FROM NEW.issue_date + 6
 OR parent.row_schema_revision IS DISTINCT FROM NEW.parser_revision THEN
  RAISE EXCEPTION '天气文档须写入相容的draft来源版本' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END $body$
""",
    "CREATE TRIGGER weather_document_immutable BEFORE INSERT OR UPDATE OR DELETE "
    "ON public.weather_publications FOR EACH ROW "
    "EXECUTE FUNCTION public.pdd_weather_document_guard()",
    "CREATE TRIGGER weather_document_no_truncate BEFORE TRUNCATE "
    "ON public.weather_publications FOR EACH STATEMENT "
    "EXECUTE FUNCTION public.pdd_v2_forbid_truncate()",
    """
CREATE FUNCTION public.pdd_weather_parent_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $body$
BEGIN
 IF OLD.dataset_code = 'weather_publication' AND NEW.dataset_code <> OLD.dataset_code
 AND EXISTS(SELECT 1 FROM public.weather_publications WHERE id=OLD.id) THEN
  RAISE EXCEPTION '天气文档父版本用途不可改变' USING ERRCODE = '23514';
 END IF;
 IF NEW.dataset_code = 'weather_publication' AND NEW.state = 'validated' AND NOT EXISTS (
  SELECT 1 FROM public.weather_publications w WHERE w.id=NEW.id
  AND NEW.origin_kind='source' AND NEW.source_system='nmc_official'
  AND w.issue_date=NEW.period_start AND NEW.period_end=w.issue_date+6
  AND w.parser_revision=NEW.row_schema_revision
  AND NEW.content_fingerprint='nmc:' || w.content_sha256
 ) THEN
  RAISE EXCEPTION '天气父版本须绑定相容的真实文档才能封版' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END $body$
""",
    "CREATE TRIGGER weather_parent_content BEFORE UPDATE ON public.dataset_versions "
    "FOR EACH ROW EXECUTE FUNCTION public.pdd_weather_parent_guard()",
    "GRANT SELECT ON public.weather_publications TO pdd_app",
    "GRANT SELECT, INSERT ON public.weather_publications TO pdd_ingest",
)


def upgrade():
    for statement in UPGRADE_SQL:
        op.execute(statement)


def downgrade():
    op.execute("LOCK TABLE public.weather_publications IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $empty$ BEGIN
      IF EXISTS(SELECT 1 FROM public.weather_publications) THEN
       RAISE EXCEPTION '拒绝回退：天气发布文档含记录';
      END IF; END $empty$""")
    op.execute("DROP TABLE public.weather_publications")
    op.execute("DROP TRIGGER weather_parent_content ON public.dataset_versions")
    op.execute("DROP FUNCTION public.pdd_weather_parent_guard()")
    op.execute("DROP FUNCTION public.pdd_weather_document_guard()")
