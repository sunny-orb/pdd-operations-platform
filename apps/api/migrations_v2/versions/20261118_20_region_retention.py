"""地区无用副本有界证明/回收及同版本重建；不复制事实、不改变发布身份。"""

from alembic import op

revision = "20261118_20"
down_revision = "20261116_19"
branch_labels = depends_on = None

PROTECTED_SQL = """
WITH RECURSIVE roots(id) AS (
 SELECT id FROM public.dataset_versions WHERE dataset_code<>'product_regions' OR state<>'published'
 UNION SELECT version_id FROM public.current_datasets
 UNION SELECT version_id FROM public.dataset_storage WHERE keep_online
 UNION SELECT p.previous_version_id FROM public.dataset_publications p
 JOIN public.current_datasets c ON c.dataset_code=p.dataset_code AND c.revision=p.revision
 WHERE p.previous_version_id IS NOT NULL
 UNION SELECT replacement_version_id FROM public.region_retirements
 WHERE status IN ('verifying','reclaiming','rebuilding')
), protected(id) AS (
 SELECT id FROM roots UNION SELECT i.input_version_id FROM public.version_inputs i
 JOIN protected p ON p.id=i.output_version_id
) SELECT id FROM protected
"""


def function(name, args, returns, body, *, definer=False):
    op.execute(
        f"CREATE FUNCTION public.{name}({args}) RETURNS {returns} LANGUAGE plpgsql "
        f"SECURITY {'DEFINER' if definer else 'INVOKER'} "
        "SET search_path=pg_catalog,public AS $region$\n" + body + "\n$region$"
    )


def patch_guard(name, before, after):
    """固定旧函数片段升级；离线生成与线上执行共用，漂移立即拒绝。"""
    op.execute(
        "DO $patch$ DECLARE definition text; BEGIN "
        f"SELECT pg_get_functiondef('public.{name}()'::regprocedure) INTO definition; "
        f"IF length(definition)-length(replace(definition,$old${before}$old$,'')) "
        f"<>length($old${before}$old$) THEN RAISE EXCEPTION 'REGION_GUARD_DRIFT'; END IF; "
        f"EXECUTE replace(definition,$old${before}$old$,$new${after}$new$); END $patch$"
    )


STORAGE_BEFORE = """      IF NEW.keep_online AND NEW.state <> 'online' THEN"""
STORAGE_AFTER = """      IF NEW.state IN ('reclaiming','reclaimed','rebuilding') OR
         OLD.state IN ('reclaiming','reclaimed','rebuilding') THEN
        IF NEW.archive_id IS DISTINCT FROM OLD.archive_id OR NEW.keep_online OR
           NEW.keep_online IS DISTINCT FROM OLD.keep_online OR
           NOT public.pdd_region_internal('lifecycle') OR
           NOT public.pdd_region_context(NEW.version_id) OR NOT (
             (OLD.state='online' AND NEW.state='reclaiming') OR
             (OLD.state='reclaiming' AND NEW.state='reclaimed') OR
             (OLD.state IN ('reclaiming','reclaimed') AND NEW.state='rebuilding') OR
             (OLD.state='rebuilding' AND NEW.state='online')) THEN
          RAISE EXCEPTION 'REGION_STATE'; END IF;
        NEW.changed_at:=clock_timestamp(); RETURN NEW;
      END IF;
      IF NEW.keep_online AND NEW.state <> 'online' THEN"""
CONTENT_BEFORE = (
    """      IF TG_OP='DELETE' AND public.pdd_retention_context(key,TG_TABLE_NAME,'offline') THEN"""
)
CONTENT_AFTER = """      IF TG_OP='DELETE' AND TG_TABLE_NAME='product_region_records'
        AND public.pdd_region_internal('lifecycle') AND public.pdd_region_context(key) AND EXISTS(
          SELECT 1 FROM public.dataset_storage WHERE version_id=key
          AND state IN ('reclaiming','rebuilding')) THEN RETURN OLD; END IF;
      IF TG_OP='DELETE' AND public.pdd_retention_context(key,TG_TABLE_NAME,'offline') THEN"""
SHARED_BEFORE = """ IF TG_TABLE_NAME='fact_content_blocks' THEN"""
SHARED_AFTER = """ IF TG_OP='DELETE' THEN
   IF TG_TABLE_NAME IN ('fact_content_members','fact_content_manifests') THEN
     IF public.pdd_region_internal('lifecycle') AND public.pdd_region_context(OLD.version_id)
       AND EXISTS(
       SELECT 1 FROM public.dataset_storage WHERE version_id=OLD.version_id
       AND state IN ('reclaiming','rebuilding')) THEN RETURN OLD; END IF;
   ELSIF TG_TABLE_NAME IN ('fact_content_blocks','shared_product_region_records') THEN
     IF TG_TABLE_NAME='fact_content_blocks' THEN key:=OLD.id; ELSE key:=OLD.block_id; END IF;
     IF public.pdd_region_internal('gc') AND current_setting('pdd.region_gc_block',true)=key
        AND current_setting('pdd.region_gc_tx',true)=txid_current()::text
        AND NOT EXISTS(SELECT 1 FROM public.fact_content_members WHERE block_id=key)
        AND EXISTS(SELECT 1 FROM public.fact_content_blocks WHERE id=key
          AND table_name='product_region_records' AND row_count<=1000) THEN RETURN OLD; END IF;
   END IF;
 END IF;
 IF TG_TABLE_NAME='fact_content_blocks' THEN"""
VERSION_BEFORE = (
    """ IF NOT FOUND OR v.state<>'draft' THEN RAISE EXCEPTION 'CONTENT_VERSION_SEALED'; END IF;"""
)
VERSION_AFTER = """ IF NOT FOUND OR (v.state<>'draft' AND NOT (
   v.dataset_code='product_regions' AND public.pdd_region_context(v.id)
   AND EXISTS(SELECT 1 FROM public.dataset_storage WHERE version_id=v.id AND state='rebuilding')
 )) THEN RAISE EXCEPTION 'CONTENT_VERSION_SEALED'; END IF;"""


def upgrade():
    op.execute("SET LOCAL lock_timeout='100ms'")
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'REGION_MIGRATION_BUSY'; END IF;
    END $$""")
    op.execute("""ALTER TABLE public.dataset_storage
      DROP CONSTRAINT dataset_storage_state_check,
      DROP CONSTRAINT dataset_storage_check,
      ADD CONSTRAINT dataset_storage_state_check
        CHECK(state IN ('online','offline','reclaiming','reclaimed','rebuilding')),
      ADD CONSTRAINT dataset_storage_check CHECK(state<>'offline' OR archive_id IS NOT NULL)""")
    op.execute("""CREATE TABLE public.region_retirements (
      version_id text PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      replacement_version_id text NOT NULL
        REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      status text NOT NULL CHECK(status IN
        ('verifying','retained','reclaiming','reclaimed','rebuilding','restored')),
      representation text NOT NULL CHECK(representation IN ('legacy','shared')),
      cursor text, checked_rows bigint NOT NULL DEFAULT 0 CHECK(checked_rows>=0),
      original_rows bigint CHECK(original_rows>=0),
      content_sha256 text NOT NULL DEFAULT ''
        CHECK(content_sha256='' OR content_sha256 ~ '^[0-9a-f]{64}$'),
      deleted_rows bigint NOT NULL DEFAULT 0 CHECK(deleted_rows>=0),
      transaction_id bigint NOT NULL, reason_code text,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(version_id<>replacement_version_id))""")
    op.execute(
        "CREATE INDEX ix_region_retirement_replacement "
        "ON public.region_retirements(replacement_version_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_region_retirement_active ON public.region_retirements((true)) "
        "WHERE status IN ('verifying','reclaiming')"
    )
    op.execute(
        "COMMENT ON COLUMN public.dataset_storage.state IS "
        "'在线、归档离线，或地区副本回收中/已回收/重建中；仅在线可发布'"
    )
    op.execute(
        "COMMENT ON COLUMN public.alembic_version_v2.version_num IS "
        "'当前独立迁移版本；20增加地区副本回收与原版本重建保护'"
    )
    op.execute(
        "COMMENT ON TABLE public.region_retirements IS "
        "'商品地区无用版本的有界证明、回收与恢复进度；不保存经营行'"
    )
    comments = {
        "version_id": "原地区版本，始终保留发布身份",
        "replacement_version_id": "逐行或不可变块子集证明的替代在线版本",
        "status": "证明、保留、回收或重建阶段",
        "representation": "原物理表示：旧事实行或共享块",
        "cursor": "已核来源键或共享表示的内容桶与来源键，空值表示尚未开始",
        "checked_rows": "已成功核对的逻辑行数",
        "original_rows": "完成证明的完整原版本行数",
        "content_sha256": "有序完整业务行的滚动SHA256摘要；共享表示另绑定内容桶",
        "deleted_rows": "已移除的原表示逻辑行数",
        "transaction_id": "当前受控操作的数据库事务ID",
        "reason_code": "保留或延期原因码",
        "created_at": "首次创建证明的数据库时刻",
        "changed_at": "最近受控进度变更的数据库时刻",
    }
    for name, comment in comments.items():
        op.execute(f"COMMENT ON COLUMN public.region_retirements.{name} IS '{comment}'")
    op.execute("GRANT SELECT ON public.region_retirements TO pdd_app,pdd_ingest")

    function(
        "pdd_region_internal",
        "action text",
        "boolean",
        r"""
    DECLARE stack text; frame text; frames text[];
    BEGIN
      -- 最后实际调用帧必须是固定入口；SQL文本里的伪造行及可写GUC不构成授权。
      -- 普通owner DML也经过此检查；DDL改函数/禁触发器仍属于数据库所有者权限边界。
      GET DIAGNOSTICS stack=PG_CONTEXT;
      frames:=string_to_array(stack,E'\n'); frame:=frames[array_length(frames,1)];
      IF action='gc' THEN
        RETURN frame ~ ('^PL/pgSQL function (public\.)?pdd_region_gc_block\(integer\)'
          ||' line [0-9]+ at SQL statement$');
      END IF;
      RETURN action='lifecycle' AND frame ~
        ('^PL/pgSQL function (public\.)?pdd_region_(retire_step\(integer\)'
          ||'|rebuild_begin\(text,text,text\)|rebuild_finish\(text\))'
          ||' line [0-9]+ at SQL statement$');
    END;""",
    )
    function(
        "pdd_region_context",
        "version_key text",
        "boolean",
        """
    BEGIN
      RETURN current_setting('pdd.region_version',true)=version_key AND EXISTS(
        SELECT 1 FROM public.region_retirements WHERE version_id=version_key
        AND transaction_id=txid_current()
        AND status IN ('verifying','reclaiming','rebuilding','reclaimed','restored'));
    END;""",
    )
    function(
        "pdd_region_audit_guard",
        "",
        "trigger",
        """
    BEGIN
      IF NOT public.pdd_region_internal('lifecycle') THEN
        RAISE EXCEPTION 'REGION_INTERNAL_ONLY'; END IF;
      IF TG_OP='DELETE' OR current_setting('pdd.region_version',true)
        IS DISTINCT FROM NEW.version_id
        OR NEW.transaction_id<>txid_current() THEN RAISE EXCEPTION 'REGION_AUDIT_IMMUTABLE'; END IF;
      IF TG_OP='UPDATE' AND (NEW.version_id<>OLD.version_id OR
        (NEW.replacement_version_id<>OLD.replacement_version_id AND NOT (
          OLD.status IN ('restored','retained') AND NEW.status='verifying'))
        OR NEW.created_at<>OLD.created_at)
        THEN RAISE EXCEPTION 'REGION_AUDIT_IMMUTABLE'; END IF;
      NEW.changed_at:=clock_timestamp(); RETURN NEW;
    END;""",
    )
    op.execute(
        "CREATE TRIGGER pdd_region_audit_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.region_retirements FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_region_audit_guard()"
    )
    op.execute(
        "CREATE TRIGGER pdd_region_no_truncate BEFORE TRUNCATE ON public.region_retirements "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate()"
    )
    function(
        "pdd_region_eligible",
        "version_key text",
        "boolean",
        f"""
    BEGIN
      RETURN EXISTS(SELECT 1 FROM public.dataset_versions v
        JOIN public.dataset_storage s ON s.version_id=v.id
        WHERE v.id=version_key AND v.dataset_code='product_regions' AND v.state='published'
          AND s.state IN ('online','reclaiming') AND NOT s.keep_online)
        AND NOT EXISTS(SELECT 1 FROM ({PROTECTED_SQL}) p WHERE p.id=version_key)
        AND (SELECT max(occurred_at) FROM public.dataset_publications
          WHERE previous_version_id=version_key)<=statement_timestamp()-interval '24 hours';
    END;""",
    )
    function(
        "pdd_region_assert_scope",
        "",
        "void",
        """
    BEGIN
      IF NOT ((current_database() ~ '^pdd_operations_v2_test_[0-9a-f]{12}$'
          AND session_user IN ('pdd_admin','pdd_ingest')) OR
        (current_database()='pdd_operations' AND session_user IN ('pdd_admin','pdd_ingest')) OR
        (current_database()='pdd_dashboard' AND session_user='songbing')) THEN
        RAISE EXCEPTION 'REGION_SCOPE'; END IF;
      IF EXISTS(SELECT 1 FROM pg_constraint WHERE contype='f' AND confrelid IN (
        'public.product_region_records'::regclass,'public.shared_product_region_records'::regclass))
        THEN RAISE EXCEPTION 'REGION_UNKNOWN_REFERENCE'; END IF;
    END;""",
    )
    patch_guard("pdd_retention_storage_guard", STORAGE_BEFORE, STORAGE_AFTER)
    patch_guard("pdd_retention_content", CONTENT_BEFORE, CONTENT_AFTER)
    patch_guard("pdd_content_guard", SHARED_BEFORE, SHARED_AFTER)
    patch_guard("pdd_content_guard", VERSION_BEFORE, VERSION_AFTER)

    function(
        "pdd_region_retire_step",
        "batch_size integer",
        "jsonb",
        """
    DECLARE audit public.region_retirements; item record; block_item record;
      candidate text; replacement text;
      amount bigint:=0; removed bigint:=0; total bigint:=0; digest text; last_key text;
      matches boolean; done boolean; next_status text;
    BEGIN
      PERFORM public.pdd_region_assert_scope();
      IF batch_size IS NULL OR batch_size<1 OR batch_size>1000 THEN
        RAISE EXCEPTION 'REGION_BATCH_LIMIT'; END IF;
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync')) OR
         NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RETURN jsonb_build_object('status','busy','checked_rows',0,'deleted_rows',0); END IF;
      IF EXISTS(SELECT 1 FROM public.task_runs WHERE status='running') THEN
        RETURN jsonb_build_object('status','busy','checked_rows',0,'deleted_rows',0); END IF;
      SELECT * INTO audit FROM public.region_retirements
        WHERE status IN ('verifying','reclaiming') ORDER BY created_at LIMIT 1 FOR UPDATE;
      IF NOT FOUND THEN
        SELECT v.id,c.version_id INTO candidate,replacement FROM public.dataset_versions v
          JOIN public.current_datasets c ON c.dataset_code='product_regions'
          JOIN public.dataset_versions r ON r.id=c.version_id AND r.state='published'
            AND r.row_schema_revision=v.row_schema_revision
          JOIN public.fact_content_manifests m ON m.version_id=r.id
            AND m.table_name='product_region_records'
          WHERE v.dataset_code='product_regions' AND public.pdd_region_eligible(v.id)
            AND NOT EXISTS(SELECT 1 FROM public.region_retirements a WHERE a.version_id=v.id
              AND NOT (a.status='restored' OR
                (a.status='retained' AND (a.replacement_version_id<>c.version_id
                  OR a.reason_code='REGION_PROTECTED'))))
          ORDER BY v.id LIMIT 1;
        IF candidate IS NULL THEN
          RETURN jsonb_build_object('status','idle','checked_rows',0,'deleted_rows',0); END IF;
        PERFORM set_config('pdd.region_version',candidate,true);
        INSERT INTO public.region_retirements(version_id,replacement_version_id,status,
          representation,transaction_id) VALUES(candidate,replacement,'verifying',
          CASE WHEN EXISTS(SELECT 1 FROM public.fact_content_manifests WHERE version_id=candidate)
          THEN 'shared' ELSE 'legacy' END,txid_current())
          ON CONFLICT(version_id) DO UPDATE SET
            replacement_version_id=excluded.replacement_version_id,status='verifying',
            representation=excluded.representation,transaction_id=txid_current(),cursor=NULL,
            checked_rows=0,original_rows=NULL,content_sha256='',deleted_rows=0,reason_code=NULL
          RETURNING * INTO audit;
      ELSE
        PERFORM set_config('pdd.region_version',audit.version_id,true);
        UPDATE public.region_retirements SET transaction_id=txid_current()
          WHERE version_id=audit.version_id;
      END IF;
      IF NOT public.pdd_region_eligible(audit.version_id) THEN
        IF audit.status='reclaiming' THEN RAISE EXCEPTION 'REGION_PROTECTED'; END IF;
        UPDATE public.region_retirements SET status='retained',reason_code='REGION_PROTECTED'
          WHERE version_id=audit.version_id;
        RETURN jsonb_build_object('status','retained','version_id',audit.version_id,
          'checked_rows',0,'deleted_rows',0); END IF;
      PERFORM public.pdd_retention_online(audit.replacement_version_id);
      IF audit.status='verifying' THEN
        digest:=audit.content_sha256; last_key:=audit.cursor; matches:=true;
        IF audit.representation='legacy' THEN
          FOR item IN
            SELECT f.source_row_key AS key,
              (to_jsonb(f)-ARRAY['id','dataset_version_id']) AS payload,
              (to_jsonb(f)-ARRAY['id','dataset_version_id']) = (to_jsonb(s)-'block_id') AS matched
            FROM (SELECT * FROM public.product_region_records
              WHERE dataset_version_id=audit.version_id
                AND (audit.cursor IS NULL OR source_row_key>audit.cursor)
              ORDER BY source_row_key LIMIT batch_size) f
            LEFT JOIN public.fact_content_members m ON m.version_id=audit.replacement_version_id
              AND m.bucket=coalesce(f.period_start::text,'?')||'/'||coalesce(f.period_end::text,'?')
                ||':'||mod(('x'||substr(md5(f.source_row_key),1,8))::bit(32)::bigint,64)::text
            LEFT JOIN public.shared_product_region_records s ON s.block_id=m.block_id
              AND s.source_row_key=f.source_row_key ORDER BY f.source_row_key
          LOOP
            IF item.matched IS DISTINCT FROM true THEN matches:=false; EXIT; END IF;
            digest:=encode(sha256(convert_to(digest||item.payload::text,'UTF8')),'hex');
            amount:=amount+1; total:=total+1; last_key:=item.key;
          END LOOP;
        ELSE
          FOR block_item IN SELECT m.bucket,m.block_id,
              EXISTS(SELECT 1 FROM public.fact_content_members r
                WHERE r.version_id=audit.replacement_version_id AND r.bucket=m.bucket
                  AND r.block_id=m.block_id) AS matched
            FROM public.fact_content_members m WHERE m.version_id=audit.version_id
              AND (audit.cursor IS NULL OR m.bucket>(audit.cursor::jsonb->>0) OR
                (m.bucket=(audit.cursor::jsonb->>0) AND EXISTS(
                  SELECT 1 FROM public.shared_product_region_records f
                  WHERE f.block_id=m.block_id AND f.source_row_key>(audit.cursor::jsonb->>1))))
            ORDER BY m.bucket LIMIT batch_size
          LOOP
            IF NOT block_item.matched THEN matches:=false; EXIT; END IF;
            FOR item IN SELECT f.source_row_key,
                jsonb_build_array(block_item.bucket,to_jsonb(f)-'block_id') AS payload
              FROM public.shared_product_region_records f WHERE f.block_id=block_item.block_id
                AND (audit.cursor IS NULL OR block_item.bucket>(audit.cursor::jsonb->>0)
                  OR f.source_row_key>(audit.cursor::jsonb->>1))
              ORDER BY f.source_row_key LIMIT (batch_size-amount)
            LOOP
              digest:=encode(sha256(convert_to(digest||item.payload::text,'UTF8')),'hex');
              amount:=amount+1; total:=total+1;
              last_key:=jsonb_build_array(block_item.bucket,item.source_row_key)::text;
            END LOOP;
            EXIT WHEN amount=batch_size;
          END LOOP;
        END IF;
        IF NOT matches THEN
          UPDATE public.region_retirements
            SET status='retained',reason_code='REGION_CONTENT_DIFFERENT'
            WHERE version_id=audit.version_id;
          next_status:='retained';
        ELSE
          done:=amount<batch_size;
          IF done AND audit.representation='shared' AND audit.checked_rows+total<>(
            SELECT row_count FROM public.fact_content_manifests WHERE version_id=audit.version_id)
            THEN RAISE EXCEPTION 'REGION_MANIFEST_INCOMPLETE'; END IF;
          next_status:=CASE WHEN done THEN 'reclaiming' ELSE 'verifying' END;
          UPDATE public.region_retirements SET cursor=last_key,checked_rows=checked_rows+total,
            content_sha256=digest,status=next_status,
            original_rows=CASE WHEN done THEN checked_rows+total ELSE NULL END
            WHERE version_id=audit.version_id;
          IF done THEN UPDATE public.dataset_storage SET state='reclaiming'
            WHERE version_id=audit.version_id; END IF;
        END IF;
      ELSE
        IF audit.representation='legacy' THEN
          WITH deleted AS (DELETE FROM public.product_region_records WHERE id IN (
            SELECT id FROM public.product_region_records WHERE dataset_version_id=audit.version_id
            ORDER BY source_row_key LIMIT batch_size) RETURNING 1)
            SELECT count(*) INTO removed FROM deleted;
          done:=NOT EXISTS(SELECT 1 FROM public.product_region_records
            WHERE dataset_version_id=audit.version_id);
        ELSE
          WITH deleted AS (DELETE FROM public.fact_content_members WHERE version_id=audit.version_id
            AND bucket IN (SELECT bucket FROM public.fact_content_members
              WHERE version_id=audit.version_id ORDER BY bucket LIMIT batch_size)
            RETURNING block_id)
            SELECT coalesce(sum(b.row_count),0) INTO removed FROM deleted d
              JOIN public.fact_content_blocks b ON b.id=d.block_id;
          done:=NOT EXISTS(SELECT 1 FROM public.fact_content_members
            WHERE version_id=audit.version_id);
          IF done THEN DELETE FROM public.fact_content_manifests
            WHERE version_id=audit.version_id; END IF;
        END IF;
        next_status:=CASE WHEN done THEN 'reclaimed' ELSE 'reclaiming' END;
        UPDATE public.region_retirements SET status=next_status,deleted_rows=deleted_rows+removed
          WHERE version_id=audit.version_id;
        IF done THEN
          IF audit.deleted_rows+removed<>audit.original_rows THEN
            RAISE EXCEPTION 'REGION_PARTIAL'; END IF;
          UPDATE public.dataset_storage SET state='reclaimed' WHERE version_id=audit.version_id;
        END IF;
      END IF;
      PERFORM set_config('pdd.region_version','',true);
      RETURN jsonb_build_object('status',next_status,'version_id',audit.version_id,
        'checked_rows',total,'deleted_rows',removed,'cursor',last_key);
    END;""",
        definer=True,
    )

    function(
        "pdd_region_rebuild_begin",
        "version_key text, fingerprint text, schema_revision text",
        "void",
        """
    DECLARE a public.region_retirements;
    BEGIN
      PERFORM public.pdd_region_assert_scope();
      PERFORM pg_advisory_xact_lock_shared(hashtext('pdd-database-maintenance'));
      PERFORM 1 FROM public.dataset_versions WHERE id=version_key FOR UPDATE;
      SELECT * INTO a FROM public.region_retirements WHERE version_id=version_key FOR UPDATE;
      IF NOT FOUND OR a.status NOT IN ('reclaimed','reclaiming') OR a.original_rows IS NULL OR
        NOT EXISTS(SELECT 1 FROM public.dataset_versions WHERE id=version_key
          AND state='published' AND dataset_code='product_regions'
          AND content_fingerprint=fingerprint AND row_schema_revision=schema_revision) THEN
        RAISE EXCEPTION 'REGION_REBUILD_IDENTITY'; END IF;
      PERFORM set_config('pdd.region_version',version_key,true);
      UPDATE public.region_retirements SET status='rebuilding',transaction_id=txid_current()
        WHERE version_id=version_key;
      UPDATE public.dataset_storage SET state='rebuilding' WHERE version_id=version_key;
      DELETE FROM public.product_region_records WHERE dataset_version_id=version_key;
      DELETE FROM public.fact_content_members WHERE version_id=version_key;
      DELETE FROM public.fact_content_manifests WHERE version_id=version_key;
    END;""",
        definer=True,
    )
    function(
        "pdd_region_rebuild_finish",
        "version_key text",
        "void",
        """
    DECLARE a public.region_retirements; item record; digest text:=''; actual bigint:=0;
    BEGIN
      SELECT * INTO a FROM public.region_retirements WHERE version_id=version_key FOR UPDATE;
      IF NOT public.pdd_region_context(version_key) OR a.status<>'rebuilding' THEN
        RAISE EXCEPTION 'REGION_REBUILD_STATE'; END IF;
      IF a.representation='legacy' THEN
        FOR item IN SELECT to_jsonb(r)-ARRAY['id','dataset_version_id'] AS payload
          FROM public.read_product_region_records r WHERE dataset_version_id=version_key
          ORDER BY source_row_key
        LOOP
          digest:=encode(sha256(convert_to(digest||item.payload::text,'UTF8')),'hex');
          actual:=actual+1;
        END LOOP;
      ELSE
        FOR item IN SELECT jsonb_build_array(m.bucket,to_jsonb(f)-'block_id') AS payload
          FROM public.fact_content_members m
          JOIN public.shared_product_region_records f ON f.block_id=m.block_id
          WHERE m.version_id=version_key ORDER BY m.bucket,f.source_row_key
        LOOP
          digest:=encode(sha256(convert_to(digest||item.payload::text,'UTF8')),'hex');
          actual:=actual+1;
        END LOOP;
      END IF;
      IF digest<>a.content_sha256 OR actual<>a.original_rows OR actual<>coalesce((
          SELECT row_count FROM public.fact_content_manifests WHERE version_id=version_key),-1) THEN
        RAISE EXCEPTION 'REGION_REBUILD_CONTENT'; END IF;
      UPDATE public.region_retirements SET status='restored' WHERE version_id=version_key;
      UPDATE public.dataset_storage SET state='online' WHERE version_id=version_key;
      PERFORM set_config('pdd.region_version','',true);
    END;""",
        definer=True,
    )
    function(
        "pdd_region_rebuild_complete",
        "",
        "trigger",
        """
    BEGIN
      IF EXISTS(SELECT 1 FROM public.region_retirements WHERE version_id=NEW.version_id
        AND status='rebuilding') THEN RAISE EXCEPTION 'REGION_REBUILD_INCOMPLETE'; END IF;
      RETURN NULL;
    END;""",
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER pdd_region_rebuild_complete AFTER UPDATE "
        "ON public.region_retirements DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION public.pdd_region_rebuild_complete()"
    )
    function(
        "pdd_region_gc_block",
        "batch_size integer",
        "bigint",
        """
    DECLARE block public.fact_content_blocks; actual bigint;
    BEGIN
      PERFORM public.pdd_region_assert_scope();
      IF batch_size IS NULL OR batch_size<1 OR batch_size>1000 THEN
        RAISE EXCEPTION 'REGION_BATCH_LIMIT'; END IF;
      IF NOT pg_try_advisory_xact_lock(hashtext('project-source-sync')) OR
        NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) OR
        EXISTS(SELECT 1 FROM public.task_runs WHERE status='running') THEN RETURN 0; END IF;
      SELECT * INTO block FROM public.fact_content_blocks b
        WHERE b.table_name='product_region_records' AND b.state='sealed' AND b.row_count<=batch_size
        AND NOT EXISTS(SELECT 1 FROM public.fact_content_members WHERE block_id=b.id)
        ORDER BY b.id LIMIT 1 FOR UPDATE;
      IF NOT FOUND THEN RETURN 0; END IF;
      PERFORM set_config('pdd.region_gc_block',block.id,true);
      PERFORM set_config('pdd.region_gc_tx',txid_current()::text,true);
      DELETE FROM public.shared_product_region_records WHERE block_id=block.id;
      GET DIAGNOSTICS actual=ROW_COUNT;
      IF actual<>block.row_count THEN RAISE EXCEPTION 'REGION_BLOCK_INCOMPLETE'; END IF;
      DELETE FROM public.fact_content_blocks WHERE id=block.id;
      PERFORM set_config('pdd.region_gc_block','',true);
      PERFORM set_config('pdd.region_gc_tx','',true);
      RETURN actual;
    END;""",
        definer=True,
    )
    for name, args in (
        ("retire_step", "integer"),
        ("rebuild_begin", "text,text,text"),
        ("rebuild_finish", "text"),
        ("gc_block", "integer"),
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.pdd_region_{name}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.pdd_region_{name}({args}) TO pdd_ingest")


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM public.dataset_storage WHERE state NOT IN ('online','offline'))
        OR EXISTS(SELECT 1 FROM public.region_retirements)
        THEN RAISE EXCEPTION 'REGION_DOWNGRADE_REBUILD_REQUIRED'; END IF;
    END $$""")
    patch_guard("pdd_retention_storage_guard", STORAGE_AFTER, STORAGE_BEFORE)
    patch_guard("pdd_retention_content", CONTENT_AFTER, CONTENT_BEFORE)
    patch_guard("pdd_content_guard", SHARED_AFTER, SHARED_BEFORE)
    patch_guard("pdd_content_guard", VERSION_AFTER, VERSION_BEFORE)
    op.execute("DROP TABLE public.region_retirements")
    for name, args in (
        ("retire_step", "integer"),
        ("rebuild_begin", "text,text,text"),
        ("rebuild_finish", "text"),
        ("gc_block", "integer"),
        ("rebuild_complete", ""),
        ("audit_guard", ""),
        ("eligible", "text"),
        ("context", "text"),
        ("assert_scope", ""),
        ("internal", "text"),
    ):
        op.execute(f"DROP FUNCTION public.pdd_region_{name}({args})")
    op.execute("""ALTER TABLE public.dataset_storage
      DROP CONSTRAINT dataset_storage_state_check, DROP CONSTRAINT dataset_storage_check,
      ADD CONSTRAINT dataset_storage_state_check CHECK(state IN ('online','offline')),
      ADD CONSTRAINT dataset_storage_check CHECK(state='online' OR archive_id IS NOT NULL)""")
    op.execute(
        "COMMENT ON COLUMN public.dataset_storage.state IS "
        "'驻留状态：online在线、offline已归档离线；不代表来源完整性'"
    )
    op.execute(
        "COMMENT ON COLUMN public.alembic_version_v2.version_num IS "
        "'当前已执行的Alembic修订号；19仅补说明，经营结构及归档格式兼容18'"
    )
