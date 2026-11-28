"""冻结预测修订的核验结果，按当前文档权限公开拒绝原因与引文。"""

from alembic import op

revision = "20261128_23"
down_revision = "20261127_22"
branch_labels = depends_on = None

LEGACY_RULE = "已核对原文的受控计算模板及参数；空表示该修订暂不可执行"


def _lock_sync():
    op.execute("""DO $$ BEGIN
      IF NOT pg_try_advisory_xact_lock(726411,20261127) THEN
        RAISE EXCEPTION 'FORECAST_INTERPRETATION_BUSY';
      END IF;
    END $$""")


def _view(*, reviewed):
    """23 仅在旧投影末尾追加 issues。降级时复原 21/22 的十列定义。"""
    reject_rule = " OR v.rule->>'status'='rejected'" if reviewed else ""
    reject_reason = (
        "WHEN state='unsupported' AND rule->>'status'='rejected' THEN 'interpretation_rejected'"
        if reviewed
        else ""
    )
    issues = (
        """,CASE WHEN state='unsupported' AND rule->>'status'='rejected'
                 THEN rule->'issues' ELSE '[]'::jsonb END AS issues"""
        if reviewed
        else ""
    )
    return f"""CREATE OR REPLACE VIEW public.read_forecast_sop WITH (security_barrier=true) AS
      WITH current_source AS (
        SELECT s.document_id,s.source_url,s.revision,s.reason,s.checked_at,s.verified_at,
          v.content,v.rule,v.synced_at,
          CASE WHEN s.version_revision IS NULL THEN s.state
               WHEN s.verified_at+interval '24 hours'<=statement_timestamp() THEN 'expired'
               WHEN v.rule IS NULL{reject_rule} THEN 'unsupported'
               WHEN s.state='ready' THEN 'active' ELSE s.state END AS state
        FROM public.forecast_sop_source s
        LEFT JOIN public.forecast_sop_versions v
          ON (s.document_id,s.version_revision)=(v.document_id,v.revision))
      SELECT document_id,source_url,revision,state,
        CASE WHEN state='expired' THEN 'verification_expired'
             {reject_reason}
             WHEN state='unsupported' THEN 'unsupported_rule' ELSE reason END AS reason,
        CASE WHEN state IN ('active','stale','unsupported') THEN content END AS content,
        CASE WHEN state IN ('active','stale') THEN rule END AS rule,checked_at,verified_at,synced_at
        {issues}
      FROM current_source"""


def upgrade():
    _lock_sync()
    op.execute(_view(reviewed=True))
    op.execute("""COMMENT ON COLUMN public.forecast_sop_versions.rule
      IS '同版已核验的规则与解读来源，或拒绝原因与引文；历史空值表示当时无法编译'""")
    op.execute("""COMMENT ON COLUMN public.read_forecast_sop.issues
      IS '当前获准读取修订的拒绝原因、章节和摘句，其他状态为空数组'""")


def downgrade():
    _lock_sync()
    op.execute("""LOCK TABLE public.forecast_sop_versions IN SHARE MODE""")
    op.execute("""DO $$ BEGIN
      IF EXISTS (SELECT FROM public.forecast_sop_versions
                 WHERE rule ? 'interpretation' OR rule->>'status'='rejected') THEN
        RAISE EXCEPTION 'FORECAST_INTERPRETATION_PRESENT';
      END IF;
    END $$""")
    # PG 的 CREATE OR REPLACE VIEW 不能减列。重建时保留十列说明及实际表/列授权。
    op.execute(f"""DO $restore$
      DECLARE description text; notes text[]; grants text[]; statement text;
      BEGIN
        SELECT obj_description('public.read_forecast_sop'::regclass,'pg_class'),
          ARRAY(SELECT format('COMMENT ON COLUMN public.read_forecast_sop.%I IS %L',
                       a.attname,col_description(a.attrelid,a.attnum))
                FROM pg_attribute a WHERE a.attrelid='public.read_forecast_sop'::regclass
                AND a.attnum>0 AND NOT a.attisdropped AND a.attname<>'issues')
          INTO description,notes;
        SELECT ARRAY(
          SELECT format('GRANT %s%s ON public.read_forecast_sop TO %s%s',p.privilege_type,
            CASE WHEN attr IS NULL THEN '' ELSE format('(%I)',attr) END,
            CASE WHEN p.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(p.grantee)) END,
            CASE WHEN p.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END)
          FROM pg_class r CROSS JOIN LATERAL (
            SELECT NULL::name AS attr,r.relacl AS acl
            UNION ALL SELECT a.attname,a.attacl FROM pg_attribute a WHERE a.attrelid=r.oid
              AND a.attnum>0 AND NOT a.attisdropped AND a.attname<>'issues'
          ) access CROSS JOIN LATERAL aclexplode(access.acl) p
          WHERE r.oid='public.read_forecast_sop'::regclass AND p.grantee<>r.relowner
        ) INTO grants;
        DROP VIEW public.read_forecast_sop;
        {_view(reviewed=False)};
        -- 建视图可能获得当前默认授权，先移除再恢复原实际权限。
        FOR statement IN SELECT DISTINCT format('REVOKE ALL ON public.read_forecast_sop FROM %s',
              CASE WHEN p.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(p.grantee)) END)
          FROM pg_class r CROSS JOIN LATERAL aclexplode(r.relacl) p
          WHERE r.oid='public.read_forecast_sop'::regclass AND p.grantee<>r.relowner
        LOOP EXECUTE statement; END LOOP;
        FOREACH statement IN ARRAY grants LOOP EXECUTE statement; END LOOP;
        EXECUTE format('COMMENT ON VIEW public.read_forecast_sop IS %L',description);
        FOREACH description IN ARRAY notes LOOP EXECUTE description; END LOOP;
      END $restore$""")
    op.execute(f"COMMENT ON COLUMN public.forecast_sop_versions.rule IS '{LEGACY_RULE}'")
