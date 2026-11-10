"""不可变事实内容块与旧/新表示只读兼容；不转换既有事实。"""

from importlib import import_module

from alembic import op

from migrations_v2.baseline_20261028.guards import _function
from migrations_v2.baseline_20261028.schema import build_metadata

revision = "20261110_14"
down_revision = "20261110_13"
branch_labels = depends_on = None
FACTS = {
    "product_daily_facts": ("product_daily",),
    "product_promotion_records": ("product_promotion",),
    "product_region_records": ("product_regions",),
    "product_sales_records": ("jst_shipped_sales", "jst_cancelled_sales"),
    "return_records": ("jst_returns",),
}
TABLES = (
    "fact_content_blocks",
    "fact_content_manifests",
    "fact_content_members",
    *("shared_" + n for n in FACTS),
)
VIEWS = tuple("read_" + n for n in FACTS)
_PREVIOUS = import_module("migrations_v2.versions.20261110_13_database_retention")
PROTECTED_SQL = _PREVIOUS.PROTECTED_SQL.replace(
    " UNION SELECT version_id FROM public.current_datasets",
    " UNION SELECT version_id FROM public.fact_content_manifests"
    " UNION SELECT version_id FROM public.current_datasets",
    1,
)


def sql_list(values):
    return ",".join("'" + v + "'" for v in values)


def context(head):
    return _function(
        "pdd_retention_context",
        """
BEGIN
 RETURN EXISTS(SELECT 1 FROM public.retention_operations o
 JOIN public.retention_operation_items i ON i.operation_id=o.id
 JOIN public.retention_archives a ON a.id=o.archive_id
 WHERE o.id=current_setting('pdd.retention_operation',true)
 AND o.transaction_id=txid_current() AND o.status='running' AND o.kind=action
 AND i.version_id=version_key AND i.table_name=table_key
 AND a.database_name=current_database() AND a.schema_head='"""
        + head
        + """');
END;""",
        arguments="version_key text, table_key text, action text",
        returns="boolean",
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def item_guard(head, protected_sql):
    return _function(
        "pdd_retention_item_guard",
        f"""
    DECLARE action text; archive text; ver_code text; expected_table text; exit_at timestamptz;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'RETENTION_AUDIT_IMMUTABLE'; END IF;
      IF NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'RETENTION_BUSY'; END IF;
      IF current_database() !~ '^pdd_operations_v2_test_[0-9a-f]{{12}}$' THEN
        RAISE EXCEPTION 'RETENTION_PRODUCTION_DISABLED'; END IF;
      SELECT o.kind,o.archive_id INTO action,archive FROM public.retention_operations o
      JOIN public.retention_archives a ON a.id=o.archive_id
      WHERE o.id=NEW.operation_id AND o.transaction_id=txid_current() AND o.status='running'
        AND a.database_name=current_database() AND a.schema_head='{head}'
        AND jsonb_array_length(a.locations)>=2;
      IF action IS NULL THEN RAISE EXCEPTION 'RETENTION_RECEIPT_REQUIRED'; END IF;
      SELECT dataset_code INTO ver_code FROM public.dataset_versions WHERE id=NEW.version_id
        AND state='published' FOR UPDATE;
      expected_table := CASE ver_code WHEN 'product_daily' THEN 'product_daily_facts'
        WHEN 'product_promotion' THEN 'product_promotion_records'
        WHEN 'product_regions' THEN 'product_region_records' END;
      IF expected_table IS DISTINCT FROM NEW.table_name THEN
        RAISE EXCEPTION 'RETENTION_SCOPE'; END IF;
      IF action='offline' THEN
        IF EXISTS(SELECT 1 FROM ({protected_sql}) p WHERE p.id=NEW.version_id)
          OR EXISTS(SELECT 1 FROM public.task_runs WHERE status='running') THEN
          RAISE EXCEPTION 'RETENTION_PROTECTED'; END IF;
        SELECT max(occurred_at) INTO exit_at FROM public.dataset_publications
          WHERE previous_version_id=NEW.version_id;
        IF exit_at IS NULL OR exit_at > statement_timestamp()-interval '24 hours' THEN
          RAISE EXCEPTION 'RETENTION_GRACE'; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.dataset_storage WHERE version_id=NEW.version_id
                      AND state='online') THEN
        RAISE EXCEPTION 'RETENTION_STATE'; END IF;
      ELSE
        IF NOT EXISTS(SELECT 1 FROM public.dataset_storage WHERE version_id=NEW.version_id
          AND state='offline' AND archive_id=archive) THEN
        RAISE EXCEPTION 'RETENTION_STATE'; END IF;
      END IF;
      RETURN NEW;
    END;
""",
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def upgrade():
    op.execute(f"""CREATE TABLE public.fact_content_blocks(
      id text PRIMARY KEY CHECK(id ~ '^[0-9a-f]{{64}}$'),
      table_name text NOT NULL CHECK(table_name IN ({sql_list(FACTS)})),
      dataset_code text NOT NULL CHECK(btrim(dataset_code)<>''),
      row_schema_revision text NOT NULL CHECK(btrim(row_schema_revision)<>''),
      bucket text NOT NULL CHECK(length(bucket) BETWEEN 1 AND 32),
      row_count bigint NOT NULL CHECK(row_count>0),
      state text NOT NULL DEFAULT 'building' CHECK(state IN ('building','sealed')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    op.execute(f"""CREATE TABLE public.fact_content_manifests(
      version_id text PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      table_name text NOT NULL CHECK(table_name IN ({sql_list(FACTS)})),
      format_revision integer NOT NULL DEFAULT 1 CHECK(format_revision=1),
      row_count bigint NOT NULL CHECK(row_count>=0))""")
    op.execute("""CREATE TABLE public.fact_content_members(
      version_id text NOT NULL REFERENCES public.fact_content_manifests(version_id)
        ON DELETE RESTRICT,
      bucket text NOT NULL CHECK(length(bucket) BETWEEN 1 AND 32),
      block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT,
      PRIMARY KEY(version_id,bucket))""")
    op.execute("CREATE INDEX ix_fact_members_block ON public.fact_content_members(block_id)")
    metadata = build_metadata()
    for name in FACTS:
        shared = "shared_" + name
        op.execute(
            f"CREATE TABLE public.{shared}(LIKE public.{name} "
            "INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
        )
        op.execute(f"ALTER TABLE public.{shared} DROP COLUMN id, DROP COLUMN dataset_version_id")
        op.execute(
            f"ALTER TABLE public.{shared} ADD COLUMN block_id text NOT NULL "
            "REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, "
            "ADD PRIMARY KEY(block_id,source_row_key)"
        )
        original = metadata.tables["public." + name]
        for fk in original.foreign_keys:
            if fk.parent.name not in ("id", "dataset_version_id"):
                op.execute(
                    f"ALTER TABLE public.{shared} ADD FOREIGN KEY({fk.parent.name}) "
                    f"REFERENCES {fk.column.table.fullname}({fk.column.name}) ON DELETE RESTRICT"
                )
        cols = []
        for col in original.c:
            if col.name == "id":
                cols.append(
                    "'shared:' || encode(convert_to("
                    "jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id"
                )
            elif col.name == "dataset_version_id":
                cols.append("m.version_id AS dataset_version_id")
            else:
                cols.append("f." + col.name)
        op.execute(
            f"CREATE VIEW public.read_{name} AS SELECT * FROM public.{name} UNION ALL "
            f"SELECT {','.join(cols)} FROM public.fact_content_members m "
            f"JOIN public.{shared} f ON f.block_id=m.block_id"
        )
    op.execute(
        _function(
            "pdd_content_guard",
            """
DECLARE b public.fact_content_blocks; v public.dataset_versions;
        manifest public.fact_content_manifests;
        n bigint; expected_bucket text; key text;
BEGIN
 IF TG_TABLE_NAME='fact_content_blocks' THEN
   IF TG_OP='INSERT' THEN
     IF NEW.state<>'building' THEN RAISE EXCEPTION 'CONTENT_BUILD_STATE'; END IF;
     NEW.created_at:=clock_timestamp(); RETURN NEW;
   END IF;
   IF TG_OP<>'UPDATE' OR OLD.state<>'building' OR NEW.state<>'sealed' OR
      (to_jsonb(NEW)-'state') IS DISTINCT FROM (to_jsonb(OLD)-'state') THEN
     RAISE EXCEPTION 'CONTENT_IMMUTABLE'; END IF;
   EXECUTE format('SELECT count(*) FROM public.%I WHERE block_id=$1','shared_'||NEW.table_name)
     INTO n USING NEW.id;
   IF n<>NEW.row_count THEN RAISE EXCEPTION 'CONTENT_BLOCK_INCOMPLETE'; END IF;
   RETURN NEW;
 END IF;
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'CONTENT_IMMUTABLE'; END IF;
 IF TG_TABLE_NAME LIKE 'shared_%' THEN
   SELECT * INTO b FROM public.fact_content_blocks WHERE id=NEW.block_id FOR UPDATE;
   IF NOT FOUND THEN RAISE EXCEPTION 'CONTENT_BLOCK_SCOPE'; END IF;
   expected_bucket := CASE WHEN TG_TABLE_NAME='shared_product_daily_facts'
     THEN coalesce(to_jsonb(NEW)->>'business_date','?')
     ELSE coalesce(to_jsonb(NEW)->>'period_start','?')||'/'
       ||coalesce(to_jsonb(NEW)->>'period_end','?') END
     ||':'||mod(('x'||substr(md5(NEW.source_row_key),1,8))::bit(32)::bigint,64)::text;
   IF b.state<>'building' OR TG_TABLE_NAME<>'shared_'||b.table_name
      OR expected_bucket<>b.bucket THEN
     RAISE EXCEPTION 'CONTENT_BLOCK_SCOPE'; END IF;
   RETURN NEW;
 END IF;
 SELECT * INTO v FROM public.dataset_versions WHERE id=NEW.version_id FOR UPDATE;
 IF NOT FOUND OR v.state<>'draft' THEN RAISE EXCEPTION 'CONTENT_VERSION_SEALED'; END IF;
 IF TG_TABLE_NAME='fact_content_manifests' THEN
   IF NOT ((NEW.table_name='product_daily_facts' AND v.dataset_code='product_daily') OR
     (NEW.table_name='product_promotion_records' AND v.dataset_code='product_promotion') OR
     (NEW.table_name='product_region_records' AND v.dataset_code='product_regions') OR
     (NEW.table_name='return_records' AND v.dataset_code='jst_returns') OR
     (NEW.table_name='product_sales_records'
       AND v.dataset_code IN ('jst_shipped_sales','jst_cancelled_sales'))) THEN
     RAISE EXCEPTION 'CONTENT_NAMESPACE'; END IF;
   EXECUTE format('SELECT count(*) FROM public.%I WHERE dataset_version_id=$1',NEW.table_name)
     INTO n USING NEW.version_id;
   IF n<>0 THEN RAISE EXCEPTION 'CONTENT_MIXED_FORMAT'; END IF;
 ELSE
   SELECT * INTO manifest FROM public.fact_content_manifests WHERE version_id=NEW.version_id;
   SELECT * INTO b FROM public.fact_content_blocks WHERE id=NEW.block_id;
   IF manifest.version_id IS NULL OR b.id IS NULL OR b.state<>'sealed'
      OR b.bucket<>NEW.bucket OR b.table_name<>manifest.table_name
      OR b.dataset_code<>v.dataset_code OR b.row_schema_revision<>v.row_schema_revision THEN
     RAISE EXCEPTION 'CONTENT_NAMESPACE'; END IF;
 END IF;
 RETURN NEW;
END;""",
        )
    )
    op.execute(
        _function(
            "pdd_content_publish",
            """
DECLARE expected bigint; actual bigint; tab text; duplicated boolean;
BEGIN
 IF TG_TABLE_NAME='dataset_versions' THEN
   IF EXISTS(SELECT 1 FROM public.fact_content_manifests WHERE version_id=NEW.id)
      AND (NEW.dataset_code IS DISTINCT FROM OLD.dataset_code
        OR NEW.row_schema_revision IS DISTINCT FROM OLD.row_schema_revision) THEN
     RAISE EXCEPTION 'CONTENT_NAMESPACE'; END IF;
   IF NEW.state NOT IN ('validated','published') THEN RETURN NEW; END IF;
   SELECT row_count,table_name INTO expected,tab FROM public.fact_content_manifests
     WHERE version_id=NEW.id;
   IF NOT FOUND THEN RETURN NEW; END IF;
   IF EXISTS(SELECT 1 FROM public.fact_content_members m
     JOIN public.fact_content_blocks b ON b.id=m.block_id WHERE m.version_id=NEW.id
       AND (b.dataset_code<>NEW.dataset_code
         OR b.row_schema_revision<>NEW.row_schema_revision)) THEN
     RAISE EXCEPTION 'CONTENT_NAMESPACE'; END IF;
   SELECT coalesce(sum(b.row_count),0) INTO actual FROM public.fact_content_members m
     JOIN public.fact_content_blocks b ON b.id=m.block_id AND b.state='sealed'
     WHERE m.version_id=NEW.id;
   IF actual<>expected THEN RAISE EXCEPTION 'CONTENT_MANIFEST_INCOMPLETE'; END IF;
   EXECUTE format('SELECT EXISTS(SELECT f.source_row_key FROM public.%I f '
     ||'JOIN public.fact_content_members m ON m.block_id=f.block_id '
     ||'WHERE m.version_id=$1 GROUP BY f.source_row_key HAVING count(*)>1)', 'shared_'||tab)
     INTO duplicated USING NEW.id;
   IF duplicated THEN RAISE EXCEPTION 'CONTENT_DUPLICATE_KEY'; END IF;
   -- 原日表部分唯一索引的语义作用于整版，不能仅在单块内约束。
   IF tab='product_daily_facts' AND EXISTS(
     SELECT f.listing_id,f.business_date FROM public.shared_product_daily_facts f
     JOIN public.fact_content_members m ON m.block_id=f.block_id
     WHERE m.version_id=NEW.id AND f.listing_id IS NOT NULL AND f.business_date IS NOT NULL
     GROUP BY f.listing_id,f.business_date HAVING count(*)>1
   ) THEN RAISE EXCEPTION 'CONTENT_DUPLICATE_LISTING_DATE'; END IF;
 ELSE
   PERFORM 1 FROM public.dataset_versions WHERE id=NEW.dataset_version_id FOR UPDATE;
   IF EXISTS(SELECT 1 FROM public.fact_content_manifests
      WHERE version_id=NEW.dataset_version_id) THEN
     RAISE EXCEPTION 'CONTENT_MIXED_FORMAT'; END IF;
 END IF;
 RETURN NEW;
END;""",
        )
    )
    op.execute(
        _function(
            "pdd_content_complete",
            """
BEGIN
 IF EXISTS(SELECT 1 FROM public.fact_content_blocks WHERE id=NEW.id AND state<>'sealed') THEN
   RAISE EXCEPTION 'CONTENT_BLOCK_INCOMPLETE'; END IF;
 RETURN NULL;
END;""",
        )
    )
    for name in TABLES:
        op.execute(
            f"CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{name} "
            "FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard()"
        )
        op.execute(
            f"CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.{name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate()"
        )
        for role in ("pdd_app", "pdd_ingest"):
            op.execute(f"GRANT SELECT ON public.{name} TO {role}")
        op.execute(f"GRANT INSERT ON public.{name} TO pdd_ingest")
    op.execute("GRANT UPDATE(state) ON public.fact_content_blocks TO pdd_ingest")
    op.execute(
        "CREATE CONSTRAINT TRIGGER pdd_content_complete AFTER INSERT ON public.fact_content_blocks "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.pdd_content_complete()"
    )
    op.execute(
        "CREATE TRIGGER pdd_content_publish BEFORE UPDATE ON public.dataset_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish()"
    )
    for name in FACTS:
        op.execute(
            f"CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.{name} "
            "FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish()"
        )
        for role in ("pdd_app", "pdd_ingest"):
            op.execute(f"GRANT SELECT ON public.read_{name} TO {role}")
    op.execute(context(revision))
    op.execute(item_guard(revision, PROTECTED_SQL))


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.fact_content_blocks) OR
         EXISTS(SELECT 1 FROM public.fact_content_manifests) OR
         EXISTS(SELECT 1 FROM public.retention_archives WHERE schema_head='20261110_14') THEN
        RAISE EXCEPTION 'CONTENT_DOWNGRADE_REQUIRES_EMPTY'; END IF;
    END $$""")
    for name in FACTS:
        op.execute(f"DROP VIEW public.read_{name}")
        op.execute(f"DROP TRIGGER pdd_content_format ON public.{name}")
    op.execute("DROP TRIGGER pdd_content_publish ON public.dataset_versions")
    for name in reversed(TABLES):
        op.execute(f"DROP TABLE public.{name}")
    for name in ("complete", "publish", "guard"):
        op.execute(f"DROP FUNCTION public.pdd_content_{name}()")
    op.execute(context(down_revision))
    op.execute(item_guard(down_revision, _PREVIOUS.PROTECTED_SQL))
