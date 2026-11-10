"""数据库副本离线登记与发布保护；不删除业务数据，不启用生产维护。"""

from alembic import op

revision = "20261110_13"
down_revision = "20261104_12"
branch_labels = None
depends_on = None

TABLES = (
    "retention_archives",
    "dataset_storage",
    "dataset_publications",
    "retention_operations",
    "retention_operation_items",
)
TARGETS = ("product_daily_facts", "product_promotion_records", "product_region_records")

PROTECTED_SQL = """
WITH RECURSIVE roots(id) AS (
 SELECT id FROM public.dataset_versions
 WHERE dataset_code NOT IN ('product_daily','product_promotion','product_regions')
    OR state <> 'published'
 UNION SELECT version_id FROM public.current_datasets
 UNION SELECT s.version_id FROM public.dataset_storage s WHERE keep_online
 UNION SELECT p.previous_version_id FROM public.dataset_publications p
 JOIN public.current_datasets c ON c.dataset_code=p.dataset_code AND c.revision=p.revision
 WHERE p.previous_version_id IS NOT NULL
), protected(id) AS (
 SELECT id FROM roots UNION
 SELECT i.input_version_id FROM public.version_inputs i JOIN protected p ON p.id=i.output_version_id
) SELECT id FROM protected
"""


def function(name, args, returns, body, *, definer=False):
    return (
        f"CREATE FUNCTION public.{name}({args}) RETURNS {returns} LANGUAGE plpgsql "
        f"SECURITY {'DEFINER' if definer else 'INVOKER'} "
        "SET search_path=pg_catalog,public AS $retention$\n" + body.strip() + "\n$retention$"
    )


def upgrade():
    op.execute("""CREATE TABLE public.retention_archives (
      id text PRIMARY KEY, database_name text NOT NULL, schema_head text NOT NULL,
      format_revision integer NOT NULL CHECK(format_revision=1),
      cipher_sha256 text NOT NULL CHECK(cipher_sha256 ~ '^[0-9a-f]{64}$'),
      manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
      locations jsonb NOT NULL CHECK(jsonb_typeof(locations)='array'),
      verified_at timestamptz NOT NULL, restored_at timestamptz NOT NULL)""")
    op.execute("""CREATE TABLE public.dataset_storage (
      version_id text PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      state text NOT NULL DEFAULT 'online' CHECK(state IN ('online','offline')),
      archive_id text REFERENCES public.retention_archives(id) ON DELETE RESTRICT,
      keep_online boolean NOT NULL DEFAULT false,
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(state='online' OR archive_id IS NOT NULL),
      CHECK(NOT keep_online OR state='online'))""")
    op.execute("""CREATE TABLE public.dataset_publications (
      id text PRIMARY KEY, dataset_code text NOT NULL, revision bigint NOT NULL CHECK(revision>=0),
      previous_version_id text REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      version_id text NOT NULL REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(dataset_code,revision))""")
    for field in ("previous_version_id", "version_id"):
        op.execute(f"CREATE INDEX ix_publications_{field} ON public.dataset_publications({field})")
    op.execute("CREATE INDEX ix_storage_archive ON public.dataset_storage(archive_id)")
    op.execute("""CREATE TABLE public.retention_operations (
      id text PRIMARY KEY, archive_id text NOT NULL
      REFERENCES public.retention_archives(id) ON DELETE RESTRICT,
      kind text NOT NULL CHECK(kind IN ('offline','restore')),
      plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
      policy_revision text NOT NULL, code_revision text NOT NULL, transaction_id bigint NOT NULL,
      status text NOT NULL CHECK(status IN ('running','succeeded')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
      CHECK((status='running' AND finished_at IS NULL) OR
            (status='succeeded' AND finished_at>=created_at)))""")
    op.execute("""CREATE TABLE public.retention_operation_items (
      operation_id text REFERENCES public.retention_operations(id) ON DELETE RESTRICT,
      version_id text REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      table_name text CHECK(table_name IN
        ('product_daily_facts','product_promotion_records','product_region_records')),
      row_count bigint NOT NULL CHECK(row_count>=0),
      content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
      PRIMARY KEY(operation_id,version_id,table_name))""")
    op.execute(
        "CREATE INDEX ix_retention_items_version ON public.retention_operation_items(version_id)"
    )
    op.execute(
        "INSERT INTO public.dataset_storage(version_id) SELECT id FROM public.dataset_versions"
    )
    op.execute(
        function(
            "pdd_retention_online",
            "version_key text",
            "void",
            """
    BEGIN
      IF EXISTS(WITH RECURSIVE deps(id) AS (
        SELECT version_key UNION SELECT i.input_version_id FROM public.version_inputs i
        JOIN deps d ON d.id=i.output_version_id)
        SELECT 1 FROM deps d LEFT JOIN public.dataset_storage s ON s.version_id=d.id
        WHERE s.state IS DISTINCT FROM 'online') THEN
        RAISE EXCEPTION 'DATASET_RESTORE_REQUIRED' USING ERRCODE='23514';
      END IF;
    END;""",
        )
    )
    op.execute(
        function(
            "pdd_retention_register",
            "",
            "trigger",
            """
    BEGIN
      INSERT INTO public.dataset_storage(version_id) VALUES(NEW.id);
      RETURN NEW;
    END;""",
            definer=True,
        )
    )
    op.execute(
        "CREATE TRIGGER pdd_retention_register AFTER INSERT ON public.dataset_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_register()"
    )
    op.execute(
        function(
            "pdd_retention_publish",
            "",
            "trigger",
            """
    BEGIN
      PERFORM pg_advisory_xact_lock_shared(hashtext('pdd-database-maintenance'));
      IF TG_TABLE_NAME='current_datasets' THEN
        PERFORM public.pdd_retention_online(NEW.version_id);
      ELSIF TG_TABLE_NAME='version_inputs' THEN
        PERFORM public.pdd_retention_online(NEW.input_version_id);
      ELSIF NEW.state IN ('validated','published') THEN
        PERFORM public.pdd_retention_online(NEW.id);
      END IF;
      RETURN NEW;
    END;""",
        )
    )
    for table in ("dataset_versions", "version_inputs", "current_datasets"):
        events = "UPDATE" if table == "dataset_versions" else "INSERT OR UPDATE"
        op.execute(
            f"CREATE TRIGGER pdd_retention_publish BEFORE {events} ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_publish()"
        )
    op.execute(
        function(
            "pdd_retention_publication",
            "",
            "trigger",
            """
    BEGIN
      IF TG_OP='INSERT' OR NEW.version_id IS DISTINCT FROM OLD.version_id THEN
        INSERT INTO public.dataset_publications(id,dataset_code,revision,
          previous_version_id,version_id)
        VALUES(NEW.dataset_code || ':' || NEW.revision,NEW.dataset_code,NEW.revision,
          CASE WHEN TG_OP='UPDATE' THEN OLD.version_id ELSE NULL END, NEW.version_id);
      END IF;
      RETURN NEW;
    END;""",
            definer=True,
        )
    )
    op.execute(
        "CREATE TRIGGER pdd_retention_publication AFTER INSERT OR UPDATE "
        "ON public.current_datasets FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_retention_publication()"
    )
    op.execute(
        function(
            "pdd_retention_context",
            "version_key text, table_key text, action text",
            "boolean",
            """
    BEGIN
      RETURN EXISTS(SELECT 1 FROM public.retention_operations o
        JOIN public.retention_operation_items i ON i.operation_id=o.id
        JOIN public.retention_archives a ON a.id=o.archive_id
        WHERE o.id=current_setting('pdd.retention_operation',true)
          AND o.transaction_id=txid_current() AND o.status='running' AND o.kind=action
          AND i.version_id=version_key AND i.table_name=table_key
          AND a.database_name=current_database() AND a.schema_head='20261110_13');
    END;""",
        )
    )
    # 仅三个白名单表换到受控函数；原函数和其余保护原样保留。
    op.execute(
        function(
            "pdd_retention_content",
            "",
            "trigger",
            """
    DECLARE key text;
    BEGIN
      key := CASE WHEN TG_OP='DELETE' THEN OLD.dataset_version_id ELSE NEW.dataset_version_id END;
      IF TG_OP='DELETE' AND public.pdd_retention_context(key,TG_TABLE_NAME,'offline') THEN
        RETURN OLD;
      ELSIF TG_OP='INSERT' AND public.pdd_retention_context(key,TG_TABLE_NAME,'restore') THEN
        RETURN NEW;
      END IF;
      IF TG_OP<>'INSERT' THEN
        PERFORM public.pdd_v2_assert_draft_version(OLD.dataset_version_id); END IF;
      IF TG_OP<>'DELETE' THEN
        PERFORM public.pdd_v2_assert_draft_version(NEW.dataset_version_id); END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END;""",
        )
    )
    for table in TARGETS:
        op.execute(f"DROP TRIGGER pdd_v2_trg_{table}_content ON public.{table}")
        op.execute(
            f"CREATE TRIGGER pdd_v2_trg_{table}_content BEFORE INSERT OR UPDATE OR DELETE "
            f"ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_content()"
        )
    op.execute(
        function(
            "pdd_retention_item_guard",
            "",
            "trigger",
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
        AND a.database_name=current_database() AND a.schema_head='20261110_13'
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
        IF EXISTS(SELECT 1 FROM ({PROTECTED_SQL}) p WHERE p.id=NEW.version_id)
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
    END;""",
        )
    )
    op.execute(
        "CREATE TRIGGER pdd_retention_item_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.retention_operation_items FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_retention_item_guard()"
    )
    op.execute(
        function(
            "pdd_retention_storage_guard",
            "",
            "trigger",
            """
    DECLARE action text; tab text;
    BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'RETENTION_STATE'; END IF;
      IF NEW.version_id IS DISTINCT FROM OLD.version_id THEN
        RAISE EXCEPTION 'RETENTION_STATE'; END IF;
      IF NEW.keep_online AND NEW.state <> 'online' THEN
        RAISE EXCEPTION 'RETENTION_PIN_REQUIRES_ONLINE'; END IF;
      IF NEW.state IS DISTINCT FROM OLD.state OR NEW.archive_id IS DISTINCT FROM OLD.archive_id THEN
        action := CASE NEW.state WHEN 'offline' THEN 'offline' ELSE 'restore' END;
        SELECT i.table_name INTO tab FROM public.retention_operation_items i
          WHERE i.operation_id=current_setting('pdd.retention_operation',true)
          AND i.version_id=NEW.version_id;
        IF tab IS NULL OR NOT public.pdd_retention_context(NEW.version_id,tab,action) THEN
          RAISE EXCEPTION 'RETENTION_STATE'; END IF;
        IF NEW.state='offline' THEN
          EXECUTE format('SELECT 1 FROM public.%I WHERE dataset_version_id=$1 LIMIT 1',tab)
            INTO tab USING NEW.version_id;
          IF tab IS NOT NULL THEN RAISE EXCEPTION 'RETENTION_PARTIAL'; END IF;
        END IF;
        NEW.changed_at:=clock_timestamp();
      END IF;
      RETURN NEW;
    END;""",
        )
    )
    op.execute(
        "CREATE TRIGGER pdd_retention_storage_guard BEFORE UPDATE OR DELETE "
        "ON public.dataset_storage FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_retention_storage_guard()"
    )
    op.execute(
        function(
            "pdd_retention_operation_guard",
            "",
            "trigger",
            """
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'RETENTION_AUDIT_IMMUTABLE'; END IF;
      IF NEW.transaction_id <> txid_current() THEN RAISE EXCEPTION 'RETENTION_TRANSACTION'; END IF;
      IF TG_OP='INSERT' AND NEW.status <> 'running' THEN
        RAISE EXCEPTION 'RETENTION_TRANSACTION'; END IF;
      IF TG_OP='UPDATE' AND (OLD.status <> 'running' OR NEW.status <> 'succeeded' OR
        (to_jsonb(NEW)-ARRAY['status','finished_at']) IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['status','finished_at'])) THEN
        RAISE EXCEPTION 'RETENTION_AUDIT_IMMUTABLE'; END IF;
      RETURN NEW;
    END;""",
        )
    )
    op.execute(
        "CREATE TRIGGER pdd_retention_operation_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.retention_operations FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_retention_operation_guard()"
    )
    op.execute(
        function(
            "pdd_retention_complete",
            "",
            "trigger",
            """
    DECLARE operation public.retention_operations; item public.retention_operation_items;
            actual bigint; target_state text;
    BEGIN
      SELECT * INTO operation FROM public.retention_operations WHERE id=NEW.id;
      IF operation.status <> 'succeeded' OR (SELECT count(*) FROM public.retention_operation_items
          WHERE operation_id=NEW.id) <> 1 THEN RAISE EXCEPTION 'RETENTION_INCOMPLETE'; END IF;
      SELECT * INTO item FROM public.retention_operation_items WHERE operation_id=NEW.id;
      target_state := CASE operation.kind WHEN 'offline' THEN 'offline' ELSE 'online' END;
      IF NOT EXISTS(SELECT 1 FROM public.dataset_storage WHERE version_id=item.version_id
        AND state=target_state AND archive_id=operation.archive_id) THEN
        RAISE EXCEPTION 'RETENTION_INCOMPLETE'; END IF;
      EXECUTE format('SELECT count(*) FROM public.%I WHERE dataset_version_id=$1',item.table_name)
        INTO actual USING item.version_id;
      IF actual <> (CASE operation.kind WHEN 'offline' THEN 0 ELSE item.row_count END) THEN
        RAISE EXCEPTION 'RETENTION_INCOMPLETE'; END IF;
      RETURN NEW;
    END;""",
        )
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER pdd_retention_complete AFTER INSERT "
        "ON public.retention_operations DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_complete()"
    )
    for table in ("retention_archives", "dataset_publications"):
        op.execute(
            f"CREATE TRIGGER pdd_retention_immutable BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_rule_guard()"
        )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate()"
        )
        op.execute(f"GRANT SELECT ON public.{table} TO pdd_app")
        op.execute(f"GRANT SELECT ON public.{table} TO pdd_ingest")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.dataset_storage WHERE state='offline') OR
         EXISTS(SELECT 1 FROM public.retention_archives) OR
         EXISTS(SELECT 1 FROM public.retention_operations) THEN
        RAISE EXCEPTION '离线数据或归档证据仍存在，不允许回退'; END IF;
      END $$""")
    for table in ("dataset_versions", "version_inputs", "current_datasets"):
        op.execute(f"DROP TRIGGER pdd_retention_publish ON public.{table}")
    op.execute("DROP TRIGGER pdd_retention_register ON public.dataset_versions")
    op.execute("DROP TRIGGER pdd_retention_publication ON public.current_datasets")
    for table in TARGETS:
        op.execute(f"DROP TRIGGER pdd_v2_trg_{table}_content ON public.{table}")
        op.execute(
            f"CREATE TRIGGER pdd_v2_trg_{table}_content BEFORE INSERT OR UPDATE OR DELETE "
            f"ON public.{table} FOR EACH ROW "
            "EXECUTE FUNCTION public.pdd_v2_content_guard()"
        )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    for name, args in (
        ("complete", ""),
        ("operation_guard", ""),
        ("storage_guard", ""),
        ("item_guard", ""),
        ("content", ""),
        ("context", "text,text,text"),
        ("publication", ""),
        ("publish", ""),
        ("register", ""),
        ("online", "text"),
    ):
        op.execute(f"DROP FUNCTION public.pdd_retention_{name}({args})")
