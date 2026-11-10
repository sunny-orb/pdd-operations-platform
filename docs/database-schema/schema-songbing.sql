-- 现行v2完整空库初始化文件；由scripts/render-db-schema.py生成，请勿手改。
-- 迁移范围：base → 20261110_14；包含Alembic版本表。
-- 迁移、冻结实现及生成器SHA-256：81b56ebd14282629edcad0052bedb91f414d3f895fcef25f0e4c5015109efd83
-- 公司单账号：songbing；应用结构须空；保留monitor及登记的扩展成员。
-- 不创建角色、不修改数据库或monitor权限；应用对象由执行账号拥有。
-- 使用方式和后续升级见docs/database-schema/migration-plan.md。

BEGIN;

DO $preflight$ BEGIN
  IF current_database() <> 'pdd_operations' THEN
    RAISE EXCEPTION '初始化数据库不符';
  END IF;
  IF current_setting('server_version_num')::integer / 10000 <> 17 THEN
    RAISE EXCEPTION '初始化文件要求PostgreSQL 17';
  END IF;
  IF current_user <> 'songbing' THEN
    RAISE EXCEPTION '初始化身份不符';
  END IF;
  PERFORM pg_advisory_xact_lock(726411, 20261104);
  IF EXISTS(
    WITH RECURSIVE extension_members(classid,objid,objsubid) AS (
  SELECT d.classid,d.objid,d.objsubid
  FROM pg_catalog.pg_depend d
  JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
  WHERE d.refclassid='pg_catalog.pg_extension'::regclass
    AND d.deptype='e' AND d.refobjsubid=0
  UNION
  SELECT d.classid,d.objid,d.objsubid
  FROM pg_catalog.pg_depend d
  JOIN extension_members e
    ON d.refclassid=e.classid AND d.refobjid=e.objid AND d.refobjsubid=e.objsubid
  WHERE d.deptype='i'
)

    SELECT 1 FROM (
      SELECT 'pg_catalog.pg_class'::regclass AS classid,oid,relnamespace AS namespace
      FROM pg_catalog.pg_class
      UNION ALL
      SELECT 'pg_catalog.pg_proc'::regclass,oid,pronamespace FROM pg_catalog.pg_proc
      UNION ALL
      SELECT 'pg_catalog.pg_type'::regclass,oid,typnamespace FROM pg_catalog.pg_type
    ) o JOIN pg_catalog.pg_namespace n ON n.oid=o.namespace
    WHERE n.nspname NOT IN ('information_schema', 'monitor') AND n.nspname !~ '^pg_'
      AND NOT EXISTS (SELECT 1 FROM extension_members e
                      WHERE e.classid=o.classid AND e.objid=o.oid AND e.objsubid=0)
  ) THEN
    RAISE EXCEPTION '仅允许应用空结构初始化；保留monitor，不重放已有应用库';
  END IF;
  IF NOT has_schema_privilege(current_user, 'public', 'USAGE')
     OR NOT has_schema_privilege(current_user, 'public', 'CREATE') THEN
    RAISE EXCEPTION 'public创建权限不足';
  END IF;
END $preflight$;


CREATE TABLE public.alembic_version_v2 (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_v2_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20261028_01

DO $preflight$
BEGIN
    IF EXISTS (
        WITH RECURSIVE extension_members(classid,objid,objsubid) AS (
  SELECT d.classid,d.objid,d.objsubid
  FROM pg_catalog.pg_depend d
  JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
  WHERE d.refclassid='pg_catalog.pg_extension'::regclass
    AND d.deptype='e' AND d.refobjsubid=0
  UNION
  SELECT d.classid,d.objid,d.objsubid
  FROM pg_catalog.pg_depend d
  JOIN extension_members e
    ON d.refclassid=e.classid AND d.refobjid=e.objid AND d.refobjsubid=e.objsubid
  WHERE d.deptype='i'
)
SELECT 1 FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S','f')
          AND c.relname <> 'alembic_version_v2' AND NOT EXISTS (SELECT 1 FROM extension_members e WHERE e.classid='pg_catalog.pg_class'::regclass AND e.objid=c.oid AND e.objsubid=0)
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname LIKE 'pdd_v2_%'
    ) THEN
        RAISE EXCEPTION 'v2基线只允许空public结构，拒绝混用既有对象';
    END IF;
END
$preflight$;

CREATE TABLE public.rule_sets (
    id TEXT NOT NULL,
    set_code TEXT NOT NULL,
    revision TEXT NOT NULL,
    domain TEXT NOT NULL,
    schema_revision TEXT NOT NULL,
    parameters JSONB NOT NULL,
    confirmation_status TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_observed_at TIMESTAMP WITH TIME ZONE,
    confirmation_ref TEXT,
    confirmed_at TIMESTAMP WITH TIME ZONE,
    content_sha256 TEXT NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_rule_sets_identity PRIMARY KEY (id),
    CONSTRAINT ck_rule_sets_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_rule_sets_set_code_text_nonblank CHECK (set_code IS NULL OR btrim(set_code) <> ''),
    CONSTRAINT ck_rule_sets_revision_text_nonblank CHECK (revision IS NULL OR btrim(revision) <> ''),
    CONSTRAINT ck_rule_sets_domain_text_nonblank CHECK (domain IS NULL OR btrim(domain) <> ''),
    CONSTRAINT ck_rule_sets_schema_revision_text_nonblank CHECK (schema_revision IS NULL OR btrim(schema_revision) <> ''),
    CONSTRAINT ck_rule_sets_confirmation_status_text_nonblank CHECK (confirmation_status IS NULL OR btrim(confirmation_status) <> ''),
    CONSTRAINT ck_rule_sets_source_locator_text_nonblank CHECK (source_locator IS NULL OR btrim(source_locator) <> ''),
    CONSTRAINT ck_rule_sets_confirmation_ref_text_nonblank CHECK (confirmation_ref IS NULL OR btrim(confirmation_ref) <> ''),
    CONSTRAINT ck_rule_sets_content_sha256_text_nonblank CHECK (content_sha256 IS NULL OR btrim(content_sha256) <> ''),
    CONSTRAINT ck_rule_sets_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT uq_rule_sets_set_revision UNIQUE (set_code, revision),
    CONSTRAINT ck_rule_sets_domain_values CHECK (domain IN ('product_scoring', 'product_risk', 'promotion', 'aftersales', 'overview')),
    CONSTRAINT ck_rule_sets_schema_revision_values CHECK (schema_revision IN ('v1')),
    CONSTRAINT ck_rule_sets_confirmation_status_values CHECK (confirmation_status IN ('confirmed', 'partially_confirmed', 'pending_confirmation')),
    CONSTRAINT ck_rule_sets_confirmation_shape CHECK ((confirmation_status <> 'confirmed' OR confirmation_ref IS NOT NULL) AND (confirmed_at IS NULL OR confirmation_status = 'confirmed')),
    CONSTRAINT ck_rule_sets_sha256_format CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_rule_sets_parameters_v1_root CHECK ((CASE WHEN jsonb_typeof(parameters) = 'object' THEN (parameters ?& ARRAY['policy', 'branches', 'dictionaries', 'suggestions']::text[] AND parameters - ARRAY['policy', 'branches', 'dictionaries', 'suggestions']::text[] = '{}'::jsonb) IS TRUE ELSE FALSE END) AND (jsonb_typeof(parameters->'policy') = 'object' AND jsonb_typeof(parameters->'branches') = 'array' AND jsonb_typeof(parameters->'dictionaries') = 'array' AND jsonb_typeof(parameters->'suggestions') = 'array') IS TRUE),
    CONSTRAINT ck_rule_sets_product_risk_policy_v1 CHECK (domain <> 'product_risk' OR (parameters->'policy' = '{"baseline_days":7,"hit_policy_code":"first_match","sort_policy_code":"priority_score_identity","excluded_level_codes":["清仓"],"blockbuster_policy_code":"legacy_revenue_top_20_percent"}'::jsonb) IS TRUE),
    CONSTRAINT ck_rule_sets_promotion_policy_v1 CHECK (domain <> 'promotion' OR (parameters->'policy' = '{"current_window_days":7,"previous_window_days":7,"end_policy_code":"min_selected_source_latest_shanghai_yesterday","hit_policy_code":"collect_findings_highest_priority","sort_policy_code":"priority_current_spend_identity","focused_level_policy_code":"sa_exclusive_new"}'::jsonb) IS TRUE),
    CONSTRAINT ck_rule_sets_aftersales_policy_v1 CHECK (domain <> 'aftersales' OR (parameters->'policy' = '{"comparison_policy_code":"frozen_report_periods","grouping_policy_code":"full_source_groups_unique_denominator"}'::jsonb) IS TRUE),
    CONSTRAINT ck_rule_sets_overview_policy_v1 CHECK (domain <> 'overview' OR (parameters->'policy' = '{"priority_order":["P0","P1","P2","observation"],"sort_policy_code":"sop_impact_deterioration_scope","deduplication_policy_code":"same_object_group_no_parent_child_amount_sum","weighted_score_policy_code":"not_applicable","count_policy_code":null}'::jsonb) IS TRUE),
    CONSTRAINT ck_rule_sets_scoring_policy_v1 CHECK (domain <> 'product_scoring' OR (CASE WHEN jsonb_typeof((parameters->'policy')) = 'object' THEN ((parameters->'policy') ?& ARRAY['metrics', 'gates', 'tier_policy_code', 'lifecycle_policy_code']::text[] AND (parameters->'policy') - ARRAY['metrics', 'gates', 'tier_policy_code', 'lifecycle_policy_code']::text[] = '{}'::jsonb) IS TRUE ELSE FALSE END AND (parameters->'policy')->'gates' = '{"current_window_days":7,"previous_window_days":7,"ad_impressions_min":"2000","previous_orders_min":5,"conversion_numerator_code":"orders","roi_basis_code":"total_attributed_revenue_over_total_cost","require_source_completeness":true,"require_verified_reference":true,"require_attribution_evidence":true}'::jsonb AND (parameters->'policy')->'tier_policy_code' = '"highest_inclusive_quarter_weight"'::jsonb AND (parameters->'policy')->'lifecycle_policy_code' = 'null'::jsonb AND CASE WHEN jsonb_typeof((parameters->'policy')->'metrics') = 'array' THEN jsonb_array_length((parameters->'policy')->'metrics') = 10 ELSE FALSE END) IS TRUE),
    CONSTRAINT ck_rule_sets_non_aftersales_dictionary_empty CHECK (domain = 'aftersales' OR (parameters->'dictionaries' = '[]'::jsonb) IS TRUE)
);

COMMENT ON TABLE public.rule_sets IS '不可变规则参数包';

COMMENT ON COLUMN public.rule_sets.id IS '规则包修订标识';

COMMENT ON COLUMN public.rule_sets.set_code IS '同一用途规则包的稳定代码';

COMMENT ON COLUMN public.rule_sets.revision IS '本包修订号';

COMMENT ON COLUMN public.rule_sets.domain IS '包所属业务域';

COMMENT ON COLUMN public.rule_sets.schema_revision IS '参数结构的确切版本';

COMMENT ON COLUMN public.rule_sets.parameters IS '该修订完整结构化参数';

COMMENT ON COLUMN public.rule_sets.confirmation_status IS '整包业务确认状态';

COMMENT ON COLUMN public.rule_sets.source_locator IS '本包依据的确切SOP/契约/交付定位';

COMMENT ON COLUMN public.rule_sets.source_observed_at IS '真实观察该依据的时刻';

COMMENT ON COLUMN public.rule_sets.confirmation_ref IS '整包确认或逐分支确认记录的固定引用';

COMMENT ON COLUMN public.rule_sets.confirmed_at IS '真实整包确认时刻';

COMMENT ON COLUMN public.rule_sets.content_sha256 IS '结构版本及参数正文的内容摘要';

COMMENT ON COLUMN public.rule_sets.evidence_note IS '已确认范围、未知项及适用边界';

CREATE TABLE public.shops (
    id TEXT NOT NULL,
    shop_name TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shops_identity PRIMARY KEY (id),
    CONSTRAINT ck_shops_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shops_status_values CHECK (status IN ('active', 'inactive')),
    CONSTRAINT ck_shops_identity_time_order CHECK (updated_at >= created_at),
    CONSTRAINT ck_shops_soft_delete_shape CHECK (deleted_at IS NULL OR (status = 'inactive' AND created_at <= deleted_at AND deleted_at <= updated_at)),
    CONSTRAINT ck_shops_shop_name_core_text CHECK (shop_name IS NULL OR (btrim(shop_name) <> ''))
);

COMMENT ON TABLE public.shops IS '经营店铺';

COMMENT ON COLUMN public.shops.id IS '项目统一店铺ID';

COMMENT ON COLUMN public.shops.shop_name IS '已核实的当前规范店铺名称';

COMMENT ON COLUMN public.shops.status IS '该身份在本项目中的启用状态';

COMMENT ON COLUMN public.shops.created_at IS '本项目首次建立该身份的时刻';

COMMENT ON COLUMN public.shops.updated_at IS '本项目最后修订该主记录的时刻';

COMMENT ON COLUMN public.shops.deleted_at IS '本项目将该身份软删除的时刻';

CREATE TABLE public.dataset_versions (
    id TEXT NOT NULL,
    dataset_code TEXT NOT NULL,
    origin_kind TEXT NOT NULL,
    state TEXT NOT NULL,
    source_system TEXT,
    source_locator TEXT,
    row_schema_revision TEXT NOT NULL,
    interpretation_ref TEXT NOT NULL,
    producer_revision TEXT NOT NULL,
    rule_set_id TEXT,
    content_fingerprint TEXT,
    retained_input_ref TEXT,
    upstream_revision TEXT,
    period_start DATE,
    period_end DATE,
    source_cursor TEXT,
    received_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    sealed_at TIMESTAMP WITH TIME ZONE,
    validation_evidence_ref TEXT,
    CONSTRAINT pk_dataset_versions_identity PRIMARY KEY (id),
    CONSTRAINT fk_dataset_versions_rule_set_id FOREIGN KEY(rule_set_id) REFERENCES public.rule_sets (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_dataset_versions_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_dataset_versions_rule_set_id_core_text CHECK (rule_set_id IS NULL OR (btrim(rule_set_id) <> '' AND rule_set_id = btrim(rule_set_id))),
    CONSTRAINT ck_dataset_versions_dataset_code_core_text CHECK (dataset_code IS NULL OR (btrim(dataset_code) <> '')),
    CONSTRAINT ck_dataset_versions_source_system_core_text CHECK (source_system IS NULL OR (btrim(source_system) <> '')),
    CONSTRAINT ck_dataset_versions_source_locator_core_text CHECK (source_locator IS NULL OR (btrim(source_locator) <> '')),
    CONSTRAINT ck_dataset_versions_row_schema_revision_core_text CHECK (row_schema_revision IS NULL OR (btrim(row_schema_revision) <> '')),
    CONSTRAINT ck_dataset_versions_interpretation_ref_core_text CHECK (interpretation_ref IS NULL OR (btrim(interpretation_ref) <> '')),
    CONSTRAINT ck_dataset_versions_producer_revision_core_text CHECK (producer_revision IS NULL OR (btrim(producer_revision) <> '')),
    CONSTRAINT ck_dataset_versions_content_fingerprint_core_text CHECK (content_fingerprint IS NULL OR (btrim(content_fingerprint) <> '')),
    CONSTRAINT ck_dataset_versions_retained_input_ref_core_text CHECK (retained_input_ref IS NULL OR (btrim(retained_input_ref) <> '')),
    CONSTRAINT ck_dataset_versions_upstream_revision_core_text CHECK (upstream_revision IS NULL OR (btrim(upstream_revision) <> '')),
    CONSTRAINT ck_dataset_versions_source_cursor_core_text CHECK (source_cursor IS NULL OR (btrim(source_cursor) <> '')),
    CONSTRAINT ck_dataset_versions_validation_evidence_ref_core_text CHECK (validation_evidence_ref IS NULL OR (btrim(validation_evidence_ref) <> '')),
    CONSTRAINT ck_dataset_versions_origin_kind_values CHECK (origin_kind IN ('source', 'derived')),
    CONSTRAINT ck_dataset_versions_state_values CHECK (state IN ('draft', 'validated', 'published', 'rejected')),
    CONSTRAINT uq_dataset_versions_dataset_content UNIQUE (dataset_code, content_fingerprint),
    CONSTRAINT ck_dataset_versions_period_start_period_end_core_pair CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_dataset_versions_origin_fields_shape CHECK ((origin_kind = 'source' AND source_system IS NOT NULL AND source_locator IS NOT NULL) OR (origin_kind = 'derived' AND source_system IS NULL AND source_locator IS NULL AND upstream_revision IS NULL AND source_cursor IS NULL AND received_at IS NULL)),
    CONSTRAINT ck_dataset_versions_sealed_state_shape CHECK (state NOT IN ('validated', 'published') OR (content_fingerprint IS NOT NULL AND sealed_at IS NOT NULL AND validation_evidence_ref IS NOT NULL AND (origin_kind <> 'source' OR received_at IS NOT NULL))),
    CONSTRAINT ck_dataset_versions_rejection_evidence CHECK (state <> 'rejected' OR validation_evidence_ref IS NOT NULL),
    CONSTRAINT ck_dataset_versions_seal_time_order CHECK (sealed_at IS NULL OR sealed_at >= created_at),
    CONSTRAINT ck_dataset_versions_receive_seal_time_order CHECK (received_at IS NULL OR sealed_at IS NULL OR received_at <= sealed_at),
    CONSTRAINT ck_dataset_versions_source_seal_received CHECK (origin_kind <> 'source' OR sealed_at IS NULL OR received_at IS NOT NULL),
    CONSTRAINT ck_dataset_versions_profile_origin_shape CHECK ((dataset_code <> 'profile_records' OR origin_kind = 'source') AND (dataset_code <> 'profile_links' OR origin_kind = 'derived')),
    CONSTRAINT ck_dataset_versions_source_interpretation_no_business_rules CHECK (dataset_code NOT IN ('profile_records', 'profile_links', 'score_standards') OR rule_set_id IS NULL)
);

COMMENT ON TABLE public.dataset_versions IS '不可变数据集版本';

COMMENT ON COLUMN public.dataset_versions.id IS '版本标识';

COMMENT ON COLUMN public.dataset_versions.dataset_code IS '逻辑数据集代码';

COMMENT ON COLUMN public.dataset_versions.origin_kind IS '来源接收或派生产物';

COMMENT ON COLUMN public.dataset_versions.state IS '校验与发布状态';

COMMENT ON COLUMN public.dataset_versions.source_system IS '实际物理来源系统';

COMMENT ON COLUMN public.dataset_versions.source_locator IS '实际交付/表/工作表定位';

COMMENT ON COLUMN public.dataset_versions.row_schema_revision IS '本版本必要投影的字段结构修订';

COMMENT ON COLUMN public.dataset_versions.interpretation_ref IS '已冻结的字段、范围及口径解释证据';

COMMENT ON COLUMN public.dataset_versions.producer_revision IS '实际接收器或计算代码修订';

COMMENT ON COLUMN public.dataset_versions.rule_set_id IS '实际使用的解释/计算规则参数包';

COMMENT ON COLUMN public.dataset_versions.content_fingerprint IS '内容及语义输入的稳定摘要';

COMMENT ON COLUMN public.dataset_versions.retained_input_ref IS '必要原交付的受控留存位置';

COMMENT ON COLUMN public.dataset_versions.upstream_revision IS '上游明确提供的版本/交付修订';

COMMENT ON COLUMN public.dataset_versions.period_start IS '上游或报告明确声明的实际覆盖起日';

COMMENT ON COLUMN public.dataset_versions.period_end IS '明确声明的实际覆盖止日';

COMMENT ON COLUMN public.dataset_versions.source_cursor IS '已核可靠的上游续读位置';

COMMENT ON COLUMN public.dataset_versions.received_at IS '该份source内容首次实际接收完成时刻';

COMMENT ON COLUMN public.dataset_versions.created_at IS '本地候选版本实际创建时刻';

COMMENT ON COLUMN public.dataset_versions.sealed_at IS '内容及输入绑定实际封版时刻';

COMMENT ON COLUMN public.dataset_versions.validation_evidence_ref IS '校验通过或拒绝的脱敏证据';

CREATE TABLE public.listings (
    id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    platform_code TEXT NOT NULL,
    platform_product_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_listings_identity PRIMARY KEY (id),
    CONSTRAINT fk_listings_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_listings_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_listings_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_listings_status_values CHECK (status IN ('active', 'inactive')),
    CONSTRAINT ck_listings_identity_time_order CHECK (updated_at >= created_at),
    CONSTRAINT ck_listings_soft_delete_shape CHECK (deleted_at IS NULL OR (status = 'inactive' AND created_at <= deleted_at AND deleted_at <= updated_at)),
    CONSTRAINT ck_listings_platform_code_core_text CHECK (platform_code IS NULL OR (btrim(platform_code) <> '' AND platform_code = btrim(platform_code))),
    CONSTRAINT ck_listings_platform_product_id_core_text CHECK (platform_product_id IS NULL OR (btrim(platform_product_id) <> '' AND platform_product_id = btrim(platform_product_id))),
    CONSTRAINT uq_listings_platform_shop_product UNIQUE (platform_code, shop_id, platform_product_id)
);

CREATE INDEX ix_listings_query_01 ON public.listings (shop_id, id);

CREATE INDEX ix_listings_query_02 ON public.listings (platform_code, platform_product_id, shop_id);

COMMENT ON TABLE public.listings IS '店铺商品链接';

COMMENT ON COLUMN public.listings.id IS '项目链接ID';

COMMENT ON COLUMN public.listings.shop_id IS '链接所属统一店铺';

COMMENT ON COLUMN public.listings.platform_code IS '已核实的平台命名空间';

COMMENT ON COLUMN public.listings.platform_product_id IS '该店铺内的平台商品ID';

COMMENT ON COLUMN public.listings.status IS '该身份在本项目中的启用状态';

COMMENT ON COLUMN public.listings.created_at IS '本项目首次建立链接身份的时刻';

COMMENT ON COLUMN public.listings.updated_at IS '本项目最后修订链接主记录时刻';

COMMENT ON COLUMN public.listings.deleted_at IS '本项目将该链接身份软删除的时刻';

CREATE TABLE public.aftersales_reports (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    report_type TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    revision_no BIGINT NOT NULL,
    source_report_key TEXT,
    source_label TEXT,
    source_generated_text TEXT,
    source_generated_at TIMESTAMP WITH TIME ZONE,
    source_generated_time_status TEXT NOT NULL,
    source_generated_time_note TEXT,
    document_url TEXT,
    sheet_url TEXT,
    previous_report_id TEXT,
    previous_comparison_status TEXT NOT NULL,
    previous_comparison_reason TEXT,
    yoy_report_id TEXT,
    yoy_comparison_status TEXT NOT NULL,
    yoy_comparison_reason TEXT,
    evidence_note TEXT,
    CONSTRAINT pk_aftersales_reports_identity PRIMARY KEY (id),
    CONSTRAINT fk_aftersales_reports_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_aftersales_reports_previous_report_id FOREIGN KEY(previous_report_id) REFERENCES public.aftersales_reports (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_aftersales_reports_yoy_report_id FOREIGN KEY(yoy_report_id) REFERENCES public.aftersales_reports (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_aftersales_reports_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_aftersales_reports_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_aftersales_reports_previous_report_id_core_text CHECK (previous_report_id IS NULL OR (btrim(previous_report_id) <> '' AND previous_report_id = btrim(previous_report_id))),
    CONSTRAINT ck_aftersales_reports_yoy_report_id_core_text CHECK (yoy_report_id IS NULL OR (btrim(yoy_report_id) <> '' AND yoy_report_id = btrim(yoy_report_id))),
    CONSTRAINT ck_aftersales_reports_report_type_text_nonblank CHECK (report_type IS NULL OR btrim(report_type) <> ''),
    CONSTRAINT ck_aftersales_reports_source_report_key_text_nonblank CHECK (source_report_key IS NULL OR btrim(source_report_key) <> ''),
    CONSTRAINT ck_aftersales_reports_source_label_text_nonblank CHECK (source_label IS NULL OR btrim(source_label) <> ''),
    CONSTRAINT ck_aftersales_reports_source_generated_text_text_nonblank CHECK (source_generated_text IS NULL OR btrim(source_generated_text) <> ''),
    CONSTRAINT ck_aftersales_reports_source_generated_time_status_t_0e44b57172 CHECK (source_generated_time_status IS NULL OR btrim(source_generated_time_status) <> ''),
    CONSTRAINT ck_aftersales_reports_source_generated_time_note_text_nonblank CHECK (source_generated_time_note IS NULL OR btrim(source_generated_time_note) <> ''),
    CONSTRAINT ck_aftersales_reports_document_url_text_nonblank CHECK (document_url IS NULL OR btrim(document_url) <> ''),
    CONSTRAINT ck_aftersales_reports_sheet_url_text_nonblank CHECK (sheet_url IS NULL OR btrim(sheet_url) <> ''),
    CONSTRAINT ck_aftersales_reports_previous_comparison_status_text_nonblank CHECK (previous_comparison_status IS NULL OR btrim(previous_comparison_status) <> ''),
    CONSTRAINT ck_aftersales_reports_previous_comparison_reason_text_nonblank CHECK (previous_comparison_reason IS NULL OR btrim(previous_comparison_reason) <> ''),
    CONSTRAINT ck_aftersales_reports_yoy_comparison_status_text_nonblank CHECK (yoy_comparison_status IS NULL OR btrim(yoy_comparison_status) <> ''),
    CONSTRAINT ck_aftersales_reports_yoy_comparison_reason_text_nonblank CHECK (yoy_comparison_reason IS NULL OR btrim(yoy_comparison_reason) <> ''),
    CONSTRAINT ck_aftersales_reports_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT uq_aftersales_reports_result_version UNIQUE (dataset_version_id),
    CONSTRAINT uq_aftersales_reports_logical_period_revision UNIQUE (report_type, period_start, period_end, revision_no),
    CONSTRAINT ck_aftersales_reports_report_type_values CHECK (report_type IN ('weekly', 'monthly', 'custom')),
    CONSTRAINT ck_aftersales_reports_period_order CHECK (period_start <= period_end),
    CONSTRAINT ck_aftersales_reports_revision_positive CHECK (revision_no >= 1),
    CONSTRAINT ck_aftersales_reports_source_generated_time_status_values CHECK (source_generated_time_status IN ('confirmed', 'unparsed', 'unknown')),
    CONSTRAINT ck_aftersales_reports_source_generated_time_shape CHECK ((source_generated_time_status = 'confirmed' AND source_generated_at IS NOT NULL) OR (source_generated_time_status = 'unparsed' AND source_generated_text IS NOT NULL AND source_generated_at IS NULL AND source_generated_time_note IS NOT NULL) OR (source_generated_time_status = 'unknown' AND source_generated_text IS NULL AND source_generated_at IS NULL)),
    CONSTRAINT ck_aftersales_reports_previous_comparison_status_values CHECK (previous_comparison_status IN ('available', 'missing', 'incompatible', 'pending')),
    CONSTRAINT ck_aftersales_reports_previous_comparison_shape CHECK ((previous_comparison_status = 'available' AND previous_report_id IS NOT NULL) OR (previous_comparison_status IN ('missing', 'pending') AND previous_report_id IS NULL AND previous_comparison_reason IS NOT NULL) OR (previous_comparison_status = 'incompatible' AND previous_comparison_reason IS NOT NULL)),
    CONSTRAINT ck_aftersales_reports_previous_not_self CHECK (previous_report_id IS NULL OR previous_report_id <> id),
    CONSTRAINT ck_aftersales_reports_yoy_comparison_status_values CHECK (yoy_comparison_status IN ('available', 'missing', 'incompatible', 'pending')),
    CONSTRAINT ck_aftersales_reports_yoy_comparison_shape CHECK ((yoy_comparison_status = 'available' AND yoy_report_id IS NOT NULL) OR (yoy_comparison_status IN ('missing', 'pending') AND yoy_report_id IS NULL AND yoy_comparison_reason IS NOT NULL) OR (yoy_comparison_status = 'incompatible' AND yoy_comparison_reason IS NOT NULL)),
    CONSTRAINT ck_aftersales_reports_yoy_not_self CHECK (yoy_report_id IS NULL OR yoy_report_id <> id)
);

CREATE INDEX ix_aftersales_reports_query_17 ON public.aftersales_reports (report_type, period_end DESC, period_start DESC, revision_no DESC, id);

COMMENT ON TABLE public.aftersales_reports IS '售后报告头';

COMMENT ON COLUMN public.aftersales_reports.id IS '本份报告修订标识';

COMMENT ON COLUMN public.aftersales_reports.dataset_version_id IS '本报告固定输入及规则的结果版本';

COMMENT ON COLUMN public.aftersales_reports.report_type IS '实际报告类型';

COMMENT ON COLUMN public.aftersales_reports.period_start IS '本报告选定起日，含当日';

COMMENT ON COLUMN public.aftersales_reports.period_end IS '本报告选定止日，含当日';

COMMENT ON COLUMN public.aftersales_reports.revision_no IS '同类型/期间内本地修订序号';

COMMENT ON COLUMN public.aftersales_reports.source_report_key IS '来源已有的报告身份';

COMMENT ON COLUMN public.aftersales_reports.source_label IS '来源报告原标签';

COMMENT ON COLUMN public.aftersales_reports.source_generated_text IS '来源实际生成时间原文本';

COMMENT ON COLUMN public.aftersales_reports.source_generated_at IS '有依据规范化的来源生成时刻';

COMMENT ON COLUMN public.aftersales_reports.source_generated_time_status IS '来源生成时间解释状态';

COMMENT ON COLUMN public.aftersales_reports.source_generated_time_note IS '原时间无法规范化的原因/解释';

COMMENT ON COLUMN public.aftersales_reports.document_url IS '实际飞书报告文档链接';

COMMENT ON COLUMN public.aftersales_reports.sheet_url IS '实际分析表格链接';

COMMENT ON COLUMN public.aftersales_reports.previous_report_id IS '本次实际绑定的同类型上期报告';

COMMENT ON COLUMN public.aftersales_reports.previous_comparison_status IS '上期比较可用状态';

COMMENT ON COLUMN public.aftersales_reports.previous_comparison_reason IS '上期缺失/不相容/未判定原因';

COMMENT ON COLUMN public.aftersales_reports.yoy_report_id IS '本次实际绑定的去年同类型同期报告';

COMMENT ON COLUMN public.aftersales_reports.yoy_comparison_status IS '去年比较可用状态';

COMMENT ON COLUMN public.aftersales_reports.yoy_comparison_reason IS '去年比较缺失或不相容原因';

COMMENT ON COLUMN public.aftersales_reports.evidence_note IS '真实范围、修订、比较及必要限制';

CREATE TABLE public.coverage_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    shop_id TEXT,
    channel_code TEXT,
    listing_id TEXT,
    field_code TEXT,
    window_start DATE,
    window_end DATE,
    availability_state TEXT NOT NULL,
    completeness_state TEXT NOT NULL,
    complete_through DATE,
    expected_record_count BIGINT,
    expectation_ref TEXT,
    reason_code TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_coverage_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_coverage_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_coverage_records_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_coverage_records_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_coverage_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_coverage_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_coverage_records_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_coverage_records_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT ck_coverage_records_field_code_core_text CHECK (field_code IS NULL OR (btrim(field_code) <> '')),
    CONSTRAINT ck_coverage_records_expectation_ref_core_text CHECK (expectation_ref IS NULL OR (btrim(expectation_ref) <> '')),
    CONSTRAINT ck_coverage_records_reason_code_core_text CHECK (reason_code IS NULL OR (btrim(reason_code) <> '')),
    CONSTRAINT ck_coverage_records_evidence_ref_core_text CHECK (evidence_ref IS NULL OR (btrim(evidence_ref) <> '')),
    CONSTRAINT ck_coverage_records_scope_kind_values CHECK (scope_kind IN ('dataset', 'shop', 'shop_channel', 'listing')),
    CONSTRAINT ck_coverage_records_channel_code_values CHECK (channel_code IN ('product_promotion', 'star_shop')),
    CONSTRAINT ck_coverage_records_availability_state_values CHECK (availability_state IN ('observed', 'no_records', 'not_received', 'field_null', 'not_applicable')),
    CONSTRAINT ck_coverage_records_completeness_state_values CHECK (completeness_state IN ('unknown', 'partial', 'complete', 'not_applicable')),
    CONSTRAINT uq_coverage_records_version_scope_field_window UNIQUE NULLS NOT DISTINCT (dataset_version_id, scope_kind, shop_id, channel_code, listing_id, field_code, window_start, window_end),
    CONSTRAINT ck_coverage_records_window_start_window_end_core_pair CHECK ((window_start IS NULL AND window_end IS NULL) OR (window_start IS NOT NULL AND window_end IS NOT NULL AND window_start <= window_end)),
    CONSTRAINT ck_coverage_records_scope_shape CHECK ((scope_kind = 'dataset' AND shop_id IS NULL AND channel_code IS NULL AND listing_id IS NULL) OR (scope_kind = 'shop' AND shop_id IS NOT NULL AND channel_code IS NULL AND listing_id IS NULL) OR (scope_kind = 'shop_channel' AND shop_id IS NOT NULL AND channel_code IS NOT NULL AND listing_id IS NULL) OR (scope_kind = 'listing' AND shop_id IS NULL AND channel_code IS NULL AND listing_id IS NOT NULL)),
    CONSTRAINT ck_coverage_records_null_field_present CHECK (availability_state <> 'field_null' OR field_code IS NOT NULL),
    CONSTRAINT ck_coverage_records_expected_count_nonnegative CHECK (expected_record_count IS NULL OR expected_record_count >= 0),
    CONSTRAINT ck_coverage_records_complete_within_window CHECK (complete_through IS NULL OR window_end IS NULL OR complete_through <= window_end),
    CONSTRAINT ck_coverage_records_expectation_required CHECK ((completeness_state <> 'complete' AND complete_through IS NULL AND expected_record_count IS NULL) OR expectation_ref IS NOT NULL)
);

COMMENT ON TABLE public.coverage_records IS '固定版本范围的独立覆盖证据';

COMMENT ON COLUMN public.coverage_records.id IS '覆盖证据标识';

COMMENT ON COLUMN public.coverage_records.dataset_version_id IS '被判断的固定版本';

COMMENT ON COLUMN public.coverage_records.scope_kind IS '证据适用对象层级';

COMMENT ON COLUMN public.coverage_records.shop_id IS '店铺范围';

COMMENT ON COLUMN public.coverage_records.channel_code IS '店铺渠道范围';

COMMENT ON COLUMN public.coverage_records.listing_id IS '商品链接范围';

COMMENT ON COLUMN public.coverage_records.field_code IS '必要字段级证据对象';

COMMENT ON COLUMN public.coverage_records.window_start IS '本证据覆盖窗口起日';

COMMENT ON COLUMN public.coverage_records.window_end IS '本证据覆盖窗口止日';

COMMENT ON COLUMN public.coverage_records.availability_state IS '数据存在/缺失的具体状态';

COMMENT ON COLUMN public.coverage_records.completeness_state IS '此范围是否完整';

COMMENT ON COLUMN public.coverage_records.complete_through IS '有独立证明的完整截至日';

COMMENT ON COLUMN public.coverage_records.expected_record_count IS '上游/业务约定明确提供的应有记录数';

COMMENT ON COLUMN public.coverage_records.expectation_ref IS '应有范围、行数或完整日的权威依据';

COMMENT ON COLUMN public.coverage_records.reason_code IS '该状态的稳定解释代码';

COMMENT ON COLUMN public.coverage_records.evidence_ref IS '这次覆盖判断的脱敏证明';

COMMENT ON COLUMN public.coverage_records.checked_at IS '判断实际完成时刻';

CREATE TABLE public.current_datasets (
    dataset_code TEXT NOT NULL,
    version_id TEXT NOT NULL,
    revision BIGINT DEFAULT 0 NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_current_datasets_identity PRIMARY KEY (dataset_code),
    CONSTRAINT fk_current_datasets_version_id FOREIGN KEY(version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_current_datasets_dataset_code_core_text CHECK (dataset_code IS NULL OR (btrim(dataset_code) <> '' AND dataset_code = btrim(dataset_code))),
    CONSTRAINT ck_current_datasets_version_id_core_text CHECK (version_id IS NULL OR (btrim(version_id) <> '' AND version_id = btrim(version_id))),
    CONSTRAINT ck_current_datasets_revision_nonnegative CHECK (revision >= 0)
);

COMMENT ON TABLE public.current_datasets IS '逻辑数据集当前入口';

COMMENT ON COLUMN public.current_datasets.dataset_code IS '逻辑数据集代码';

COMMENT ON COLUMN public.current_datasets.version_id IS '当前已发布版本';

COMMENT ON COLUMN public.current_datasets.revision IS '该入口的并发修订号';

COMMENT ON COLUMN public.current_datasets.updated_at IS '入口实际成功切换时刻';

CREATE TABLE public.product_daily_facts (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    listing_id TEXT,
    listing_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_product_id TEXT,
    source_product_name TEXT,
    source_dt_raw TEXT,
    business_date DATE,
    date_status TEXT NOT NULL,
    source_related_date_raw TEXT,
    views BIGINT,
    visitors BIGINT,
    favorites BIGINT,
    buyers BIGINT,
    orders BIGINT,
    units BIGINT,
    product_revenue NUMERIC,
    source_conversion_rate NUMERIC,
    source_first_category TEXT,
    source_second_category TEXT,
    source_third_category TEXT,
    source_created_time_raw TEXT,
    source_created_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_product_daily_facts_identity PRIMARY KEY (id),
    CONSTRAINT fk_product_daily_facts_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_daily_facts_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_daily_facts_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_product_daily_facts_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_product_daily_facts_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_product_daily_facts_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT ck_product_daily_facts_product_revenue_core_finite CHECK (product_revenue IS NULL OR product_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_daily_facts_source_conversion_rate_core_finite CHECK (source_conversion_rate IS NULL OR source_conversion_rate NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_product_daily_facts_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_product_daily_facts_listing_match_status_values CHECK (listing_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_product_daily_facts_listing_match_shape CHECK ((listing_match_status = 'matched' AND listing_id IS NOT NULL) OR (listing_match_status <> 'matched' AND listing_id IS NULL)),
    CONSTRAINT ck_product_daily_facts_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_product_daily_facts_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND business_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND business_date IS NULL))
);

CREATE INDEX ix_product_daily_facts_query_04 ON public.product_daily_facts (dataset_version_id, business_date, listing_id, id);

CREATE UNIQUE INDEX ux_product_daily_facts_version_listing_date ON public.product_daily_facts (dataset_version_id, listing_id, business_date) WHERE listing_id IS NOT NULL AND business_date IS NOT NULL;

COMMENT ON TABLE public.product_daily_facts IS '商品日经营原行';

COMMENT ON COLUMN public.product_daily_facts.id IS '本项目事实行ID';

COMMENT ON COLUMN public.product_daily_facts.dataset_version_id IS '可重读的商品日输入版本';

COMMENT ON COLUMN public.product_daily_facts.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.product_daily_facts.source_record_id IS '来源z_id原标识';

COMMENT ON COLUMN public.product_daily_facts.listing_id IS '已核实的店铺商品链接';

COMMENT ON COLUMN public.product_daily_facts.listing_match_status IS '链接身份匹配状态';

COMMENT ON COLUMN public.product_daily_facts.source_shop_id IS '来源shop_id原值';

COMMENT ON COLUMN public.product_daily_facts.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.product_daily_facts.source_product_id IS '来源spu_id原值';

COMMENT ON COLUMN public.product_daily_facts.source_product_name IS '来源spu_name观察值';

COMMENT ON COLUMN public.product_daily_facts.source_dt_raw IS '来源dt原表示';

COMMENT ON COLUMN public.product_daily_facts.business_date IS '来源dt对应的日标签';

COMMENT ON COLUMN public.product_daily_facts.date_status IS '行日期解析/核验状态';

COMMENT ON COLUMN public.product_daily_facts.source_related_date_raw IS 'relevant_dt原值';

COMMENT ON COLUMN public.product_daily_facts.views IS '商品浏览量product_pv';

COMMENT ON COLUMN public.product_daily_facts.visitors IS '商品访客product_uv';

COMMENT ON COLUMN public.product_daily_facts.favorites IS '收藏商品人数favorite_product_users';

COMMENT ON COLUMN public.product_daily_facts.buyers IS '成交买家transaction_users';

COMMENT ON COLUMN public.product_daily_facts.orders IS '成交订单transaction_orders';

COMMENT ON COLUMN public.product_daily_facts.units IS '支付件数transaction_products';

COMMENT ON COLUMN public.product_daily_facts.product_revenue IS '商品成交金额transaction_amount';

COMMENT ON COLUMN public.product_daily_facts.source_conversion_rate IS '来源transaction_conversion_rate';

COMMENT ON COLUMN public.product_daily_facts.source_first_category IS '来源平台一级类目观察值';

COMMENT ON COLUMN public.product_daily_facts.source_second_category IS '来源平台二级类目观察值';

COMMENT ON COLUMN public.product_daily_facts.source_third_category IS '来源平台三级类目观察值';

COMMENT ON COLUMN public.product_daily_facts.source_created_time_raw IS '来源create_time原表示';

COMMENT ON COLUMN public.product_daily_facts.source_created_at IS '已有时区依据的来源创建时间';

CREATE TABLE public.product_promotion_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    listing_id TEXT,
    listing_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_product_id TEXT,
    source_product_name TEXT,
    source_dt_raw TEXT,
    source_start_dt_raw TEXT,
    source_granularity TEXT,
    period_start DATE,
    period_end DATE,
    date_status TEXT NOT NULL,
    source_grouping TEXT,
    promotion_name TEXT,
    promotion_type TEXT,
    bidding_method TEXT,
    source_deleted_label TEXT,
    spend NUMERIC,
    transaction_spend NUMERIC,
    attributed_revenue NUMERIC,
    attributed_deals NUMERIC,
    net_attributed_revenue NUMERIC,
    net_attributed_deals NUMERIC,
    settled_attributed_revenue NUMERIC,
    settled_attributed_deals NUMERIC,
    impressions NUMERIC,
    clicks NUMERIC,
    source_roi NUMERIC,
    source_net_roi NUMERIC,
    source_settlement_roi NUMERIC,
    source_ctr NUMERIC,
    source_cvr NUMERIC,
    source_created_time_raw TEXT,
    source_created_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_product_promotion_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_product_promotion_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_promotion_records_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_promotion_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_product_promotion_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_product_promotion_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_product_promotion_records_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT ck_product_promotion_records_spend_core_finite CHECK (spend IS NULL OR spend NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_transaction_spend_core_finite CHECK (transaction_spend IS NULL OR transaction_spend NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_attributed_revenue_core_finite CHECK (attributed_revenue IS NULL OR attributed_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_attributed_deals_core_finite CHECK (attributed_deals IS NULL OR attributed_deals NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_net_attributed_revenue_core_finite CHECK (net_attributed_revenue IS NULL OR net_attributed_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_net_attributed_deals_core_finite CHECK (net_attributed_deals IS NULL OR net_attributed_deals NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_settled_attributed_reve_392fb5a2c1 CHECK (settled_attributed_revenue IS NULL OR settled_attributed_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_settled_attributed_deal_dab0cab760 CHECK (settled_attributed_deals IS NULL OR settled_attributed_deals NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_impressions_core_finite CHECK (impressions IS NULL OR impressions NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_clicks_core_finite CHECK (clicks IS NULL OR clicks NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_source_roi_core_finite CHECK (source_roi IS NULL OR source_roi NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_source_net_roi_core_finite CHECK (source_net_roi IS NULL OR source_net_roi NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_source_settlement_roi_core_finite CHECK (source_settlement_roi IS NULL OR source_settlement_roi NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_source_ctr_core_finite CHECK (source_ctr IS NULL OR source_ctr NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_promotion_records_source_cvr_core_finite CHECK (source_cvr IS NULL OR source_cvr NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_product_promotion_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_product_promotion_records_listing_match_status_values CHECK (listing_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_product_promotion_records_listing_match_shape CHECK ((listing_match_status = 'matched' AND listing_id IS NOT NULL) OR (listing_match_status <> 'matched' AND listing_id IS NULL)),
    CONSTRAINT ck_product_promotion_records_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'not_provided', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_product_promotion_records_period_pair_order CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_product_promotion_records_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND period_start IS NOT NULL AND period_end IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND period_start IS NULL AND period_end IS NULL)),
    CONSTRAINT ck_product_promotion_records_day_period_same_date CHECK (source_granularity IS DISTINCT FROM 'day' OR period_start IS NULL OR period_start = period_end)
);

CREATE INDEX ix_product_promotion_records_query_06 ON public.product_promotion_records (dataset_version_id, source_granularity, period_end, listing_id, id);

CREATE INDEX ix_product_promotion_records_query_07 ON public.product_promotion_records (dataset_version_id, listing_id, source_granularity, period_end, id);

COMMENT ON TABLE public.product_promotion_records IS '商品推广原行';

COMMENT ON COLUMN public.product_promotion_records.id IS '推广原行ID';

COMMENT ON COLUMN public.product_promotion_records.dataset_version_id IS '推广输入版本';

COMMENT ON COLUMN public.product_promotion_records.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.product_promotion_records.source_record_id IS '来源id原标识';

COMMENT ON COLUMN public.product_promotion_records.listing_id IS '已核实商品链接';

COMMENT ON COLUMN public.product_promotion_records.listing_match_status IS '链接匹配状态';

COMMENT ON COLUMN public.product_promotion_records.source_shop_id IS '原shop_id';

COMMENT ON COLUMN public.product_promotion_records.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.product_promotion_records.source_product_id IS '原spu_id';

COMMENT ON COLUMN public.product_promotion_records.source_product_name IS '原spu_name';

COMMENT ON COLUMN public.product_promotion_records.source_dt_raw IS '原dt表示';

COMMENT ON COLUMN public.product_promotion_records.source_start_dt_raw IS '原start_dt表示';

COMMENT ON COLUMN public.product_promotion_records.source_granularity IS '原time_granularity';

COMMENT ON COLUMN public.product_promotion_records.period_start IS '已解释的实际统计起日';

COMMENT ON COLUMN public.product_promotion_records.period_end IS '已解释的实际统计末日';

COMMENT ON COLUMN public.product_promotion_records.date_status IS '期间解析/核验状态';

COMMENT ON COLUMN public.product_promotion_records.source_grouping IS '来源spu_grouping';

COMMENT ON COLUMN public.product_promotion_records.promotion_name IS '来源推广名称';

COMMENT ON COLUMN public.product_promotion_records.promotion_type IS '来源推广场景/方式';

COMMENT ON COLUMN public.product_promotion_records.bidding_method IS '来源出价方式';

COMMENT ON COLUMN public.product_promotion_records.source_deleted_label IS '来源is_deleted原标签';

COMMENT ON COLUMN public.product_promotion_records.spend IS '总花费total_cost';

COMMENT ON COLUMN public.product_promotion_records.transaction_spend IS '成交花费transaction_cost';

COMMENT ON COLUMN public.product_promotion_records.attributed_revenue IS '总归因transaction_amount';

COMMENT ON COLUMN public.product_promotion_records.attributed_deals IS '总归因transaction_count';

COMMENT ON COLUMN public.product_promotion_records.net_attributed_revenue IS '净归因net_transaction_amount';

COMMENT ON COLUMN public.product_promotion_records.net_attributed_deals IS '净归因net_transaction_count';

COMMENT ON COLUMN public.product_promotion_records.settled_attributed_revenue IS '结算归因金额';

COMMENT ON COLUMN public.product_promotion_records.settled_attributed_deals IS '结算归因笔数';

COMMENT ON COLUMN public.product_promotion_records.impressions IS 'impression_count广告曝光量';

COMMENT ON COLUMN public.product_promotion_records.clicks IS 'click_count广告点击量';

COMMENT ON COLUMN public.product_promotion_records.source_roi IS '来源actual_roi';

COMMENT ON COLUMN public.product_promotion_records.source_net_roi IS '来源net_actual_roi';

COMMENT ON COLUMN public.product_promotion_records.source_settlement_roi IS '来源settlement_roi';

COMMENT ON COLUMN public.product_promotion_records.source_ctr IS '来源click_through_rate';

COMMENT ON COLUMN public.product_promotion_records.source_cvr IS '来源click_conversion_rate';

COMMENT ON COLUMN public.product_promotion_records.source_created_time_raw IS '来源创建时间原表示';

COMMENT ON COLUMN public.product_promotion_records.source_created_at IS '有依据的来源创建时间';

CREATE TABLE public.product_region_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_style_code TEXT,
    source_product_code TEXT,
    source_product_name TEXT,
    source_color_specification TEXT,
    source_product_category TEXT,
    source_virtual_category TEXT,
    source_region_name TEXT,
    source_dt_raw TEXT,
    source_granularity TEXT,
    period_start DATE,
    period_end DATE,
    date_status TEXT NOT NULL,
    actual_shipped_qty NUMERIC,
    actual_shipped_amount NUMERIC,
    actual_returned_qty NUMERIC,
    actual_returned_amount NUMERIC,
    source_refund_rate NUMERIC,
    source_updated_time_raw TEXT,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_product_region_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_product_region_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_region_records_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_region_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_product_region_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_product_region_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_product_region_records_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_product_region_records_actual_shipped_qty_core_finite CHECK (actual_shipped_qty IS NULL OR actual_shipped_qty NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_region_records_actual_shipped_amount_core_finite CHECK (actual_shipped_amount IS NULL OR actual_shipped_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_region_records_actual_returned_qty_core_finite CHECK (actual_returned_qty IS NULL OR actual_returned_qty NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_region_records_actual_returned_amount_core_finite CHECK (actual_returned_amount IS NULL OR actual_returned_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_region_records_source_refund_rate_core_finite CHECK (source_refund_rate IS NULL OR source_refund_rate NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_product_region_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_product_region_records_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_product_region_records_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_product_region_records_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'not_provided', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_product_region_records_period_pair_order CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_product_region_records_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND period_start IS NOT NULL AND period_end IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND period_start IS NULL AND period_end IS NULL)),
    CONSTRAINT ck_product_region_records_day_period_same_date CHECK (source_granularity IS DISTINCT FROM 'day' OR period_start IS NULL OR period_start = period_end)
);

CREATE INDEX ix_product_region_records_query_12 ON public.product_region_records (dataset_version_id, source_granularity, period_end, shop_id, id);

COMMENT ON TABLE public.product_region_records IS '产品地区实发实退原行';

COMMENT ON COLUMN public.product_region_records.id IS '产品地区原行ID';

COMMENT ON COLUMN public.product_region_records.dataset_version_id IS '聚水潭地区输入版本';

COMMENT ON COLUMN public.product_region_records.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.product_region_records.source_record_id IS '来源id';

COMMENT ON COLUMN public.product_region_records.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.product_region_records.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.product_region_records.source_shop_id IS '原shop_id';

COMMENT ON COLUMN public.product_region_records.source_shop_name IS '原shop_name';

COMMENT ON COLUMN public.product_region_records.source_style_code IS '原style_code';

COMMENT ON COLUMN public.product_region_records.source_product_code IS '原product_code';

COMMENT ON COLUMN public.product_region_records.source_product_name IS '原product_name';

COMMENT ON COLUMN public.product_region_records.source_color_specification IS '原color_specification';

COMMENT ON COLUMN public.product_region_records.source_product_category IS '原product_category';

COMMENT ON COLUMN public.product_region_records.source_virtual_category IS '原virtual_category';

COMMENT ON COLUMN public.product_region_records.source_region_name IS '原province';

COMMENT ON COLUMN public.product_region_records.source_dt_raw IS '原dt表示';

COMMENT ON COLUMN public.product_region_records.source_granularity IS '原time_granularity';

COMMENT ON COLUMN public.product_region_records.period_start IS '已解释的实际统计起日';

COMMENT ON COLUMN public.product_region_records.period_end IS '已解释的实际统计末日';

COMMENT ON COLUMN public.product_region_records.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.product_region_records.actual_shipped_qty IS '来源实发件数';

COMMENT ON COLUMN public.product_region_records.actual_shipped_amount IS '来源实发金额';

COMMENT ON COLUMN public.product_region_records.actual_returned_qty IS '来源实退件数';

COMMENT ON COLUMN public.product_region_records.actual_returned_amount IS '来源实退金额';

COMMENT ON COLUMN public.product_region_records.source_refund_rate IS '来源refund_rate核对值';

COMMENT ON COLUMN public.product_region_records.source_updated_time_raw IS '来源updated_at原表示';

COMMENT ON COLUMN public.product_region_records.source_updated_at IS '可解释的来源更新时间';

CREATE TABLE public.product_sales_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    row_kind TEXT NOT NULL,
    source_row_kind TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_style_code TEXT,
    source_product_code TEXT,
    source_product_name TEXT,
    source_color_specification TEXT,
    source_product_category TEXT,
    source_subcategory TEXT,
    source_virtual_category TEXT,
    source_period_label TEXT,
    period_start DATE,
    period_end DATE,
    date_status TEXT NOT NULL,
    sales_amount NUMERIC,
    source_sales_qty NUMERIC,
    CONSTRAINT pk_product_sales_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_product_sales_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_sales_records_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_sales_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_product_sales_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_product_sales_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_product_sales_records_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_product_sales_records_sales_amount_core_finite CHECK (sales_amount IS NULL OR sales_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_sales_records_source_sales_qty_core_finite CHECK (source_sales_qty IS NULL OR source_sales_qty NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_product_sales_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_product_sales_records_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_product_sales_records_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_product_sales_records_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'not_provided', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_product_sales_records_period_pair_order CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_product_sales_records_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND period_start IS NOT NULL AND period_end IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND period_start IS NULL AND period_end IS NULL)),
    CONSTRAINT ck_product_sales_records_row_kind_values CHECK (row_kind IN ('detail', 'subtotal', 'total', 'unclassified'))
);

CREATE INDEX ix_product_sales_records_query_13 ON public.product_sales_records (dataset_version_id, source_style_code);

COMMENT ON TABLE public.product_sales_records IS '商品销售履约期间原行';

COMMENT ON COLUMN public.product_sales_records.id IS '销售期间原行ID';

COMMENT ON COLUMN public.product_sales_records.dataset_version_id IS '一份J2或J3类型化事实版本';

COMMENT ON COLUMN public.product_sales_records.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.product_sales_records.source_record_id IS '来源原生行ID';

COMMENT ON COLUMN public.product_sales_records.row_kind IS '来源行的明细/合计性质';

COMMENT ON COLUMN public.product_sales_records.source_row_kind IS '来源明确提供的行类型标签';

COMMENT ON COLUMN public.product_sales_records.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.product_sales_records.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.product_sales_records.source_shop_id IS '来源店铺ID';

COMMENT ON COLUMN public.product_sales_records.source_shop_name IS '来源店铺标签';

COMMENT ON COLUMN public.product_sales_records.source_style_code IS '来源款号/款式编码';

COMMENT ON COLUMN public.product_sales_records.source_product_code IS '来源商品编码';

COMMENT ON COLUMN public.product_sales_records.source_product_name IS '正式商品名称原值';

COMMENT ON COLUMN public.product_sales_records.source_color_specification IS '来源颜色规格';

COMMENT ON COLUMN public.product_sales_records.source_product_category IS '来源产品分类';

COMMENT ON COLUMN public.product_sales_records.source_subcategory IS '来源实际参与分组/搜索的子分类原值';

COMMENT ON COLUMN public.product_sales_records.source_virtual_category IS '来源虚拟分类';

COMMENT ON COLUMN public.product_sales_records.source_period_label IS '来源本行期间原标签';

COMMENT ON COLUMN public.product_sales_records.period_start IS '本行明确提供的统计起日';

COMMENT ON COLUMN public.product_sales_records.period_end IS '本行明确提供的统计末日';

COMMENT ON COLUMN public.product_sales_records.date_status IS '本行期间核验状态';

COMMENT ON COLUMN public.product_sales_records.sales_amount IS '本版本角色对应的来源销售金额';

COMMENT ON COLUMN public.product_sales_records.source_sales_qty IS '本版本角色对应的来源销售数量';

CREATE TABLE public.profile_source_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    source_product_name_raw TEXT,
    source_category_raw TEXT,
    source_level_raw TEXT,
    source_operator_raw TEXT,
    source_listed_at_raw TEXT,
    source_listing_status_raw TEXT,
    source_style_codes_raw TEXT,
    source_listing_ids_official_raw TEXT,
    source_listing_ids_apparel_raw TEXT,
    source_listing_ids_outdoor_raw TEXT,
    source_listing_ids_raingear_raw TEXT,
    source_listing_ids_luggage_raw TEXT,
    source_listing_ids_kids_footwear_raw TEXT,
    CONSTRAINT pk_profile_source_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_profile_source_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_profile_source_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_profile_source_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_profile_source_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT uq_profile_source_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_profile_source_records_source_record_id_core_text CHECK (source_record_id IS NULL OR (btrim(source_record_id) <> '' AND source_record_id = btrim(source_record_id)))
);

COMMENT ON TABLE public.profile_source_records IS '商品资料原行';

COMMENT ON COLUMN public.profile_source_records.id IS '本版本中的资料原行ID';

COMMENT ON COLUMN public.profile_source_records.dataset_version_id IS '固定必要原行投影的来源资料版本';

COMMENT ON COLUMN public.profile_source_records.source_row_key IS '本份输入内可重读的原行定位键';

COMMENT ON COLUMN public.profile_source_records.source_record_id IS '来源原生主键，如W01的__id';

COMMENT ON COLUMN public.profile_source_records.source_product_name_raw IS '运营商品名原格';

COMMENT ON COLUMN public.profile_source_records.source_category_raw IS '运营品类原格';

COMMENT ON COLUMN public.profile_source_records.source_level_raw IS '产品等级原格';

COMMENT ON COLUMN public.profile_source_records.source_operator_raw IS '负责运营原标签';

COMMENT ON COLUMN public.profile_source_records.source_listed_at_raw IS '实际上架日期/时间的原表示';

COMMENT ON COLUMN public.profile_source_records.source_listing_status_raw IS '来源上架/下架状态原格';

COMMENT ON COLUMN public.profile_source_records.source_style_codes_raw IS '款号原格完整内容';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_official_raw IS '官方店ID列原格';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_apparel_raw IS '服饰店ID列原格';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_outdoor_raw IS '户外店ID列原格';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_raingear_raw IS '雨具别名列ID原格';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_luggage_raw IS '箱包别名列ID原格';

COMMENT ON COLUMN public.profile_source_records.source_listing_ids_kids_footwear_raw IS '童装童鞋店ID列原格';

CREATE TABLE public.refund_targets (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_target_key TEXT,
    source_scope_label TEXT NOT NULL,
    source_scope_text TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    category_match_mode TEXT NOT NULL,
    style_match_mode TEXT NOT NULL,
    scope_status TEXT NOT NULL,
    unresolved_condition_text TEXT,
    source_period_label TEXT,
    period_kind TEXT NOT NULL,
    period_start DATE,
    period_end DATE,
    period_status TEXT NOT NULL,
    target_rate NUMERIC NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_refund_targets_identity PRIMARY KEY (id),
    CONSTRAINT fk_refund_targets_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_refund_targets_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_refund_targets_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_refund_targets_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_refund_targets_target_rate_core_finite CHECK (target_rate IS NULL OR target_rate NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_refund_targets_source_target_key_text_nonblank CHECK (source_target_key IS NULL OR btrim(source_target_key) <> ''),
    CONSTRAINT ck_refund_targets_source_scope_label_text_nonblank CHECK (source_scope_label IS NULL OR btrim(source_scope_label) <> ''),
    CONSTRAINT ck_refund_targets_source_scope_text_text_nonblank CHECK (source_scope_text IS NULL OR btrim(source_scope_text) <> ''),
    CONSTRAINT ck_refund_targets_scope_kind_text_nonblank CHECK (scope_kind IS NULL OR btrim(scope_kind) <> ''),
    CONSTRAINT ck_refund_targets_category_match_mode_text_nonblank CHECK (category_match_mode IS NULL OR btrim(category_match_mode) <> ''),
    CONSTRAINT ck_refund_targets_style_match_mode_text_nonblank CHECK (style_match_mode IS NULL OR btrim(style_match_mode) <> ''),
    CONSTRAINT ck_refund_targets_scope_status_text_nonblank CHECK (scope_status IS NULL OR btrim(scope_status) <> ''),
    CONSTRAINT ck_refund_targets_unresolved_condition_text_text_nonblank CHECK (unresolved_condition_text IS NULL OR btrim(unresolved_condition_text) <> ''),
    CONSTRAINT ck_refund_targets_source_period_label_text_nonblank CHECK (source_period_label IS NULL OR btrim(source_period_label) <> ''),
    CONSTRAINT ck_refund_targets_period_kind_text_nonblank CHECK (period_kind IS NULL OR btrim(period_kind) <> ''),
    CONSTRAINT ck_refund_targets_period_status_text_nonblank CHECK (period_status IS NULL OR btrim(period_status) <> ''),
    CONSTRAINT ck_refund_targets_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT uq_refund_targets_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_refund_targets_period_start_period_end_pair CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_refund_targets_confirmed_period CHECK (period_status <> 'confirmed' OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_kind <> 'unknown')),
    CONSTRAINT ck_refund_targets_period_kind_values CHECK (period_kind IN ('cumulative_reference', 'natural_month', 'rolling_window', 'custom', 'unknown')),
    CONSTRAINT ck_refund_targets_period_status_values CHECK (period_status IN ('confirmed', 'reference_only', 'unknown', 'conflict')),
    CONSTRAINT ck_refund_targets_scope_kind_values CHECK (scope_kind IN ('all', 'restricted')),
    CONSTRAINT ck_refund_targets_category_match_mode_values CHECK (category_match_mode IN ('all', 'include_union')),
    CONSTRAINT ck_refund_targets_style_match_mode_values CHECK (style_match_mode IN ('all', 'include_union')),
    CONSTRAINT ck_refund_targets_scope_status_values CHECK (scope_status IN ('complete', 'partial', 'unmapped', 'conflict', 'unresolved_condition')),
    CONSTRAINT ck_refund_targets_target_rate_range CHECK (target_rate BETWEEN 0 AND 1),
    CONSTRAINT ck_refund_targets_all_scope_modes CHECK (scope_kind <> 'all' OR (category_match_mode = 'all' AND style_match_mode = 'all')),
    CONSTRAINT ck_refund_targets_complete_no_unresolved_condition CHECK (scope_status <> 'complete' OR unresolved_condition_text IS NULL)
);

COMMENT ON TABLE public.refund_targets IS '退款率目标';

COMMENT ON COLUMN public.refund_targets.id IS '本退款目标记录标识';

COMMENT ON COLUMN public.refund_targets.dataset_version_id IS '数值、原范围及解释的不可变版本';

COMMENT ON COLUMN public.refund_targets.source_row_key IS '原目标记录的确定定位';

COMMENT ON COLUMN public.refund_targets.source_target_key IS '来源已有目标ID';

COMMENT ON COLUMN public.refund_targets.source_scope_label IS '原细类/目标范围简称';

COMMENT ON COLUMN public.refund_targets.source_scope_text IS '包含、排除及原筛选条件全文';

COMMENT ON COLUMN public.refund_targets.scope_kind IS '总体范围是否受限';

COMMENT ON COLUMN public.refund_targets.category_match_mode IS '分类包含集合的算子';

COMMENT ON COLUMN public.refund_targets.style_match_mode IS '款码包含集合的算子';

COMMENT ON COLUMN public.refund_targets.scope_status IS '整份条件及清单核验状态';

COMMENT ON COLUMN public.refund_targets.unresolved_condition_text IS '尚未落入可执行条件的原文';

COMMENT ON COLUMN public.refund_targets.source_period_label IS '来源适用期间原标签';

COMMENT ON COLUMN public.refund_targets.period_kind IS '来源目标期间类型';

COMMENT ON COLUMN public.refund_targets.period_start IS '经核实的目标适用起日';

COMMENT ON COLUMN public.refund_targets.period_end IS '经核实的目标适用止日';

COMMENT ON COLUMN public.refund_targets.period_status IS '目标期间及适用依据状态';

COMMENT ON COLUMN public.refund_targets.target_rate IS '本组总退款率目标';

COMMENT ON COLUMN public.refund_targets.evidence_note IS '数值、期间、清单及范围依据';

CREATE TABLE public.return_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    row_kind TEXT NOT NULL,
    source_row_kind TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_style_code TEXT,
    source_product_code TEXT,
    source_product_name TEXT,
    source_color_specification TEXT,
    source_product_category TEXT,
    source_subcategory TEXT,
    source_virtual_category TEXT,
    source_return_reason TEXT,
    source_analysis_fields JSONB,
    source_aftersale_type TEXT,
    source_aftersale_status TEXT,
    source_period_label TEXT,
    period_start DATE,
    period_end DATE,
    date_status TEXT NOT NULL,
    actual_returned_qty NUMERIC,
    actual_inbound_amount NUMERIC,
    CONSTRAINT pk_return_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_return_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_return_records_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_return_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_return_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_return_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_return_records_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_return_records_actual_returned_qty_core_finite CHECK (actual_returned_qty IS NULL OR actual_returned_qty NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_return_records_actual_inbound_amount_core_finite CHECK (actual_inbound_amount IS NULL OR actual_inbound_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_return_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_return_records_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_return_records_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_return_records_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'not_provided', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_return_records_period_pair_order CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_return_records_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND period_start IS NOT NULL AND period_end IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND period_start IS NULL AND period_end IS NULL)),
    CONSTRAINT ck_return_records_row_kind_values CHECK (row_kind IN ('detail', 'subtotal', 'total', 'unclassified')),
    CONSTRAINT ck_return_records_analysis_fields_text_object CHECK (source_analysis_fields IS NULL OR (jsonb_typeof(source_analysis_fields) = 'object' AND NOT jsonb_path_exists(source_analysis_fields, '$.* ? (@.type() != "string" && @.type() != "null")')))
);

CREATE INDEX ix_return_records_query_14 ON public.return_records (dataset_version_id, source_style_code);

COMMENT ON TABLE public.return_records IS '售后退货期间原行';

COMMENT ON COLUMN public.return_records.id IS '售后原行ID';

COMMENT ON COLUMN public.return_records.dataset_version_id IS 'J1类型化事实版本';

COMMENT ON COLUMN public.return_records.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.return_records.source_record_id IS '来源原生行ID';

COMMENT ON COLUMN public.return_records.row_kind IS '明细/小计/合计性质';

COMMENT ON COLUMN public.return_records.source_row_kind IS '来源实际行类型标签';

COMMENT ON COLUMN public.return_records.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.return_records.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.return_records.source_shop_id IS '来源店铺ID';

COMMENT ON COLUMN public.return_records.source_shop_name IS '来源店铺标签';

COMMENT ON COLUMN public.return_records.source_style_code IS '来源款式编码';

COMMENT ON COLUMN public.return_records.source_product_code IS '来源商品编码';

COMMENT ON COLUMN public.return_records.source_product_name IS '正式商品名称原值';

COMMENT ON COLUMN public.return_records.source_color_specification IS '来源颜色规格';

COMMENT ON COLUMN public.return_records.source_product_category IS '来源产品分类原值';

COMMENT ON COLUMN public.return_records.source_subcategory IS '来源实际参与分组/搜索的子分类原值';

COMMENT ON COLUMN public.return_records.source_virtual_category IS '来源虚拟分类原值';

COMMENT ON COLUMN public.return_records.source_return_reason IS '来源退货原因原文本';

COMMENT ON COLUMN public.return_records.source_analysis_fields IS '实际分类配置需要的额外原文本投影';

COMMENT ON COLUMN public.return_records.source_aftersale_type IS '来源明确给出的售后分类';

COMMENT ON COLUMN public.return_records.source_aftersale_status IS '来源明确给出的售后状态';

COMMENT ON COLUMN public.return_records.source_period_label IS '来源本行期间标签';

COMMENT ON COLUMN public.return_records.period_start IS '本行明确提供的确认统计起日';

COMMENT ON COLUMN public.return_records.period_end IS '本行明确提供的确认统计末日';

COMMENT ON COLUMN public.return_records.date_status IS '本行期间核验状态';

COMMENT ON COLUMN public.return_records.actual_returned_qty IS '实退数量Q';

COMMENT ON COLUMN public.return_records.actual_inbound_amount IS '实际入库金额A';

CREATE TABLE public.sales_targets (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_target_key TEXT,
    source_scope_label TEXT,
    source_period_label TEXT,
    period_kind TEXT NOT NULL,
    period_start DATE,
    period_end DATE,
    period_status TEXT NOT NULL,
    target_amount NUMERIC NOT NULL,
    scope_status TEXT NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_sales_targets_identity PRIMARY KEY (id),
    CONSTRAINT fk_sales_targets_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_sales_targets_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_sales_targets_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_sales_targets_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_sales_targets_target_amount_core_finite CHECK (target_amount IS NULL OR target_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_sales_targets_source_target_key_text_nonblank CHECK (source_target_key IS NULL OR btrim(source_target_key) <> ''),
    CONSTRAINT ck_sales_targets_source_scope_label_text_nonblank CHECK (source_scope_label IS NULL OR btrim(source_scope_label) <> ''),
    CONSTRAINT ck_sales_targets_source_period_label_text_nonblank CHECK (source_period_label IS NULL OR btrim(source_period_label) <> ''),
    CONSTRAINT ck_sales_targets_period_kind_text_nonblank CHECK (period_kind IS NULL OR btrim(period_kind) <> ''),
    CONSTRAINT ck_sales_targets_period_status_text_nonblank CHECK (period_status IS NULL OR btrim(period_status) <> ''),
    CONSTRAINT ck_sales_targets_scope_status_text_nonblank CHECK (scope_status IS NULL OR btrim(scope_status) <> ''),
    CONSTRAINT ck_sales_targets_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT uq_sales_targets_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_sales_targets_period_start_period_end_pair CHECK ((period_start IS NULL AND period_end IS NULL) OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)),
    CONSTRAINT ck_sales_targets_confirmed_period CHECK (period_status <> 'confirmed' OR (period_start IS NOT NULL AND period_end IS NOT NULL AND period_kind <> 'unknown')),
    CONSTRAINT ck_sales_targets_period_kind_values CHECK (period_kind IN ('month', 'year', 'custom', 'unknown')),
    CONSTRAINT ck_sales_targets_period_status_values CHECK (period_status IN ('confirmed', 'unknown', 'conflict')),
    CONSTRAINT ck_sales_targets_scope_status_values CHECK (scope_status IN ('complete', 'partial', 'unmapped', 'conflict')),
    CONSTRAINT ck_sales_targets_target_amount_nonnegative CHECK (target_amount >= 0)
);

COMMENT ON TABLE public.sales_targets IS '销售目标';

COMMENT ON COLUMN public.sales_targets.id IS '本目标记录标识';

COMMENT ON COLUMN public.sales_targets.dataset_version_id IS '目标原值及解释的规范化版本';

COMMENT ON COLUMN public.sales_targets.source_row_key IS '原交付中本行的确定定位';

COMMENT ON COLUMN public.sales_targets.source_target_key IS '来源已有目标记录ID';

COMMENT ON COLUMN public.sales_targets.source_scope_label IS '来源实际范围名称/说明';

COMMENT ON COLUMN public.sales_targets.source_period_label IS '来源年份、月份或期间原标签';

COMMENT ON COLUMN public.sales_targets.period_kind IS '目标期间类型';

COMMENT ON COLUMN public.sales_targets.period_start IS '经核实的目标起日，含当日';

COMMENT ON COLUMN public.sales_targets.period_end IS '经核实的目标止日，含当日';

COMMENT ON COLUMN public.sales_targets.period_status IS '期间依据状态';

COMMENT ON COLUMN public.sales_targets.target_amount IS '这一范围的一份目标金额';

COMMENT ON COLUMN public.sales_targets.scope_status IS '固定店铺集合核验状态';

COMMENT ON COLUMN public.sales_targets.evidence_note IS '原入口、重复月份、范围和期间核验说明';

CREATE TABLE public.score_standards (
    id TEXT NOT NULL,
    standard_code TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    listing_id TEXT,
    source_listing_key TEXT,
    source_category_system TEXT NOT NULL,
    source_category_label TEXT NOT NULL,
    model_category_label TEXT,
    season_label TEXT,
    lifecycle_label TEXT,
    basis_kind TEXT NOT NULL,
    identity_match_status TEXT NOT NULL,
    source_effective_date DATE,
    source_date_status TEXT NOT NULL,
    source_observed_at TIMESTAMP WITH TIME ZONE,
    source_locator TEXT NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_score_standards_identity PRIMARY KEY (id),
    CONSTRAINT fk_score_standards_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_score_standards_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_score_standards_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_score_standards_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_score_standards_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT ck_score_standards_standard_code_text_nonblank CHECK (standard_code IS NULL OR btrim(standard_code) <> ''),
    CONSTRAINT ck_score_standards_source_listing_key_text_nonblank CHECK (source_listing_key IS NULL OR btrim(source_listing_key) <> ''),
    CONSTRAINT ck_score_standards_source_category_system_text_nonblank CHECK (source_category_system IS NULL OR btrim(source_category_system) <> ''),
    CONSTRAINT ck_score_standards_source_category_label_text_nonblank CHECK (source_category_label IS NULL OR btrim(source_category_label) <> ''),
    CONSTRAINT ck_score_standards_model_category_label_text_nonblank CHECK (model_category_label IS NULL OR btrim(model_category_label) <> ''),
    CONSTRAINT ck_score_standards_season_label_text_nonblank CHECK (season_label IS NULL OR btrim(season_label) <> ''),
    CONSTRAINT ck_score_standards_lifecycle_label_text_nonblank CHECK (lifecycle_label IS NULL OR btrim(lifecycle_label) <> ''),
    CONSTRAINT ck_score_standards_basis_kind_text_nonblank CHECK (basis_kind IS NULL OR btrim(basis_kind) <> ''),
    CONSTRAINT ck_score_standards_identity_match_status_text_nonblank CHECK (identity_match_status IS NULL OR btrim(identity_match_status) <> ''),
    CONSTRAINT ck_score_standards_source_date_status_text_nonblank CHECK (source_date_status IS NULL OR btrim(source_date_status) <> ''),
    CONSTRAINT ck_score_standards_source_locator_text_nonblank CHECK (source_locator IS NULL OR btrim(source_locator) <> ''),
    CONSTRAINT ck_score_standards_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT uq_score_standards_version_standard_code UNIQUE (dataset_version_id, standard_code),
    CONSTRAINT ck_score_standards_basis_kind_values CHECK (basis_kind IN ('fixed_display_trial', 'verified_reference', 'proposed')),
    CONSTRAINT ck_score_standards_identity_match_status_values CHECK (identity_match_status IN ('matched', 'unmapped', 'conflict')),
    CONSTRAINT ck_score_standards_source_date_status_values CHECK (source_date_status IN ('confirmed', 'unknown')),
    CONSTRAINT ck_score_standards_source_date_shape CHECK ((source_date_status = 'confirmed') = (source_effective_date IS NOT NULL)),
    CONSTRAINT ck_score_standards_matched_identity CHECK (identity_match_status <> 'matched' OR (listing_id IS NOT NULL AND source_listing_key IS NOT NULL))
);

CREATE INDEX ix_score_standards_query_16 ON public.score_standards (dataset_version_id, listing_id);

COMMENT ON TABLE public.score_standards IS '新品评分标准';

COMMENT ON COLUMN public.score_standards.id IS '本标准修订标识';

COMMENT ON COLUMN public.score_standards.standard_code IS '标准系列的稳定代码';

COMMENT ON COLUMN public.score_standards.dataset_version_id IS '标准参数及出处的可重读版本';

COMMENT ON COLUMN public.score_standards.listing_id IS '已核原绑定的店铺商品链接';

COMMENT ON COLUMN public.score_standards.source_listing_key IS '标准声明的原完整店铺商品定位';

COMMENT ON COLUMN public.score_standards.source_category_system IS '原绑定的品类体系';

COMMENT ON COLUMN public.score_standards.source_category_label IS '原绑定的运营品类原标签';

COMMENT ON COLUMN public.score_standards.model_category_label IS '历史模型品类声明';

COMMENT ON COLUMN public.score_standards.season_label IS '历史季节上下文';

COMMENT ON COLUMN public.score_standards.lifecycle_label IS '历史生命周期上下文';

COMMENT ON COLUMN public.score_standards.basis_kind IS '标准依据类别';

COMMENT ON COLUMN public.score_standards.identity_match_status IS '原链接及品类核验状态';

COMMENT ON COLUMN public.score_standards.source_effective_date IS '有证据的标准适用起日';

COMMENT ON COLUMN public.score_standards.source_date_status IS '上述日期依据状态';

COMMENT ON COLUMN public.score_standards.source_observed_at IS '实际观察标准出处的时刻';

COMMENT ON COLUMN public.score_standards.source_locator IS '原标准页面/交付的确切定位';

COMMENT ON COLUMN public.score_standards.evidence_note IS '原精度、适用边界及缺口';

CREATE TABLE public.shop_channel_promotion_daily (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    channel_code TEXT,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_channel_label TEXT,
    source_dt_raw TEXT,
    business_date DATE,
    date_status TEXT NOT NULL,
    spend NUMERIC,
    attributed_revenue NUMERIC,
    net_attributed_revenue NUMERIC,
    attributed_deals NUMERIC,
    impressions NUMERIC,
    clicks NUMERIC,
    source_updated_time_raw TEXT,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    source_created_time_raw TEXT,
    source_created_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shop_channel_promotion_daily_identity PRIMARY KEY (id),
    CONSTRAINT fk_shop_channel_promotion_daily_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_shop_channel_promotion_daily_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_shop_channel_promotion_daily_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shop_channel_promotion_daily_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_shop_channel_promotion_daily_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_shop_channel_promotion_daily_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_shop_channel_promotion_daily_spend_core_finite CHECK (spend IS NULL OR spend NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_channel_promotion_daily_attributed_revenue_core_finite CHECK (attributed_revenue IS NULL OR attributed_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_channel_promotion_daily_net_attributed_reven_5fe1c40022 CHECK (net_attributed_revenue IS NULL OR net_attributed_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_channel_promotion_daily_attributed_deals_core_finite CHECK (attributed_deals IS NULL OR attributed_deals NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_channel_promotion_daily_impressions_core_finite CHECK (impressions IS NULL OR impressions NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_channel_promotion_daily_clicks_core_finite CHECK (clicks IS NULL OR clicks NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_shop_channel_promotion_daily_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_shop_channel_promotion_daily_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_shop_channel_promotion_daily_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_shop_channel_promotion_daily_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_shop_channel_promotion_daily_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND business_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND business_date IS NULL)),
    CONSTRAINT ck_shop_channel_promotion_daily_channel_code_values CHECK (channel_code IN ('product_promotion', 'star_shop')),
    CONSTRAINT ck_shop_channel_promotion_daily_star_shop_net_unavailable CHECK (channel_code IS DISTINCT FROM 'star_shop' OR net_attributed_revenue IS NULL)
);

CREATE INDEX ix_shop_channel_promotion_daily_query_08 ON public.shop_channel_promotion_daily (dataset_version_id, business_date, shop_id, id);

COMMENT ON TABLE public.shop_channel_promotion_daily IS '店铺渠道推广日原行';

COMMENT ON COLUMN public.shop_channel_promotion_daily.id IS '店铺渠道推广原行ID';

COMMENT ON COLUMN public.shop_channel_promotion_daily.dataset_version_id IS '渠道来源输入版本';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_record_id IS '来源id';

COMMENT ON COLUMN public.shop_channel_promotion_daily.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.shop_channel_promotion_daily.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.shop_channel_promotion_daily.channel_code IS '已核来源对应的有限渠道代码';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_shop_id IS '来源店铺ID';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_channel_label IS '来源明确提供的渠道标签';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_dt_raw IS '来源dt原表示';

COMMENT ON COLUMN public.shop_channel_promotion_daily.business_date IS '已解释的来源日标签';

COMMENT ON COLUMN public.shop_channel_promotion_daily.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.shop_channel_promotion_daily.spend IS '概览总花费(元)/明星cost';

COMMENT ON COLUMN public.shop_channel_promotion_daily.attributed_revenue IS '概览交易额(元)/明星transaction_amount';

COMMENT ON COLUMN public.shop_channel_promotion_daily.net_attributed_revenue IS '概览净交易额(元)';

COMMENT ON COLUMN public.shop_channel_promotion_daily.attributed_deals IS '概览成交笔数/明星transaction_count';

COMMENT ON COLUMN public.shop_channel_promotion_daily.impressions IS '广告曝光量';

COMMENT ON COLUMN public.shop_channel_promotion_daily.clicks IS '广告点击量';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_updated_time_raw IS '概览updated_at原表示';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_updated_at IS '可解释的来源更新时间';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_created_time_raw IS '明星create_time原表示';

COMMENT ON COLUMN public.shop_channel_promotion_daily.source_created_at IS '可解释的来源创建时间';

CREATE TABLE public.shop_daily_facts (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_dt_raw TEXT,
    business_date DATE,
    date_status TEXT NOT NULL,
    shop_revenue NUMERIC,
    shop_visitors NUMERIC,
    shop_views NUMERIC,
    product_visitors NUMERIC,
    product_views NUMERIC,
    buyers NUMERIC,
    orders NUMERIC,
    source_aov NUMERIC,
    source_uv_value NUMERIC,
    source_conversion_rate NUMERIC,
    source_updated_time_raw TEXT,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shop_daily_facts_identity PRIMARY KEY (id),
    CONSTRAINT fk_shop_daily_facts_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_shop_daily_facts_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_shop_daily_facts_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shop_daily_facts_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_shop_daily_facts_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_shop_daily_facts_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_shop_daily_facts_shop_revenue_core_finite CHECK (shop_revenue IS NULL OR shop_revenue NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_shop_visitors_core_finite CHECK (shop_visitors IS NULL OR shop_visitors NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_shop_views_core_finite CHECK (shop_views IS NULL OR shop_views NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_product_visitors_core_finite CHECK (product_visitors IS NULL OR product_visitors NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_product_views_core_finite CHECK (product_views IS NULL OR product_views NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_buyers_core_finite CHECK (buyers IS NULL OR buyers NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_orders_core_finite CHECK (orders IS NULL OR orders NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_source_aov_core_finite CHECK (source_aov IS NULL OR source_aov NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_source_uv_value_core_finite CHECK (source_uv_value IS NULL OR source_uv_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_daily_facts_source_conversion_rate_core_finite CHECK (source_conversion_rate IS NULL OR source_conversion_rate NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_shop_daily_facts_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_shop_daily_facts_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_shop_daily_facts_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_shop_daily_facts_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_shop_daily_facts_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND business_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND business_date IS NULL))
);

CREATE INDEX ix_shop_daily_facts_query_05 ON public.shop_daily_facts (dataset_version_id, business_date, shop_id, id);

COMMENT ON TABLE public.shop_daily_facts IS '店铺日经营原行';

COMMENT ON COLUMN public.shop_daily_facts.id IS '店铺经营原行ID';

COMMENT ON COLUMN public.shop_daily_facts.dataset_version_id IS '店铺日输入版本';

COMMENT ON COLUMN public.shop_daily_facts.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.shop_daily_facts.source_record_id IS '来源id';

COMMENT ON COLUMN public.shop_daily_facts.shop_id IS '已核实经营店铺';

COMMENT ON COLUMN public.shop_daily_facts.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.shop_daily_facts.source_shop_id IS '来源shop_id';

COMMENT ON COLUMN public.shop_daily_facts.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.shop_daily_facts.source_dt_raw IS '来源dt原表示';

COMMENT ON COLUMN public.shop_daily_facts.business_date IS '可解释的来源日标签';

COMMENT ON COLUMN public.shop_daily_facts.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.shop_daily_facts.shop_revenue IS 'JSON成交金额';

COMMENT ON COLUMN public.shop_daily_facts.shop_visitors IS 'JSON店铺访客数';

COMMENT ON COLUMN public.shop_daily_facts.shop_views IS 'JSON店铺浏览量';

COMMENT ON COLUMN public.shop_daily_facts.product_visitors IS 'JSON商品访客数';

COMMENT ON COLUMN public.shop_daily_facts.product_views IS 'JSON商品浏览量';

COMMENT ON COLUMN public.shop_daily_facts.buyers IS 'JSON成交买家数';

COMMENT ON COLUMN public.shop_daily_facts.orders IS 'JSON成交订单数';

COMMENT ON COLUMN public.shop_daily_facts.source_aov IS 'JSON来源客单价';

COMMENT ON COLUMN public.shop_daily_facts.source_uv_value IS 'JSON来源成交UV价值';

COMMENT ON COLUMN public.shop_daily_facts.source_conversion_rate IS 'JSON来源成交转化率';

COMMENT ON COLUMN public.shop_daily_facts.source_updated_time_raw IS '来源updated_at原表示';

COMMENT ON COLUMN public.shop_daily_facts.source_updated_at IS '有时区依据的来源更新时间';

CREATE TABLE public.shop_rating_daily (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_dt_raw TEXT,
    rating_date DATE,
    date_status TEXT NOT NULL,
    source_score NUMERIC,
    source_created_time_raw TEXT,
    source_created_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shop_rating_daily_identity PRIMARY KEY (id),
    CONSTRAINT fk_shop_rating_daily_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_shop_rating_daily_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_shop_rating_daily_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shop_rating_daily_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_shop_rating_daily_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_shop_rating_daily_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_shop_rating_daily_source_score_core_finite CHECK (source_score IS NULL OR source_score NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_shop_rating_daily_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_shop_rating_daily_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_shop_rating_daily_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_shop_rating_daily_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_shop_rating_daily_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND rating_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND rating_date IS NULL))
);

CREATE INDEX ix_shop_rating_daily_query_09 ON public.shop_rating_daily (dataset_version_id, shop_id, rating_date DESC, id);

COMMENT ON TABLE public.shop_rating_daily IS '店铺评分日原行';

COMMENT ON COLUMN public.shop_rating_daily.id IS '评分原行ID';

COMMENT ON COLUMN public.shop_rating_daily.dataset_version_id IS '评分输入版本';

COMMENT ON COLUMN public.shop_rating_daily.source_row_key IS '版本内原行定位符';

COMMENT ON COLUMN public.shop_rating_daily.source_record_id IS '来源id';

COMMENT ON COLUMN public.shop_rating_daily.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.shop_rating_daily.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.shop_rating_daily.source_shop_id IS '来源shop_id';

COMMENT ON COLUMN public.shop_rating_daily.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.shop_rating_daily.source_dt_raw IS '原VARCHAR dt';

COMMENT ON COLUMN public.shop_rating_daily.rating_date IS '可解析的来源评分日';

COMMENT ON COLUMN public.shop_rating_daily.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.shop_rating_daily.source_score IS 'shop_rating_score原评分';

COMMENT ON COLUMN public.shop_rating_daily.source_created_time_raw IS 'create_time原表示';

COMMENT ON COLUMN public.shop_rating_daily.source_created_at IS '有依据的来源创建/采集时间';

CREATE TABLE public.shop_refund_records (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_platform TEXT,
    source_department TEXT,
    source_date_raw TEXT,
    business_date DATE,
    date_status TEXT NOT NULL,
    reported_refund_amount NUMERIC,
    source_reported_amount NUMERIC,
    source_refund_ratio NUMERIC,
    source_modified_time_raw TEXT,
    source_modified_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shop_refund_records_identity PRIMARY KEY (id),
    CONSTRAINT fk_shop_refund_records_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_shop_refund_records_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_shop_refund_records_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shop_refund_records_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_shop_refund_records_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_shop_refund_records_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_shop_refund_records_reported_refund_amount_core_finite CHECK (reported_refund_amount IS NULL OR reported_refund_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_refund_records_source_reported_amount_core_finite CHECK (source_reported_amount IS NULL OR source_reported_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_refund_records_source_refund_ratio_core_finite CHECK (source_refund_ratio IS NULL OR source_refund_ratio NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_shop_refund_records_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_shop_refund_records_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_shop_refund_records_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_shop_refund_records_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'not_provided', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_shop_refund_records_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND business_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND business_date IS NULL))
);

CREATE INDEX ix_shop_refund_records_query_10 ON public.shop_refund_records (dataset_version_id, business_date, shop_id, id);

COMMENT ON TABLE public.shop_refund_records IS '店铺退款统计原行';

COMMENT ON COLUMN public.shop_refund_records.id IS '退款原行ID';

COMMENT ON COLUMN public.shop_refund_records.dataset_version_id IS '退款输入版本';

COMMENT ON COLUMN public.shop_refund_records.source_row_key IS '本版本内原行定位符';

COMMENT ON COLUMN public.shop_refund_records.source_record_id IS '飞书record_id等来源标识';

COMMENT ON COLUMN public.shop_refund_records.shop_id IS '已核实经营店铺';

COMMENT ON COLUMN public.shop_refund_records.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.shop_refund_records.source_shop_id IS '来源明确提供的店铺ID';

COMMENT ON COLUMN public.shop_refund_records.source_shop_name IS '来源店铺标签';

COMMENT ON COLUMN public.shop_refund_records.source_platform IS '本行来源平台维度';

COMMENT ON COLUMN public.shop_refund_records.source_department IS '本行来源部门维度';

COMMENT ON COLUMN public.shop_refund_records.source_date_raw IS '来源日期原表示';

COMMENT ON COLUMN public.shop_refund_records.business_date IS '可解释的统计日标签';

COMMENT ON COLUMN public.shop_refund_records.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.shop_refund_records.reported_refund_amount IS '统计退款金额原值';

COMMENT ON COLUMN public.shop_refund_records.source_reported_amount IS '来源统计金额';

COMMENT ON COLUMN public.shop_refund_records.source_refund_ratio IS '来源已算退款占比';

COMMENT ON COLUMN public.shop_refund_records.source_modified_time_raw IS '来源明确提供的修改时间原表示';

COMMENT ON COLUMN public.shop_refund_records.source_modified_at IS '有依据的来源修改时间';

CREATE TABLE public.shop_region_daily (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    source_row_key TEXT NOT NULL,
    source_record_id TEXT,
    shop_id TEXT,
    shop_match_status TEXT NOT NULL,
    source_shop_id TEXT,
    source_shop_name TEXT,
    source_region_name TEXT,
    source_dt_raw TEXT,
    business_date DATE,
    date_status TEXT NOT NULL,
    transaction_amount NUMERIC,
    transaction_orders NUMERIC,
    transaction_buyers NUMERIC,
    source_price_per_buyer NUMERIC,
    source_price_per_order NUMERIC,
    source_updated_time_raw TEXT,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_shop_region_daily_identity PRIMARY KEY (id),
    CONSTRAINT fk_shop_region_daily_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_shop_region_daily_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_shop_region_daily_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_shop_region_daily_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_shop_region_daily_source_row_key_core_text CHECK (source_row_key IS NULL OR (btrim(source_row_key) <> '' AND source_row_key = btrim(source_row_key))),
    CONSTRAINT ck_shop_region_daily_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_shop_region_daily_transaction_amount_core_finite CHECK (transaction_amount IS NULL OR transaction_amount NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_region_daily_transaction_orders_core_finite CHECK (transaction_orders IS NULL OR transaction_orders NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_region_daily_transaction_buyers_core_finite CHECK (transaction_buyers IS NULL OR transaction_buyers NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_region_daily_source_price_per_buyer_core_finite CHECK (source_price_per_buyer IS NULL OR source_price_per_buyer NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_shop_region_daily_source_price_per_order_core_finite CHECK (source_price_per_order IS NULL OR source_price_per_order NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT uq_shop_region_daily_version_source_row UNIQUE (dataset_version_id, source_row_key),
    CONSTRAINT ck_shop_region_daily_shop_match_status_values CHECK (shop_match_status IN ('matched', 'unmatched', 'conflict', 'not_provided')),
    CONSTRAINT ck_shop_region_daily_shop_match_shape CHECK ((shop_match_status = 'matched' AND shop_id IS NOT NULL) OR (shop_match_status <> 'matched' AND shop_id IS NULL)),
    CONSTRAINT ck_shop_region_daily_date_status_values CHECK (date_status IN ('parsed_label', 'verified_period', 'missing', 'unparseable', 'conflict')),
    CONSTRAINT ck_shop_region_daily_date_status_shape CHECK ((date_status IN ('parsed_label', 'verified_period') AND business_date IS NOT NULL) OR (date_status NOT IN ('parsed_label', 'verified_period') AND business_date IS NULL))
);

CREATE INDEX ix_shop_region_daily_query_11 ON public.shop_region_daily (dataset_version_id, business_date, shop_id, id);

COMMENT ON TABLE public.shop_region_daily IS '店铺地区成交日原行';

COMMENT ON COLUMN public.shop_region_daily.id IS '后台地区原行ID';

COMMENT ON COLUMN public.shop_region_daily.dataset_version_id IS '后台地区输入版本';

COMMENT ON COLUMN public.shop_region_daily.source_row_key IS '版本内原行定位符';

COMMENT ON COLUMN public.shop_region_daily.source_record_id IS '来源id';

COMMENT ON COLUMN public.shop_region_daily.shop_id IS '已核实店铺';

COMMENT ON COLUMN public.shop_region_daily.shop_match_status IS '店铺匹配状态';

COMMENT ON COLUMN public.shop_region_daily.source_shop_id IS '来源shop_id';

COMMENT ON COLUMN public.shop_region_daily.source_shop_name IS '来源观察店名';

COMMENT ON COLUMN public.shop_region_daily.source_region_name IS '来源province标签';

COMMENT ON COLUMN public.shop_region_daily.source_dt_raw IS '来源dt原表示';

COMMENT ON COLUMN public.shop_region_daily.business_date IS '可解释的来源日';

COMMENT ON COLUMN public.shop_region_daily.date_status IS '日期解析/核验状态';

COMMENT ON COLUMN public.shop_region_daily.transaction_amount IS '地区成交金额';

COMMENT ON COLUMN public.shop_region_daily.transaction_orders IS '地区成交订单';

COMMENT ON COLUMN public.shop_region_daily.transaction_buyers IS '地区成交买家';

COMMENT ON COLUMN public.shop_region_daily.source_price_per_buyer IS '来源买家均额';

COMMENT ON COLUMN public.shop_region_daily.source_price_per_order IS '来源订单均额';

COMMENT ON COLUMN public.shop_region_daily.source_updated_time_raw IS '来源updated_at原表示';

COMMENT ON COLUMN public.shop_region_daily.source_updated_at IS '有依据的来源更新时间';

CREATE TABLE public.task_runs (
    id TEXT NOT NULL,
    job_code TEXT NOT NULL,
    request_ref TEXT NOT NULL,
    retry_of_id TEXT,
    lock_key TEXT NOT NULL,
    status TEXT NOT NULL,
    queued_at TIMESTAMP WITH TIME ZONE DEFAULT transaction_timestamp() NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    worker_ref TEXT,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    result_version_id TEXT,
    source_updated_text TEXT,
    source_updated_at TIMESTAMP WITH TIME ZONE,
    source_update_observed_at TIMESTAMP WITH TIME ZONE,
    error_code TEXT,
    error_summary TEXT,
    evidence_ref TEXT,
    CONSTRAINT pk_task_runs_identity PRIMARY KEY (id),
    CONSTRAINT fk_task_runs_retry_of_id FOREIGN KEY(retry_of_id) REFERENCES public.task_runs (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_task_runs_result_version_id FOREIGN KEY(result_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_task_runs_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_task_runs_retry_of_id_core_text CHECK (retry_of_id IS NULL OR (btrim(retry_of_id) <> '' AND retry_of_id = btrim(retry_of_id))),
    CONSTRAINT ck_task_runs_result_version_id_core_text CHECK (result_version_id IS NULL OR (btrim(result_version_id) <> '' AND result_version_id = btrim(result_version_id))),
    CONSTRAINT ck_task_runs_job_code_core_text CHECK (job_code IS NULL OR (btrim(job_code) <> '')),
    CONSTRAINT ck_task_runs_request_ref_core_text CHECK (request_ref IS NULL OR (btrim(request_ref) <> '')),
    CONSTRAINT ck_task_runs_lock_key_core_text CHECK (lock_key IS NULL OR (btrim(lock_key) <> '')),
    CONSTRAINT ck_task_runs_worker_ref_core_text CHECK (worker_ref IS NULL OR (btrim(worker_ref) <> '')),
    CONSTRAINT ck_task_runs_error_code_core_text CHECK (error_code IS NULL OR (btrim(error_code) <> '')),
    CONSTRAINT ck_task_runs_error_summary_core_text CHECK (error_summary IS NULL OR (btrim(error_summary) <> '')),
    CONSTRAINT ck_task_runs_evidence_ref_core_text CHECK (evidence_ref IS NULL OR (btrim(evidence_ref) <> '')),
    CONSTRAINT ck_task_runs_status_values CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'abandoned')),
    CONSTRAINT ck_task_runs_retry_not_self CHECK (retry_of_id IS NULL OR retry_of_id <> id),
    CONSTRAINT ck_task_runs_execution_state_shape CHECK ((status = 'queued' AND started_at IS NULL AND finished_at IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND worker_ref IS NOT NULL AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR (status IN ('succeeded', 'failed', 'cancelled', 'abandoned') AND finished_at IS NOT NULL AND lease_expires_at IS NULL)),
    CONSTRAINT ck_task_runs_execution_time_order CHECK ((started_at IS NULL OR started_at >= queued_at) AND (finished_at IS NULL OR finished_at >= queued_at) AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)),
    CONSTRAINT ck_task_runs_result_error_shape CHECK ((status <> 'succeeded' OR (result_version_id IS NOT NULL AND error_code IS NULL)) AND (status NOT IN ('failed', 'abandoned') OR error_code IS NOT NULL)),
    CONSTRAINT ck_task_runs_source_update_observation_shape CHECK ((source_updated_text IS NULL AND source_updated_at IS NULL AND source_update_observed_at IS NULL) OR (source_updated_text IS NOT NULL AND source_update_observed_at IS NOT NULL))
);

CREATE INDEX ix_task_runs_query_19 ON public.task_runs (job_code, queued_at DESC, id);

CREATE INDEX ix_task_runs_query_20 ON public.task_runs (result_version_id, status, finished_at DESC, id);

CREATE INDEX ix_task_runs_query_21 ON public.task_runs (lease_expires_at, id) WHERE status = 'running';

CREATE INDEX ix_task_runs_query_22 ON public.task_runs (queued_at, id) WHERE status = 'queued';

CREATE UNIQUE INDEX ux_task_runs_one_running_scope ON public.task_runs (lock_key) WHERE status = 'running';

COMMENT ON TABLE public.task_runs IS '同步或计算的一次实际尝试';

COMMENT ON COLUMN public.task_runs.id IS '本次实际尝试标识';

COMMENT ON COLUMN public.task_runs.job_code IS '实际任务种类';

COMMENT ON COLUMN public.task_runs.request_ref IS '该尝试获准请求的冻结描述';

COMMENT ON COLUMN public.task_runs.retry_of_id IS '直接重试的前次尝试';

COMMENT ON COLUMN public.task_runs.lock_key IS '会相互冲突的实际任务范围键';

COMMENT ON COLUMN public.task_runs.status IS '实际执行状态';

COMMENT ON COLUMN public.task_runs.queued_at IS '实际入队/登记时刻';

COMMENT ON COLUMN public.task_runs.started_at IS '实际开始执行时刻';

COMMENT ON COLUMN public.task_runs.finished_at IS '实际结束或宣布遗失的时刻';

COMMENT ON COLUMN public.task_runs.worker_ref IS '当前/最后实际执行者标记';

COMMENT ON COLUMN public.task_runs.lease_expires_at IS '本次运行租约截止时刻';

COMMENT ON COLUMN public.task_runs.result_version_id IS '本次产生或复用的版本';

COMMENT ON COLUMN public.task_runs.source_updated_text IS '本次实际看到的来源更新时间原文';

COMMENT ON COLUMN public.task_runs.source_updated_at IS '有时区依据可解析的来源更新时间';

COMMENT ON COLUMN public.task_runs.source_update_observed_at IS '本次观察到上述原文的实际时刻';

COMMENT ON COLUMN public.task_runs.error_code IS '失败/取消/遗失的稳定原因代码';

COMMENT ON COLUMN public.task_runs.error_summary IS '脱敏的人可读原因';

COMMENT ON COLUMN public.task_runs.evidence_ref IS '实际执行及失败排查证据';

CREATE TABLE public.version_inputs (
    output_version_id TEXT NOT NULL,
    input_role TEXT NOT NULL,
    input_version_id TEXT NOT NULL,
    window_start DATE,
    window_end DATE,
    usage_note TEXT,
    CONSTRAINT pk_version_inputs_identity PRIMARY KEY (output_version_id, input_role),
    CONSTRAINT fk_version_inputs_output_version_id FOREIGN KEY(output_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_version_inputs_input_version_id FOREIGN KEY(input_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_version_inputs_output_version_id_core_text CHECK (output_version_id IS NULL OR (btrim(output_version_id) <> '' AND output_version_id = btrim(output_version_id))),
    CONSTRAINT ck_version_inputs_input_role_core_text CHECK (input_role IS NULL OR (btrim(input_role) <> '' AND input_role = btrim(input_role))),
    CONSTRAINT ck_version_inputs_input_version_id_core_text CHECK (input_version_id IS NULL OR (btrim(input_version_id) <> '' AND input_version_id = btrim(input_version_id))),
    CONSTRAINT ck_version_inputs_input_role_values CHECK (input_role IN ('source', 'scope_mapping', 'profile_records', 'product_current', 'product_previous', 'ad_current', 'profile', 'standard', 'product_history', 'target_source', 'scope_list', 'j1_returns', 'j2_shipped_sales', 'j3_cancelled_sales', 'targets')),
    CONSTRAINT ck_version_inputs_usage_note_core_text CHECK (usage_note IS NULL OR (btrim(usage_note) <> '')),
    CONSTRAINT ck_version_inputs_window_start_window_end_core_pair CHECK ((window_start IS NULL AND window_end IS NULL) OR (window_start IS NOT NULL AND window_end IS NOT NULL AND window_start <= window_end)),
    CONSTRAINT ck_version_inputs_not_self CHECK (input_version_id <> output_version_id)
);

CREATE INDEX ix_version_inputs_query_18 ON public.version_inputs (input_version_id, output_version_id);

COMMENT ON TABLE public.version_inputs IS '输出与输入版本的角色血缘';

COMMENT ON COLUMN public.version_inputs.output_version_id IS '派生输出版本';

COMMENT ON COLUMN public.version_inputs.input_role IS '该输入在输出中的明确业务角色';

COMMENT ON COLUMN public.version_inputs.input_version_id IS '实际使用的已封版输入';

COMMENT ON COLUMN public.version_inputs.window_start IS '此角色实际使用/请求的起日';

COMMENT ON COLUMN public.version_inputs.window_end IS '此角色实际使用/请求的止日';

COMMENT ON COLUMN public.version_inputs.usage_note IS '特殊范围或兼容性证据说明';

CREATE TABLE public.product_scores (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    standard_id TEXT,
    visitors_7d BIGINT,
    product_revenue_7d NUMERIC,
    units_7d BIGINT,
    favorites_7d BIGINT,
    buyers_7d BIGINT,
    orders_7d BIGINT,
    ad_spend_7d NUMERIC,
    ad_attributed_revenue_7d NUMERIC,
    ad_impressions_7d NUMERIC,
    ad_clicks_7d NUMERIC,
    ad_deals_7d NUMERIC,
    previous_revenue_7d NUMERIC,
    previous_orders_7d BIGINT,
    current_lifecycle TEXT,
    lifecycle_reason TEXT,
    decision_code TEXT NOT NULL,
    decision_evidence TEXT,
    CONSTRAINT pk_product_scores_identity PRIMARY KEY (id),
    CONSTRAINT fk_product_scores_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_scores_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_product_scores_standard_id FOREIGN KEY(standard_id) REFERENCES public.score_standards (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_scores_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_product_scores_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_product_scores_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT ck_product_scores_standard_id_core_text CHECK (standard_id IS NULL OR (btrim(standard_id) <> '' AND standard_id = btrim(standard_id))),
    CONSTRAINT ck_product_scores_product_revenue_7d_core_finite CHECK (product_revenue_7d IS NULL OR product_revenue_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_ad_spend_7d_core_finite CHECK (ad_spend_7d IS NULL OR ad_spend_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_ad_attributed_revenue_7d_core_finite CHECK (ad_attributed_revenue_7d IS NULL OR ad_attributed_revenue_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_ad_impressions_7d_core_finite CHECK (ad_impressions_7d IS NULL OR ad_impressions_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_ad_clicks_7d_core_finite CHECK (ad_clicks_7d IS NULL OR ad_clicks_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_ad_deals_7d_core_finite CHECK (ad_deals_7d IS NULL OR ad_deals_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_previous_revenue_7d_core_finite CHECK (previous_revenue_7d IS NULL OR previous_revenue_7d NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_product_scores_current_lifecycle_text_nonblank CHECK (current_lifecycle IS NULL OR btrim(current_lifecycle) <> ''),
    CONSTRAINT ck_product_scores_lifecycle_reason_text_nonblank CHECK (lifecycle_reason IS NULL OR btrim(lifecycle_reason) <> ''),
    CONSTRAINT ck_product_scores_decision_code_text_nonblank CHECK (decision_code IS NULL OR btrim(decision_code) <> ''),
    CONSTRAINT ck_product_scores_decision_evidence_text_nonblank CHECK (decision_evidence IS NULL OR btrim(decision_evidence) <> ''),
    CONSTRAINT uq_product_scores_version_listing UNIQUE (dataset_version_id, listing_id),
    CONSTRAINT ck_product_scores_decision_code_values CHECK (decision_code IN ('unscored', 'pending_validation', 'optimize', 'hold', 'fix_key_metrics', 'small_scale')),
    CONSTRAINT ck_product_scores_lifecycle_reason_required CHECK (lifecycle_reason IS NOT NULL)
);

COMMENT ON TABLE public.product_scores IS '商品评分对象及实际特征';

COMMENT ON COLUMN public.product_scores.id IS '本评分结果定位';

COMMENT ON COLUMN public.product_scores.dataset_version_id IS '本评分结果不可变版本';

COMMENT ON COLUMN public.product_scores.listing_id IS '被评分的完整店铺商品链接';

COMMENT ON COLUMN public.product_scores.standard_id IS '本次实际适用标准修订';

COMMENT ON COLUMN public.product_scores.visitors_7d IS '当期7日商品访客累计特征';

COMMENT ON COLUMN public.product_scores.product_revenue_7d IS '当期7日商品交易额';

COMMENT ON COLUMN public.product_scores.units_7d IS '当期7日支付件数';

COMMENT ON COLUMN public.product_scores.favorites_7d IS '本次实际观察的7日收藏聚合特征';

COMMENT ON COLUMN public.product_scores.buyers_7d IS '当期7日买家累计核对特征';

COMMENT ON COLUMN public.product_scores.orders_7d IS '当期7日订单累计特征';

COMMENT ON COLUMN public.product_scores.ad_spend_7d IS '当期7日推广总花费';

COMMENT ON COLUMN public.product_scores.ad_attributed_revenue_7d IS '当期7日总归因成交额';

COMMENT ON COLUMN public.product_scores.ad_impressions_7d IS '当期7日推广曝光合计';

COMMENT ON COLUMN public.product_scores.ad_clicks_7d IS '当期7日推广点击合计';

COMMENT ON COLUMN public.product_scores.ad_deals_7d IS '当期7日总归因成交笔数';

COMMENT ON COLUMN public.product_scores.previous_revenue_7d IS '前7日商品交易额';

COMMENT ON COLUMN public.product_scores.previous_orders_7d IS '前7日订单累计特征';

COMMENT ON COLUMN public.product_scores.current_lifecycle IS '本次有真实依据的阶段分类结果';

COMMENT ON COLUMN public.product_scores.lifecycle_reason IS '当前阶段判定依据或未知原因';

COMMENT ON COLUMN public.product_scores.decision_code IS '本次确定性门控的实际输出分支';

COMMENT ON COLUMN public.product_scores.decision_evidence IS '本次分支及关键门控的具体原因';

CREATE TABLE public.profile_listing_links (
    id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    profile_record_id TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    relationship_status TEXT NOT NULL,
    relationship_reason TEXT NOT NULL,
    listed_date_applicability_status TEXT NOT NULL,
    listed_date_evidence_ref TEXT,
    CONSTRAINT pk_profile_listing_links_identity PRIMARY KEY (id),
    CONSTRAINT fk_profile_listing_links_dataset_version_id FOREIGN KEY(dataset_version_id) REFERENCES public.dataset_versions (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_profile_listing_links_profile_record_id FOREIGN KEY(profile_record_id) REFERENCES public.profile_source_records (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_profile_listing_links_listing_id FOREIGN KEY(listing_id) REFERENCES public.listings (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_profile_listing_links_id_core_text CHECK (id IS NULL OR (btrim(id) <> '' AND id = btrim(id))),
    CONSTRAINT ck_profile_listing_links_dataset_version_id_core_text CHECK (dataset_version_id IS NULL OR (btrim(dataset_version_id) <> '' AND dataset_version_id = btrim(dataset_version_id))),
    CONSTRAINT ck_profile_listing_links_profile_record_id_core_text CHECK (profile_record_id IS NULL OR (btrim(profile_record_id) <> '' AND profile_record_id = btrim(profile_record_id))),
    CONSTRAINT ck_profile_listing_links_listing_id_core_text CHECK (listing_id IS NULL OR (btrim(listing_id) <> '' AND listing_id = btrim(listing_id))),
    CONSTRAINT uq_profile_listing_links_version_record_listing UNIQUE (dataset_version_id, profile_record_id, listing_id),
    CONSTRAINT ck_profile_listing_links_relationship_status_values CHECK (relationship_status IN ('matched', 'rejected')),
    CONSTRAINT ck_profile_listing_links_listed_date_applicability_s_4536a013c5 CHECK (listed_date_applicability_status IN ('confirmed', 'not_applicable', 'unknown', 'conflict')),
    CONSTRAINT ck_profile_listing_links_relationship_reason_core_text CHECK (relationship_reason IS NULL OR (btrim(relationship_reason) <> '')),
    CONSTRAINT ck_profile_listing_links_listed_date_evidence_ref_core_text CHECK (listed_date_evidence_ref IS NULL OR (btrim(listed_date_evidence_ref) <> '' AND listed_date_evidence_ref = btrim(listed_date_evidence_ref))),
    CONSTRAINT ck_profile_listing_links_date_applicability_shape CHECK ((listed_date_applicability_status = 'unknown' OR listed_date_evidence_ref IS NOT NULL) AND (listed_date_applicability_status <> 'confirmed' OR relationship_status = 'matched') AND (relationship_status <> 'rejected' OR listed_date_applicability_status = 'not_applicable'))
);

CREATE INDEX ix_profile_listing_links_query_03 ON public.profile_listing_links (dataset_version_id, listing_id, profile_record_id);

COMMENT ON TABLE public.profile_listing_links IS '资料原行与链接关系';

COMMENT ON COLUMN public.profile_listing_links.id IS '本版本的原行—链接关系ID';

COMMENT ON COLUMN public.profile_listing_links.dataset_version_id IS '固定关系结果与规则的版本';

COMMENT ON COLUMN public.profile_listing_links.profile_record_id IS '该关系涉及的固定资料原行';

COMMENT ON COLUMN public.profile_listing_links.listing_id IS '该关系涉及的已核店铺商品链接';

COMMENT ON COLUMN public.profile_listing_links.relationship_status IS '本关系的核验结果';

COMMENT ON COLUMN public.profile_listing_links.relationship_reason IS '确认或排除关系的可核查依据';

COMMENT ON COLUMN public.profile_listing_links.listed_date_applicability_status IS '本原行日期对本链接的适用状态';

COMMENT ON COLUMN public.profile_listing_links.listed_date_evidence_ref IS '日期适用/排除/冲突的固定证据引用';

CREATE TABLE public.refund_target_categories (
    target_id TEXT NOT NULL,
    source_member_key TEXT NOT NULL,
    effect TEXT NOT NULL,
    source_category_system TEXT NOT NULL,
    source_category_key TEXT,
    source_category_label TEXT,
    match_basis TEXT NOT NULL,
    condition_status TEXT NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_refund_target_categories_identity PRIMARY KEY (target_id, source_member_key),
    CONSTRAINT fk_refund_target_categories_target_id FOREIGN KEY(target_id) REFERENCES public.refund_targets (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_refund_target_categories_target_id_core_text CHECK (target_id IS NULL OR (btrim(target_id) <> '' AND target_id = btrim(target_id))),
    CONSTRAINT ck_refund_target_categories_source_member_key_core_text CHECK (source_member_key IS NULL OR (btrim(source_member_key) <> '' AND source_member_key = btrim(source_member_key))),
    CONSTRAINT ck_refund_target_categories_effect_text_nonblank CHECK (effect IS NULL OR btrim(effect) <> ''),
    CONSTRAINT ck_refund_target_categories_source_category_system_t_fbb439c736 CHECK (source_category_system IS NULL OR btrim(source_category_system) <> ''),
    CONSTRAINT ck_refund_target_categories_source_category_key_text_nonblank CHECK (source_category_key IS NULL OR btrim(source_category_key) <> ''),
    CONSTRAINT ck_refund_target_categories_source_category_label_text_nonblank CHECK (source_category_label IS NULL OR btrim(source_category_label) <> ''),
    CONSTRAINT ck_refund_target_categories_match_basis_text_nonblank CHECK (match_basis IS NULL OR btrim(match_basis) <> ''),
    CONSTRAINT ck_refund_target_categories_condition_status_text_nonblank CHECK (condition_status IS NULL OR btrim(condition_status) <> ''),
    CONSTRAINT ck_refund_target_categories_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT ck_refund_target_categories_effect_values CHECK (effect IN ('include', 'exclude')),
    CONSTRAINT ck_refund_target_categories_condition_status_values CHECK (condition_status IN ('confirmed', 'unresolved', 'conflict')),
    CONSTRAINT ck_refund_target_categories_match_basis_values CHECK (match_basis IN ('source_code', 'exact_label')),
    CONSTRAINT ck_refund_target_categories_category_present CHECK (source_category_key IS NOT NULL OR source_category_label IS NOT NULL),
    CONSTRAINT ck_refund_target_categories_match_basis_shape CHECK ((match_basis = 'source_code' AND source_category_key IS NOT NULL) OR (match_basis = 'exact_label' AND source_category_label IS NOT NULL))
);

CREATE UNIQUE INDEX ux_refund_target_categories_category_code ON public.refund_target_categories (target_id, source_category_system, source_category_key) WHERE source_category_key IS NOT NULL;

CREATE UNIQUE INDEX ux_refund_target_categories_category_label_without_code ON public.refund_target_categories (target_id, source_category_system, source_category_label) WHERE source_category_key IS NULL;

COMMENT ON TABLE public.refund_target_categories IS '退款目标分类条件';

COMMENT ON COLUMN public.refund_target_categories.target_id IS '所属退款目标';

COMMENT ON COLUMN public.refund_target_categories.source_member_key IS '原条件的确定定位';

COMMENT ON COLUMN public.refund_target_categories.effect IS '包含或排除';

COMMENT ON COLUMN public.refund_target_categories.source_category_system IS '实际来源分类体系';

COMMENT ON COLUMN public.refund_target_categories.source_category_key IS '原分类编码';

COMMENT ON COLUMN public.refund_target_categories.source_category_label IS '原分类标签';

COMMENT ON COLUMN public.refund_target_categories.match_basis IS '条件实际按编码还是精确标签匹配';

COMMENT ON COLUMN public.refund_target_categories.condition_status IS '本条件是否已明确可执行';

COMMENT ON COLUMN public.refund_target_categories.evidence_note IS '别名、体系或原条件核验依据';

CREATE TABLE public.refund_target_styles (
    target_id TEXT NOT NULL,
    source_member_key TEXT NOT NULL,
    effect TEXT NOT NULL,
    source_style_namespace TEXT NOT NULL,
    source_style_code TEXT NOT NULL,
    condition_status TEXT NOT NULL,
    evidence_note TEXT,
    CONSTRAINT pk_refund_target_styles_identity PRIMARY KEY (target_id, source_member_key),
    CONSTRAINT fk_refund_target_styles_target_id FOREIGN KEY(target_id) REFERENCES public.refund_targets (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_refund_target_styles_target_id_core_text CHECK (target_id IS NULL OR (btrim(target_id) <> '' AND target_id = btrim(target_id))),
    CONSTRAINT ck_refund_target_styles_source_member_key_core_text CHECK (source_member_key IS NULL OR (btrim(source_member_key) <> '' AND source_member_key = btrim(source_member_key))),
    CONSTRAINT ck_refund_target_styles_effect_text_nonblank CHECK (effect IS NULL OR btrim(effect) <> ''),
    CONSTRAINT ck_refund_target_styles_source_style_namespace_text_nonblank CHECK (source_style_namespace IS NULL OR btrim(source_style_namespace) <> ''),
    CONSTRAINT ck_refund_target_styles_source_style_code_text_nonblank CHECK (source_style_code IS NULL OR btrim(source_style_code) <> ''),
    CONSTRAINT ck_refund_target_styles_condition_status_text_nonblank CHECK (condition_status IS NULL OR btrim(condition_status) <> ''),
    CONSTRAINT ck_refund_target_styles_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> ''),
    CONSTRAINT ck_refund_target_styles_effect_values CHECK (effect IN ('include', 'exclude')),
    CONSTRAINT ck_refund_target_styles_condition_status_values CHECK (condition_status IN ('confirmed', 'unresolved', 'conflict')),
    CONSTRAINT uq_refund_target_styles_target_namespace_code UNIQUE (target_id, source_style_namespace, source_style_code)
);

COMMENT ON TABLE public.refund_target_styles IS '退款目标款码条件';

COMMENT ON COLUMN public.refund_target_styles.target_id IS '所属退款目标';

COMMENT ON COLUMN public.refund_target_styles.source_member_key IS '原名单/原条件中的定位';

COMMENT ON COLUMN public.refund_target_styles.effect IS '包含或排除';

COMMENT ON COLUMN public.refund_target_styles.source_style_namespace IS '款码实际所属来源命名空间';

COMMENT ON COLUMN public.refund_target_styles.source_style_code IS '原来源款式编码';

COMMENT ON COLUMN public.refund_target_styles.condition_status IS '款码及匹配语义核验状态';

COMMENT ON COLUMN public.refund_target_styles.evidence_note IS '清单、别名或编码冲突说明';

CREATE TABLE public.sales_target_shops (
    target_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    source_member_key TEXT,
    evidence_note TEXT,
    CONSTRAINT pk_sales_target_shops_identity PRIMARY KEY (target_id, shop_id),
    CONSTRAINT fk_sales_target_shops_target_id FOREIGN KEY(target_id) REFERENCES public.sales_targets (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT fk_sales_target_shops_shop_id FOREIGN KEY(shop_id) REFERENCES public.shops (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_sales_target_shops_target_id_core_text CHECK (target_id IS NULL OR (btrim(target_id) <> '' AND target_id = btrim(target_id))),
    CONSTRAINT ck_sales_target_shops_shop_id_core_text CHECK (shop_id IS NULL OR (btrim(shop_id) <> '' AND shop_id = btrim(shop_id))),
    CONSTRAINT ck_sales_target_shops_source_member_key_text_nonblank CHECK (source_member_key IS NULL OR btrim(source_member_key) <> ''),
    CONSTRAINT ck_sales_target_shops_evidence_note_text_nonblank CHECK (evidence_note IS NULL OR btrim(evidence_note) <> '')
);

CREATE INDEX ix_sales_target_shops_query_15 ON public.sales_target_shops (shop_id, target_id);

COMMENT ON TABLE public.sales_target_shops IS '销售目标店铺成员';

COMMENT ON COLUMN public.sales_target_shops.target_id IS '所属销售目标';

COMMENT ON COLUMN public.sales_target_shops.shop_id IS '固定范围内的统一店铺';

COMMENT ON COLUMN public.sales_target_shops.source_member_key IS '原范围中该成员的确定定位';

COMMENT ON COLUMN public.sales_target_shops.evidence_note IS '原别名对应及成员核验说明';

CREATE TABLE public.score_standard_items (
    standard_id TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    basic_threshold NUMERIC,
    good_threshold NUMERIC,
    excellent_threshold NUMERIC,
    outstanding_threshold NUMERIC,
    growth_base_floor NUMERIC,
    threshold_status TEXT NOT NULL,
    precision_kind TEXT NOT NULL,
    reference_group_label TEXT,
    reference_category_system TEXT,
    reference_category_label TEXT,
    declared_sample_count BIGINT,
    declared_sample_unit TEXT,
    sample_basis_status TEXT NOT NULL,
    history_start DATE,
    history_end DATE,
    quantile_method TEXT,
    fallback_level TEXT,
    fallback_basis_status TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    missing_reason TEXT,
    CONSTRAINT pk_score_standard_items_identity PRIMARY KEY (standard_id, metric_code),
    CONSTRAINT fk_score_standard_items_standard_id FOREIGN KEY(standard_id) REFERENCES public.score_standards (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_score_standard_items_standard_id_core_text CHECK (standard_id IS NULL OR (btrim(standard_id) <> '' AND standard_id = btrim(standard_id))),
    CONSTRAINT ck_score_standard_items_metric_code_core_text CHECK (metric_code IS NULL OR (btrim(metric_code) <> '' AND metric_code = btrim(metric_code))),
    CONSTRAINT ck_score_standard_items_basic_threshold_core_finite CHECK (basic_threshold IS NULL OR basic_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_score_standard_items_good_threshold_core_finite CHECK (good_threshold IS NULL OR good_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_score_standard_items_excellent_threshold_core_finite CHECK (excellent_threshold IS NULL OR excellent_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_score_standard_items_outstanding_threshold_core_finite CHECK (outstanding_threshold IS NULL OR outstanding_threshold NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_score_standard_items_growth_base_floor_core_finite CHECK (growth_base_floor IS NULL OR growth_base_floor NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)),
    CONSTRAINT ck_score_standard_items_threshold_status_text_nonblank CHECK (threshold_status IS NULL OR btrim(threshold_status) <> ''),
    CONSTRAINT ck_score_standard_items_precision_kind_text_nonblank CHECK (precision_kind IS NULL OR btrim(precision_kind) <> ''),
    CONSTRAINT ck_score_standard_items_reference_group_label_text_nonblank CHECK (reference_group_label IS NULL OR btrim(reference_group_label) <> ''),
    CONSTRAINT ck_score_standard_items_reference_category_system_text_nonblank CHECK (reference_category_system IS NULL OR btrim(reference_category_system) <> ''),
    CONSTRAINT ck_score_standard_items_reference_category_label_text_nonblank CHECK (reference_category_label IS NULL OR btrim(reference_category_label) <> ''),
    CONSTRAINT ck_score_standard_items_declared_sample_unit_text_nonblank CHECK (declared_sample_unit IS NULL OR btrim(declared_sample_unit) <> ''),
    CONSTRAINT ck_score_standard_items_sample_basis_status_text_nonblank CHECK (sample_basis_status IS NULL OR btrim(sample_basis_status) <> ''),
    CONSTRAINT ck_score_standard_items_quantile_method_text_nonblank CHECK (quantile_method IS NULL OR btrim(quantile_method) <> ''),
    CONSTRAINT ck_score_standard_items_fallback_level_text_nonblank CHECK (fallback_level IS NULL OR btrim(fallback_level) <> ''),
    CONSTRAINT ck_score_standard_items_fallback_basis_status_text_nonblank CHECK (fallback_basis_status IS NULL OR btrim(fallback_basis_status) <> ''),
    CONSTRAINT ck_score_standard_items_source_locator_text_nonblank CHECK (source_locator IS NULL OR btrim(source_locator) <> ''),
    CONSTRAINT ck_score_standard_items_missing_reason_text_nonblank CHECK (missing_reason IS NULL OR btrim(missing_reason) <> ''),
    CONSTRAINT ck_score_standard_items_metric_code_values CHECK (metric_code IN ('visitors_7d', 'product_revenue_7d', 'units_7d', 'favorites_7d', 'score_conversion_rate_7d', 'revenue_per_visitor_7d', 'ad_roi_7d', 'ad_click_through_rate_7d', 'ad_click_conversion_rate_7d', 'revenue_multiple_7d')),
    CONSTRAINT ck_score_standard_items_threshold_status_values CHECK (threshold_status IN ('available', 'partial', 'missing', 'incompatible')),
    CONSTRAINT ck_score_standard_items_precision_kind_values CHECK (precision_kind IN ('source_display_rounded', 'full_precision', 'unknown')),
    CONSTRAINT ck_score_standard_items_sample_basis_status_values CHECK (sample_basis_status IN ('verified_in_scope', 'declared_unverified', 'unknown')),
    CONSTRAINT ck_score_standard_items_fallback_basis_status_values CHECK (fallback_basis_status IN ('not_used', 'verified_in_scope', 'declared_unverified', 'unknown')),
    CONSTRAINT ck_score_standard_items_threshold_state_shape CHECK ((threshold_status = 'available' AND num_nonnulls(basic_threshold, good_threshold, excellent_threshold, outstanding_threshold) = 4 AND basic_threshold <= good_threshold AND good_threshold <= excellent_threshold AND excellent_threshold <= outstanding_threshold) OR (threshold_status = 'partial' AND num_nonnulls(basic_threshold, good_threshold, excellent_threshold, outstanding_threshold) BETWEEN 1 AND 3) OR (threshold_status = 'missing' AND num_nonnulls(basic_threshold, good_threshold, excellent_threshold, outstanding_threshold) = 0) OR threshold_status = 'incompatible'),
    CONSTRAINT ck_score_standard_items_threshold_missing_reason CHECK (threshold_status = 'available' OR missing_reason IS NOT NULL),
    CONSTRAINT ck_score_standard_items_growth_floor_shape CHECK (growth_base_floor IS NULL OR (metric_code = 'revenue_multiple_7d' AND growth_base_floor >= 0)),
    CONSTRAINT ck_score_standard_items_sample_count_nonnegative CHECK (declared_sample_count IS NULL OR declared_sample_count >= 0),
    CONSTRAINT ck_score_standard_items_history_start_history_end_pair CHECK ((history_start IS NULL AND history_end IS NULL) OR (history_start IS NOT NULL AND history_end IS NOT NULL AND history_start <= history_end))
);

COMMENT ON TABLE public.score_standard_items IS '逐指标四线与参照';

COMMENT ON COLUMN public.score_standard_items.standard_id IS '所属不可变标准';

COMMENT ON COLUMN public.score_standard_items.metric_code IS '十项评分中的一个有限指标代码';

COMMENT ON COLUMN public.score_standard_items.basic_threshold IS '基础档下限';

COMMENT ON COLUMN public.score_standard_items.good_threshold IS '良好档下限';

COMMENT ON COLUMN public.score_standard_items.excellent_threshold IS '优秀档下限';

COMMENT ON COLUMN public.score_standard_items.outstanding_threshold IS '突出档下限';

COMMENT ON COLUMN public.score_standard_items.growth_base_floor IS '交易倍数项的前7日金额基数线';

COMMENT ON COLUMN public.score_standard_items.threshold_status IS '本项四线可用性';

COMMENT ON COLUMN public.score_standard_items.precision_kind IS '阈值精度出处';

COMMENT ON COLUMN public.score_standard_items.reference_group_label IS '本项实际参照组原声明';

COMMENT ON COLUMN public.score_standard_items.reference_category_system IS '本项借用/参照的原分类体系';

COMMENT ON COLUMN public.score_standard_items.reference_category_label IS '本项借用/参照的原品类标签';

COMMENT ON COLUMN public.score_standard_items.declared_sample_count IS '来源声明的参照样本数';

COMMENT ON COLUMN public.score_standard_items.declared_sample_unit IS '原样本单位声明';

COMMENT ON COLUMN public.score_standard_items.sample_basis_status IS '样本口径核验状态';

COMMENT ON COLUMN public.score_standard_items.history_start IS '有证据的参照历史起日';

COMMENT ON COLUMN public.score_standard_items.history_end IS '有证据的参照历史止日';

COMMENT ON COLUMN public.score_standard_items.quantile_method IS '有证据的分位/插值方法';

COMMENT ON COLUMN public.score_standard_items.fallback_level IS '本项实际回退层级/声明';

COMMENT ON COLUMN public.score_standard_items.fallback_basis_status IS '回退依据状态';

COMMENT ON COLUMN public.score_standard_items.source_locator IS '本指标四线和参照的原定位';

COMMENT ON COLUMN public.score_standard_items.missing_reason IS '缺线、单位或参照冲突说明';

CREATE TABLE public.product_score_items (
    score_id TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    signal_status TEXT NOT NULL,
    missing_reason_code TEXT,
    missing_reason_text TEXT,
    evidence_context JSONB,
    CONSTRAINT pk_product_score_items_identity PRIMARY KEY (score_id, metric_code),
    CONSTRAINT fk_product_score_items_score_id FOREIGN KEY(score_id) REFERENCES public.product_scores (id) ON DELETE RESTRICT ON UPDATE NO ACTION,
    CONSTRAINT ck_product_score_items_score_id_core_text CHECK (score_id IS NULL OR (btrim(score_id) <> '' AND score_id = btrim(score_id))),
    CONSTRAINT ck_product_score_items_metric_code_core_text CHECK (metric_code IS NULL OR (btrim(metric_code) <> '' AND metric_code = btrim(metric_code))),
    CONSTRAINT ck_product_score_items_signal_status_text_nonblank CHECK (signal_status IS NULL OR btrim(signal_status) <> ''),
    CONSTRAINT ck_product_score_items_missing_reason_code_text_nonblank CHECK (missing_reason_code IS NULL OR btrim(missing_reason_code) <> ''),
    CONSTRAINT ck_product_score_items_missing_reason_text_text_nonblank CHECK (missing_reason_text IS NULL OR btrim(missing_reason_text) <> ''),
    CONSTRAINT ck_product_score_items_metric_code_values CHECK (metric_code IN ('visitors_7d', 'product_revenue_7d', 'units_7d', 'favorites_7d', 'score_conversion_rate_7d', 'revenue_per_visitor_7d', 'ad_roi_7d', 'ad_click_through_rate_7d', 'ad_click_conversion_rate_7d', 'revenue_multiple_7d')),
    CONSTRAINT ck_product_score_items_signal_status_values CHECK (signal_status IN ('valid', 'partial', 'missing', 'not_computable')),
    CONSTRAINT ck_product_score_items_missing_reason_shape CHECK ((signal_status = 'valid' AND missing_reason_code IS NULL AND missing_reason_text IS NULL) OR (signal_status <> 'valid' AND missing_reason_code IS NOT NULL AND (missing_reason_text IS NOT NULL OR evidence_context IS NOT NULL))),
    CONSTRAINT ck_product_score_items_evidence_object_keys CHECK (evidence_context IS NULL OR CASE WHEN jsonb_typeof(evidence_context) = 'object' THEN (evidence_context <> '{}'::jsonb AND evidence_context - ARRAY['input_role','field_codes','missing_dates','note']::text[] = '{}'::jsonb) IS TRUE ELSE FALSE END),
    CONSTRAINT ck_product_score_items_evidence_role CHECK (evidence_context IS NULL OR NOT (evidence_context ? 'input_role') OR (jsonb_typeof(evidence_context->'input_role') = 'string' AND evidence_context->>'input_role' IN ('product_current', 'product_previous', 'ad_current', 'profile', 'standard', 'product_history')) IS TRUE),
    CONSTRAINT ck_product_score_items_evidence_note CHECK (evidence_context IS NULL OR NOT (evidence_context ? 'note') OR (jsonb_typeof(evidence_context->'note') = 'string' AND btrim(evidence_context->>'note') <> '') IS TRUE),
    CONSTRAINT ck_product_score_items_evidence_fields CHECK (evidence_context IS NULL OR NOT (evidence_context ? 'field_codes') OR CASE WHEN jsonb_typeof(evidence_context->'field_codes') = 'array' THEN (jsonb_array_length(evidence_context->'field_codes') > 0 AND evidence_context->'field_codes' <@ '["feature.visitors_7d","feature.product_revenue_7d","feature.units_7d","feature.favorites_7d","feature.buyers_7d","feature.orders_7d","feature.ad_spend_7d","feature.ad_attributed_revenue_7d","feature.ad_impressions_7d","feature.ad_clicks_7d","feature.ad_deals_7d","feature.previous_revenue_7d","feature.previous_orders_7d","standard.basic_threshold","standard.good_threshold","standard.excellent_threshold","standard.outstanding_threshold","standard.growth_base_floor","standard.threshold_status","standard.precision_kind","standard.reference_group_label","standard.reference_category_system","standard.reference_category_label","standard.declared_sample_count","standard.declared_sample_unit","standard.sample_basis_status","standard.history_start","standard.history_end","standard.quantile_method","standard.fallback_level","standard.fallback_basis_status","standard.source_locator","standard.missing_reason"]'::jsonb AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.visitors_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.product_revenue_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.units_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.favorites_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.buyers_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.orders_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.ad_spend_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.ad_attributed_revenue_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.ad_impressions_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.ad_clicks_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.ad_deals_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.previous_revenue_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "feature.previous_orders_7d")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.basic_threshold")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.good_threshold")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.excellent_threshold")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.outstanding_threshold")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.growth_base_floor")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.threshold_status")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.precision_kind")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.reference_group_label")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.reference_category_system")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.reference_category_label")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.declared_sample_count")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.declared_sample_unit")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.sample_basis_status")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.history_start")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.history_end")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.quantile_method")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.fallback_level")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.fallback_basis_status")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.source_locator")')) <= 1 AND jsonb_array_length(jsonb_path_query_array(evidence_context->'field_codes', '$[*] ? (@ == "standard.missing_reason")')) <= 1) IS TRUE ELSE FALSE END),
    CONSTRAINT ck_product_score_items_evidence_dates CHECK (evidence_context IS NULL OR NOT (evidence_context ? 'missing_dates') OR CASE WHEN jsonb_typeof(evidence_context->'missing_dates') = 'array' THEN (jsonb_array_length(evidence_context->'missing_dates') > 0 AND evidence_context ? 'input_role' AND NOT jsonb_path_exists(evidence_context->'missing_dates', '$[*] ? (@.type() != "string" || !(@ like_regex "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"))')) IS TRUE ELSE FALSE END)
);

COMMENT ON TABLE public.product_score_items IS '评分分项状态与缺项证据';

COMMENT ON COLUMN public.product_score_items.score_id IS '所属商品评分结果';

COMMENT ON COLUMN public.product_score_items.metric_code IS '固定十项中的指标代码';

COMMENT ON COLUMN public.product_score_items.signal_status IS '本项本次可用状态';

COMMENT ON COLUMN public.product_score_items.missing_reason_code IS '缺项/不可计分原因代码';

COMMENT ON COLUMN public.product_score_items.missing_reason_text IS '具体缺失字段、日期或标准解释';

COMMENT ON COLUMN public.product_score_items.evidence_context IS '本项核验所需非数值证据';

CREATE FUNCTION public.pdd_v2_identity_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '身份不可物理删除，请使用受控软删除' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := transaction_timestamp();
        NEW.updated_at := NEW.created_at;
        IF NEW.deleted_at IS NOT NULL THEN
            RAISE EXCEPTION '新身份不可带伪造的软删除事件' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION '身份ID和首次创建时刻不可改写' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'listings' AND (
        (to_jsonb(NEW)->'shop_id') IS DISTINCT FROM (to_jsonb(OLD)->'shop_id') OR
        (to_jsonb(NEW)->'platform_code') IS DISTINCT FROM (to_jsonb(OLD)->'platform_code') OR
        (to_jsonb(NEW)->'platform_product_id') IS DISTINCT FROM
        (to_jsonb(OLD)->'platform_product_id')
    ) THEN
        RAISE EXCEPTION '链接业务身份不可迁移或重用' USING ERRCODE = '23514';
    END IF;
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
        NEW.deleted_at := GREATEST(statement_timestamp(), OLD.updated_at, OLD.created_at);
        NEW.status := 'inactive';
    ELSIF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT NULL THEN
        IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
            RAISE EXCEPTION '已有软删除事件时刻不可改写' USING ERRCODE = '23514';
        END IF;
        IF NEW.status IS DISTINCT FROM 'inactive' THEN
            RAISE EXCEPTION '软删除身份必须保持停用，恢复须清空deleted_at'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF (to_jsonb(NEW) - ARRAY['created_at', 'updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at', 'updated_at']) THEN
        NEW.updated_at := GREATEST(statement_timestamp(), OLD.updated_at, OLD.created_at);
    ELSE
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_version_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.state IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION '封版或拒绝版本不可删除' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state IS DISTINCT FROM 'draft' OR NEW.sealed_at IS NOT NULL THEN
            RAISE EXCEPTION '版本须从未封版draft创建' USING ERRCODE = '23514';
        END IF;
        NEW.created_at := transaction_timestamp();
        IF NEW.received_at IS NOT NULL THEN
            NEW.received_at := GREATEST(statement_timestamp(), NEW.created_at);
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.dataset_code IS DISTINCT FROM OLD.dataset_code OR
       NEW.origin_kind IS DISTINCT FROM OLD.origin_kind OR
       NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION '版本身份、用途、来源类别和创建时刻不可改写' USING ERRCODE = '23514';
    END IF;
    IF OLD.state = 'validated' AND NEW.state = 'published' THEN
        IF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state') THEN
            RAISE EXCEPTION '封版后发布只能改变state' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state <> 'draft' THEN
        IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION '封版或拒绝版本不可改写' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.state NOT IN ('draft', 'validated', 'rejected') OR NEW.state IS NULL THEN
        RAISE EXCEPTION '非法版本状态转换' USING ERRCODE = '23514';
    END IF;
    IF OLD.received_at IS NOT NULL AND NEW.received_at IS DISTINCT FROM OLD.received_at THEN
        RAISE EXCEPTION '首次接收完成事件不可改写' USING ERRCODE = '23514';
    ELSIF OLD.received_at IS NULL AND NEW.received_at IS NOT NULL THEN
        NEW.received_at := GREATEST(statement_timestamp(), NEW.created_at);
    END IF;
    IF NEW.state = 'validated' THEN
        NEW.sealed_at := GREATEST(statement_timestamp(), NEW.created_at, NEW.received_at);
    ELSIF NEW.sealed_at IS NOT NULL THEN
        RAISE EXCEPTION '未校验版本不可带封版时刻' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_assert_draft_version(version_key text) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE
    version_state text;
BEGIN
    SELECT state INTO version_state FROM public.dataset_versions
        WHERE id = version_key FOR SHARE;
    IF NOT FOUND OR version_state IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION '内容仅可写入存在的draft版本' USING ERRCODE = '23514';
    END IF;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_content_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE
    version_keys text[] := ARRAY[]::text[];
    version_key text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        version_keys := array_append(version_keys, OLD.dataset_version_id);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        version_keys := array_append(version_keys, NEW.dataset_version_id);
    END IF;
    FOR version_key IN
        SELECT DISTINCT value FROM unnest(version_keys) AS keys(value) ORDER BY value
    LOOP
        PERFORM public.pdd_v2_assert_draft_version(version_key);
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_child_content_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE
    parent_keys text[] := ARRAY[]::text[];
    version_keys text[] := ARRAY[]::text[];
    parent_key text;
    version_key text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        parent_keys := array_append(parent_keys, to_jsonb(OLD)->>TG_ARGV[1]);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        parent_keys := array_append(parent_keys, to_jsonb(NEW)->>TG_ARGV[1]);
    END IF;
    FOR parent_key IN
        SELECT DISTINCT value FROM unnest(parent_keys) AS keys(value) ORDER BY value
    LOOP
        -- 先锁父行，避免成员取到父行迁版之前的过期版本号。
        EXECUTE 'SELECT dataset_version_id FROM public.' || quote_ident(TG_ARGV[0]) ||
                ' WHERE id = $1 FOR SHARE' INTO version_key USING parent_key;
        IF version_key IS NULL THEN
            RAISE EXCEPTION '成员须引用存在的父记录' USING ERRCODE = '23514';
        END IF;
        version_keys := array_append(version_keys, version_key);
    END LOOP;
    FOR version_key IN
        SELECT DISTINCT value FROM unnest(version_keys) AS keys(value) ORDER BY value
    LOOP
        PERFORM public.pdd_v2_assert_draft_version(version_key);
    END LOOP;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_inputs_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE
    version_keys text[] := ARRAY[]::text[];
    version_key text;
    output_state text;
    output_kind text;
    input_state text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        version_keys := array_append(version_keys, OLD.output_version_id);
        version_keys := array_append(version_keys, OLD.input_version_id);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        version_keys := array_append(version_keys, NEW.output_version_id);
        version_keys := array_append(version_keys, NEW.input_version_id);
    END IF;
    -- 锁同一次DML涉及的两侧版本，稳定排序；FOR KEY SHARE不能阻挡state更新。
    FOR version_key IN
        SELECT DISTINCT value FROM unnest(version_keys) AS keys(value) ORDER BY value
    LOOP
        PERFORM id FROM public.dataset_versions WHERE id = version_key FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '输入关系须引用存在的版本' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    IF TG_OP <> 'INSERT' THEN
        PERFORM public.pdd_v2_assert_draft_version(OLD.output_version_id);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        SELECT state, origin_kind INTO output_state, output_kind
            FROM public.dataset_versions WHERE id = NEW.output_version_id;
        SELECT state INTO input_state
            FROM public.dataset_versions WHERE id = NEW.input_version_id;
        IF output_state IS DISTINCT FROM 'draft' OR output_kind IS DISTINCT FROM 'derived' OR
           input_state NOT IN ('validated', 'published') OR input_state IS NULL THEN
            RAISE EXCEPTION '输入边须由derived草稿指向已封版输入' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_rule_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
    RAISE EXCEPTION '规则包只可追加修订，不可原地更新或删除' USING ERRCODE = '23514';
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_current_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE
    version_state text;
    version_code text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '当前入口不可删除重建，防止并发修订号回到0'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.dataset_code IS DISTINCT FROM OLD.dataset_code THEN
        RAISE EXCEPTION '当前入口用途不可改写' USING ERRCODE = '23514';
    END IF;
    SELECT state, dataset_code INTO version_state, version_code
        FROM public.dataset_versions WHERE id = NEW.version_id FOR SHARE;
    IF NOT FOUND OR version_state IS DISTINCT FROM 'published' OR
       version_code IS DISTINCT FROM NEW.dataset_code THEN
        RAISE EXCEPTION '当前入口只可指向同用途的published版本' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        NEW.revision := 0;
        NEW.updated_at := statement_timestamp();
    ELSIF NEW.version_id IS DISTINCT FROM OLD.version_id THEN
        NEW.revision := OLD.revision + 1;
        NEW.updated_at := GREATEST(statement_timestamp(), OLD.updated_at);
    ELSE
        NEW.revision := OLD.revision;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_v2_forbid_truncate() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
    RAISE EXCEPTION '受保护表不允许TRUNCATE，维护须另行评审'
        USING ERRCODE = '23514';
END;
$pdd_v2$;

CREATE TRIGGER pdd_v2_trg_shops_identity BEFORE INSERT OR UPDATE OR DELETE ON public.shops FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_identity_guard();

CREATE TRIGGER pdd_v2_trg_listings_identity BEFORE INSERT OR UPDATE OR DELETE ON public.listings FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_identity_guard();

CREATE TRIGGER pdd_v2_trg_dataset_versions_version BEFORE INSERT OR UPDATE OR DELETE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_version_guard();

CREATE TRIGGER pdd_v2_trg_rule_sets_rule BEFORE UPDATE OR DELETE ON public.rule_sets FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_rule_guard();

CREATE TRIGGER pdd_v2_trg_current_datasets_current BEFORE INSERT OR UPDATE OR DELETE ON public.current_datasets FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_current_guard();

CREATE TRIGGER pdd_v2_trg_version_inputs_inputs BEFORE INSERT OR UPDATE OR DELETE ON public.version_inputs FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_inputs_guard();

CREATE TRIGGER pdd_v2_trg_profile_source_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.profile_source_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_profile_listing_links_content BEFORE INSERT OR UPDATE OR DELETE ON public.profile_listing_links FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_product_daily_facts_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_daily_facts FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_shop_daily_facts_content BEFORE INSERT OR UPDATE OR DELETE ON public.shop_daily_facts FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_product_promotion_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_promotion_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_shop_channel_promotion_daily_content BEFORE INSERT OR UPDATE OR DELETE ON public.shop_channel_promotion_daily FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_shop_rating_daily_content BEFORE INSERT OR UPDATE OR DELETE ON public.shop_rating_daily FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_shop_refund_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.shop_refund_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_shop_region_daily_content BEFORE INSERT OR UPDATE OR DELETE ON public.shop_region_daily FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_product_region_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_region_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_product_sales_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_sales_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_return_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.return_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_sales_targets_content BEFORE INSERT OR UPDATE OR DELETE ON public.sales_targets FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_refund_targets_content BEFORE INSERT OR UPDATE OR DELETE ON public.refund_targets FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_score_standards_content BEFORE INSERT OR UPDATE OR DELETE ON public.score_standards FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_product_scores_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_scores FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_aftersales_reports_content BEFORE INSERT OR UPDATE OR DELETE ON public.aftersales_reports FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_coverage_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.coverage_records FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_content_guard();

CREATE TRIGGER pdd_v2_trg_sales_target_shops_child BEFORE INSERT OR UPDATE OR DELETE ON public.sales_target_shops FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_child_content_guard('sales_targets', 'target_id');

CREATE TRIGGER pdd_v2_trg_refund_target_categories_child BEFORE INSERT OR UPDATE OR DELETE ON public.refund_target_categories FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_child_content_guard('refund_targets', 'target_id');

CREATE TRIGGER pdd_v2_trg_refund_target_styles_child BEFORE INSERT OR UPDATE OR DELETE ON public.refund_target_styles FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_child_content_guard('refund_targets', 'target_id');

CREATE TRIGGER pdd_v2_trg_score_standard_items_child BEFORE INSERT OR UPDATE OR DELETE ON public.score_standard_items FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_child_content_guard('score_standards', 'standard_id');

CREATE TRIGGER pdd_v2_trg_product_score_items_child BEFORE INSERT OR UPDATE OR DELETE ON public.product_score_items FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_child_content_guard('product_scores', 'score_id');

CREATE TRIGGER pdd_v2_trg_aftersales_reports_truncate BEFORE TRUNCATE ON public.aftersales_reports FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_coverage_records_truncate BEFORE TRUNCATE ON public.coverage_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_current_datasets_truncate BEFORE TRUNCATE ON public.current_datasets FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_dataset_versions_truncate BEFORE TRUNCATE ON public.dataset_versions FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_listings_truncate BEFORE TRUNCATE ON public.listings FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_daily_facts_truncate BEFORE TRUNCATE ON public.product_daily_facts FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_promotion_records_truncate BEFORE TRUNCATE ON public.product_promotion_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_region_records_truncate BEFORE TRUNCATE ON public.product_region_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_sales_records_truncate BEFORE TRUNCATE ON public.product_sales_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_score_items_truncate BEFORE TRUNCATE ON public.product_score_items FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_product_scores_truncate BEFORE TRUNCATE ON public.product_scores FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_profile_listing_links_truncate BEFORE TRUNCATE ON public.profile_listing_links FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_profile_source_records_truncate BEFORE TRUNCATE ON public.profile_source_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_refund_target_categories_truncate BEFORE TRUNCATE ON public.refund_target_categories FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_refund_target_styles_truncate BEFORE TRUNCATE ON public.refund_target_styles FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_refund_targets_truncate BEFORE TRUNCATE ON public.refund_targets FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_return_records_truncate BEFORE TRUNCATE ON public.return_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_rule_sets_truncate BEFORE TRUNCATE ON public.rule_sets FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_sales_target_shops_truncate BEFORE TRUNCATE ON public.sales_target_shops FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_sales_targets_truncate BEFORE TRUNCATE ON public.sales_targets FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_score_standard_items_truncate BEFORE TRUNCATE ON public.score_standard_items FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_score_standards_truncate BEFORE TRUNCATE ON public.score_standards FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shop_channel_promotion_daily_truncate BEFORE TRUNCATE ON public.shop_channel_promotion_daily FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shop_daily_facts_truncate BEFORE TRUNCATE ON public.shop_daily_facts FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shop_rating_daily_truncate BEFORE TRUNCATE ON public.shop_rating_daily FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shop_refund_records_truncate BEFORE TRUNCATE ON public.shop_refund_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shop_region_daily_truncate BEFORE TRUNCATE ON public.shop_region_daily FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_shops_truncate BEFORE TRUNCATE ON public.shops FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

CREATE TRIGGER pdd_v2_trg_version_inputs_truncate BEFORE TRUNCATE ON public.version_inputs FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

INSERT INTO public.alembic_version_v2 (version_num) VALUES ('20261028_01') RETURNING public.alembic_version_v2.version_num;

-- Running upgrade 20261028_01 -> 20261028_02










UPDATE public.alembic_version_v2 SET version_num='20261028_02' WHERE public.alembic_version_v2.version_num = '20261028_01';

-- Running upgrade 20261028_02 -> 20261028_03








UPDATE public.alembic_version_v2 SET version_num='20261028_03' WHERE public.alembic_version_v2.version_num = '20261028_02';

-- Running upgrade 20261028_03 -> 20261028_04












UPDATE public.alembic_version_v2 SET version_num='20261028_04' WHERE public.alembic_version_v2.version_num = '20261028_03';

-- Running upgrade 20261028_04 -> 20261028_05



UPDATE public.alembic_version_v2 SET version_num='20261028_05' WHERE public.alembic_version_v2.version_num = '20261028_04';

-- Running upgrade 20261028_05 -> 20261028_06




UPDATE public.alembic_version_v2 SET version_num='20261028_06' WHERE public.alembic_version_v2.version_num = '20261028_05';

-- Running upgrade 20261028_06 -> 20261028_07

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
);

CREATE INDEX ix_weather_publications_history ON public.weather_publications (issue_date DESC, revision_no DESC, id);

COMMENT ON TABLE public.weather_publications IS 'NMC两官文及确定解释的不可变发布文档';

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
END $body$;

CREATE TRIGGER weather_document_immutable BEFORE INSERT OR UPDATE OR DELETE ON public.weather_publications FOR EACH ROW EXECUTE FUNCTION public.pdd_weather_document_guard();

CREATE TRIGGER weather_document_no_truncate BEFORE TRUNCATE ON public.weather_publications FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();

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
END $body$;

CREATE TRIGGER weather_parent_content BEFORE UPDATE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.pdd_weather_parent_guard();



UPDATE public.alembic_version_v2 SET version_num='20261028_07' WHERE public.alembic_version_v2.version_num = '20261028_06';

-- Running upgrade 20261028_07 -> 20261028_08

ALTER TABLE public.refund_targets ADD COLUMN metric_code TEXT NOT NULL DEFAULT 'total_refund_rate';

ALTER TABLE public.refund_targets ADD COLUMN lower_rate NUMERIC NULL;

ALTER TABLE public.refund_targets ADD COLUMN upper_rate NUMERIC NULL;

ALTER TABLE public.refund_targets ADD COLUMN application_policy_code TEXT NULL;

ALTER TABLE public.refund_targets ALTER COLUMN target_rate DROP NOT NULL;

ALTER TABLE public.refund_targets ADD CONSTRAINT ck_refund_targets_metric_code_values CHECK (metric_code IN ('total_refund_rate','aftersale_refund_rate'));

ALTER TABLE public.refund_targets ADD CONSTRAINT ck_refund_targets_application_policy_code_values CHECK (application_policy_code IN ('rolling_30d_and_natural_month'));

ALTER TABLE public.refund_targets ADD CONSTRAINT ck_refund_targets_scalar_or_range
CHECK (
 (target_rate IS NOT NULL AND lower_rate IS NULL AND upper_rate IS NULL)
 OR
 (target_rate IS NULL AND lower_rate IS NOT NULL AND upper_rate IS NOT NULL
  AND lower_rate NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND upper_rate NOT IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
  AND 0 <= lower_rate AND lower_rate <= upper_rate AND upper_rate <= 1
  AND application_policy_code IS NOT NULL)
);

COMMENT ON COLUMN public.refund_targets.metric_code IS '退款率指标代码';

COMMENT ON COLUMN public.refund_targets.lower_rate IS '退款率目标下限';

COMMENT ON COLUMN public.refund_targets.upper_rate IS '退款率目标上限';

COMMENT ON COLUMN public.refund_targets.application_policy_code IS '滚动30天与自然月适用策略';




UPDATE public.alembic_version_v2 SET version_num='20261028_08' WHERE public.alembic_version_v2.version_num = '20261028_07';

-- Running upgrade 20261028_08 -> 20261031_09

CREATE INDEX ix_product_promotion_records_version_period_start ON public.product_promotion_records (dataset_version_id, period_start);

CREATE INDEX ix_product_region_records_version_period_start ON public.product_region_records (dataset_version_id, period_start);

UPDATE public.alembic_version_v2 SET version_num='20261031_09' WHERE public.alembic_version_v2.version_num = '20261028_08';

-- Running upgrade 20261031_09 -> 20261103_10

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



UPDATE public.alembic_version_v2 SET version_num='20261103_10' WHERE public.alembic_version_v2.version_num = '20261031_09';

-- Running upgrade 20261103_10 -> 20261104_11

ALTER TABLE public.source_checkpoints ADD COLUMN observed_at TIMESTAMPTZ, ADD COLUMN web_updated_at TEXT, ADD COLUMN web_checked_at TIMESTAMPTZ, ADD CONSTRAINT source_web_time_pair CHECK ((web_updated_at IS NULL) = (web_checked_at IS NULL)), ADD CONSTRAINT source_web_time_format CHECK (web_updated_at IS NULL OR web_updated_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$');

UPDATE public.source_checkpoints SET observed_at=checked_at WHERE check_status='succeeded';

UPDATE public.alembic_version_v2 SET version_num='20261104_11' WHERE public.alembic_version_v2.version_num = '20261103_10';

-- Running upgrade 20261104_11 -> 20261104_12

ALTER TABLE public.source_checkpoints ADD COLUMN scheduled_slot_at TIMESTAMPTZ, ADD COLUMN scheduled_attempts INTEGER NOT NULL DEFAULT 0 CHECK (scheduled_attempts BETWEEN 0 AND 2), ADD COLUMN sync_started_at TIMESTAMPTZ, ADD COLUMN sync_finished_at TIMESTAMPTZ, ADD COLUMN sync_status TEXT CHECK(sync_status IN ('running','succeeded','failed')), ADD CONSTRAINT source_sync_times CHECK ((sync_status IS NULL AND sync_started_at IS NULL AND sync_finished_at IS NULL) OR (sync_status IS NOT NULL AND ((sync_status='running' AND sync_started_at IS NOT NULL AND sync_finished_at IS NULL) OR (sync_status IN ('succeeded','failed') AND sync_started_at IS NOT NULL AND sync_finished_at IS NOT NULL AND sync_finished_at>=sync_started_at))));

UPDATE public.alembic_version_v2 SET version_num='20261104_12' WHERE public.alembic_version_v2.version_num = '20261104_11';

-- Running upgrade 20261104_12 -> 20261110_13

CREATE TABLE public.retention_archives (
      id text PRIMARY KEY, database_name text NOT NULL, schema_head text NOT NULL,
      format_revision integer NOT NULL CHECK(format_revision=1),
      cipher_sha256 text NOT NULL CHECK(cipher_sha256 ~ '^[0-9a-f]{64}$'),
      manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
      locations jsonb NOT NULL CHECK(jsonb_typeof(locations)='array'),
      verified_at timestamptz NOT NULL, restored_at timestamptz NOT NULL);

CREATE TABLE public.dataset_storage (
      version_id text PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      state text NOT NULL DEFAULT 'online' CHECK(state IN ('online','offline')),
      archive_id text REFERENCES public.retention_archives(id) ON DELETE RESTRICT,
      keep_online boolean NOT NULL DEFAULT false,
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(state='online' OR archive_id IS NOT NULL),
      CHECK(NOT keep_online OR state='online'));

CREATE TABLE public.dataset_publications (
      id text PRIMARY KEY, dataset_code text NOT NULL, revision bigint NOT NULL CHECK(revision>=0),
      previous_version_id text REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      version_id text NOT NULL REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(dataset_code,revision));

CREATE INDEX ix_publications_previous_version_id ON public.dataset_publications(previous_version_id);

CREATE INDEX ix_publications_version_id ON public.dataset_publications(version_id);

CREATE INDEX ix_storage_archive ON public.dataset_storage(archive_id);

CREATE TABLE public.retention_operations (
      id text PRIMARY KEY, archive_id text NOT NULL
      REFERENCES public.retention_archives(id) ON DELETE RESTRICT,
      kind text NOT NULL CHECK(kind IN ('offline','restore')),
      plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
      policy_revision text NOT NULL, code_revision text NOT NULL, transaction_id bigint NOT NULL,
      status text NOT NULL CHECK(status IN ('running','succeeded')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
      CHECK((status='running' AND finished_at IS NULL) OR
            (status='succeeded' AND finished_at>=created_at)));

CREATE TABLE public.retention_operation_items (
      operation_id text REFERENCES public.retention_operations(id) ON DELETE RESTRICT,
      version_id text REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      table_name text CHECK(table_name IN
        ('product_daily_facts','product_promotion_records','product_region_records')),
      row_count bigint NOT NULL CHECK(row_count>=0),
      content_sha256 text NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
      PRIMARY KEY(operation_id,version_id,table_name));

CREATE INDEX ix_retention_items_version ON public.retention_operation_items(version_id);

INSERT INTO public.dataset_storage(version_id) SELECT id FROM public.dataset_versions;

CREATE FUNCTION public.pdd_retention_online(version_key text) RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
BEGIN
      IF EXISTS(WITH RECURSIVE deps(id) AS (
        SELECT version_key UNION SELECT i.input_version_id FROM public.version_inputs i
        JOIN deps d ON d.id=i.output_version_id)
        SELECT 1 FROM deps d LEFT JOIN public.dataset_storage s ON s.version_id=d.id
        WHERE s.state IS DISTINCT FROM 'online') THEN
        RAISE EXCEPTION 'DATASET_RESTORE_REQUIRED' USING ERRCODE='23514';
      END IF;
    END;
$retention$;

CREATE FUNCTION public.pdd_retention_register() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $retention$
BEGIN
      INSERT INTO public.dataset_storage(version_id) VALUES(NEW.id);
      RETURN NEW;
    END;
$retention$;

CREATE TRIGGER pdd_retention_register AFTER INSERT ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_register();

CREATE FUNCTION public.pdd_retention_publish() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
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
    END;
$retention$;

CREATE TRIGGER pdd_retention_publish BEFORE UPDATE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_publish();

CREATE TRIGGER pdd_retention_publish BEFORE INSERT OR UPDATE ON public.version_inputs FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_publish();

CREATE TRIGGER pdd_retention_publish BEFORE INSERT OR UPDATE ON public.current_datasets FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_publish();

CREATE FUNCTION public.pdd_retention_publication() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $retention$
BEGIN
      IF TG_OP='INSERT' OR NEW.version_id IS DISTINCT FROM OLD.version_id THEN
        INSERT INTO public.dataset_publications(id,dataset_code,revision,
          previous_version_id,version_id)
        VALUES(NEW.dataset_code || ':' || NEW.revision,NEW.dataset_code,NEW.revision,
          CASE WHEN TG_OP='UPDATE' THEN OLD.version_id ELSE NULL END, NEW.version_id);
      END IF;
      RETURN NEW;
    END;
$retention$;

CREATE TRIGGER pdd_retention_publication AFTER INSERT OR UPDATE ON public.current_datasets FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_publication();

CREATE FUNCTION public.pdd_retention_context(version_key text, table_key text, action text) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
BEGIN
      RETURN EXISTS(SELECT 1 FROM public.retention_operations o
        JOIN public.retention_operation_items i ON i.operation_id=o.id
        JOIN public.retention_archives a ON a.id=o.archive_id
        WHERE o.id=current_setting('pdd.retention_operation',true)
          AND o.transaction_id=txid_current() AND o.status='running' AND o.kind=action
          AND i.version_id=version_key AND i.table_name=table_key
          AND a.database_name=current_database() AND a.schema_head='20261110_13');
    END;
$retention$;

CREATE FUNCTION public.pdd_retention_content() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
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
    END;
$retention$;

DROP TRIGGER pdd_v2_trg_product_daily_facts_content ON public.product_daily_facts;

CREATE TRIGGER pdd_v2_trg_product_daily_facts_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_daily_facts FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_content();

DROP TRIGGER pdd_v2_trg_product_promotion_records_content ON public.product_promotion_records;

CREATE TRIGGER pdd_v2_trg_product_promotion_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_promotion_records FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_content();

DROP TRIGGER pdd_v2_trg_product_region_records_content ON public.product_region_records;

CREATE TRIGGER pdd_v2_trg_product_region_records_content BEFORE INSERT OR UPDATE OR DELETE ON public.product_region_records FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_content();

CREATE FUNCTION public.pdd_retention_item_guard() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
DECLARE action text; archive text; ver_code text; expected_table text; exit_at timestamptz;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'RETENTION_AUDIT_IMMUTABLE'; END IF;
      IF NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'RETENTION_BUSY'; END IF;
      IF current_database() !~ '^pdd_operations_v2_test_[0-9a-f]{12}$' THEN
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
        IF EXISTS(SELECT 1 FROM (
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
) p WHERE p.id=NEW.version_id)
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
$retention$;

CREATE TRIGGER pdd_retention_item_guard BEFORE INSERT OR UPDATE OR DELETE ON public.retention_operation_items FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_item_guard();

CREATE FUNCTION public.pdd_retention_storage_guard() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
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
    END;
$retention$;

CREATE TRIGGER pdd_retention_storage_guard BEFORE UPDATE OR DELETE ON public.dataset_storage FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_storage_guard();

CREATE FUNCTION public.pdd_retention_operation_guard() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
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
    END;
$retention$;

CREATE TRIGGER pdd_retention_operation_guard BEFORE INSERT OR UPDATE OR DELETE ON public.retention_operations FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_operation_guard();

CREATE FUNCTION public.pdd_retention_complete() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $retention$
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
    END;
$retention$;

CREATE CONSTRAINT TRIGGER pdd_retention_complete AFTER INSERT ON public.retention_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.pdd_retention_complete();

CREATE TRIGGER pdd_retention_immutable BEFORE UPDATE OR DELETE ON public.retention_archives FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_rule_guard();

CREATE TRIGGER pdd_retention_immutable BEFORE UPDATE OR DELETE ON public.dataset_publications FOR EACH ROW EXECUTE FUNCTION public.pdd_v2_rule_guard();

CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.retention_archives FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();



CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.dataset_storage FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();



CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.dataset_publications FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();



CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.retention_operations FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();



CREATE TRIGGER pdd_retention_truncate BEFORE TRUNCATE ON public.retention_operation_items FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();



UPDATE public.alembic_version_v2 SET version_num='20261110_13' WHERE public.alembic_version_v2.version_num = '20261104_12';

-- Running upgrade 20261110_13 -> 20261110_14

CREATE TABLE public.fact_content_blocks(
      id text PRIMARY KEY CHECK(id ~ '^[0-9a-f]{64}$'),
      table_name text NOT NULL CHECK(table_name IN ('product_daily_facts','product_promotion_records','product_region_records','product_sales_records','return_records')),
      dataset_code text NOT NULL CHECK(btrim(dataset_code)<>''),
      row_schema_revision text NOT NULL CHECK(btrim(row_schema_revision)<>''),
      bucket text NOT NULL CHECK(length(bucket) BETWEEN 1 AND 32),
      row_count bigint NOT NULL CHECK(row_count>0),
      state text NOT NULL DEFAULT 'building' CHECK(state IN ('building','sealed')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE public.fact_content_manifests(
      version_id text PRIMARY KEY REFERENCES public.dataset_versions(id) ON DELETE RESTRICT,
      table_name text NOT NULL CHECK(table_name IN ('product_daily_facts','product_promotion_records','product_region_records','product_sales_records','return_records')),
      format_revision integer NOT NULL DEFAULT 1 CHECK(format_revision=1),
      row_count bigint NOT NULL CHECK(row_count>=0));

CREATE TABLE public.fact_content_members(
      version_id text NOT NULL REFERENCES public.fact_content_manifests(version_id)
        ON DELETE RESTRICT,
      bucket text NOT NULL CHECK(length(bucket) BETWEEN 1 AND 32),
      block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT,
      PRIMARY KEY(version_id,bucket));

CREATE INDEX ix_fact_members_block ON public.fact_content_members(block_id);

CREATE TABLE public.shared_product_daily_facts(LIKE public.product_daily_facts INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE public.shared_product_daily_facts DROP COLUMN id, DROP COLUMN dataset_version_id;

ALTER TABLE public.shared_product_daily_facts ADD COLUMN block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, ADD PRIMARY KEY(block_id,source_row_key);

ALTER TABLE public.shared_product_daily_facts ADD FOREIGN KEY(listing_id) REFERENCES public.listings(id) ON DELETE RESTRICT;

CREATE VIEW public.read_product_daily_facts AS SELECT * FROM public.product_daily_facts UNION ALL SELECT 'shared:' || encode(convert_to(jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id,m.version_id AS dataset_version_id,f.source_row_key,f.source_record_id,f.listing_id,f.listing_match_status,f.source_shop_id,f.source_shop_name,f.source_product_id,f.source_product_name,f.source_dt_raw,f.business_date,f.date_status,f.source_related_date_raw,f.views,f.visitors,f.favorites,f.buyers,f.orders,f.units,f.product_revenue,f.source_conversion_rate,f.source_first_category,f.source_second_category,f.source_third_category,f.source_created_time_raw,f.source_created_at FROM public.fact_content_members m JOIN public.shared_product_daily_facts f ON f.block_id=m.block_id;

CREATE TABLE public.shared_product_promotion_records(LIKE public.product_promotion_records INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE public.shared_product_promotion_records DROP COLUMN id, DROP COLUMN dataset_version_id;

ALTER TABLE public.shared_product_promotion_records ADD COLUMN block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, ADD PRIMARY KEY(block_id,source_row_key);

ALTER TABLE public.shared_product_promotion_records ADD FOREIGN KEY(listing_id) REFERENCES public.listings(id) ON DELETE RESTRICT;

CREATE VIEW public.read_product_promotion_records AS SELECT * FROM public.product_promotion_records UNION ALL SELECT 'shared:' || encode(convert_to(jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id,m.version_id AS dataset_version_id,f.source_row_key,f.source_record_id,f.listing_id,f.listing_match_status,f.source_shop_id,f.source_shop_name,f.source_product_id,f.source_product_name,f.source_dt_raw,f.source_start_dt_raw,f.source_granularity,f.period_start,f.period_end,f.date_status,f.source_grouping,f.promotion_name,f.promotion_type,f.bidding_method,f.source_deleted_label,f.spend,f.transaction_spend,f.attributed_revenue,f.attributed_deals,f.net_attributed_revenue,f.net_attributed_deals,f.settled_attributed_revenue,f.settled_attributed_deals,f.impressions,f.clicks,f.source_roi,f.source_net_roi,f.source_settlement_roi,f.source_ctr,f.source_cvr,f.source_created_time_raw,f.source_created_at FROM public.fact_content_members m JOIN public.shared_product_promotion_records f ON f.block_id=m.block_id;

CREATE TABLE public.shared_product_region_records(LIKE public.product_region_records INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE public.shared_product_region_records DROP COLUMN id, DROP COLUMN dataset_version_id;

ALTER TABLE public.shared_product_region_records ADD COLUMN block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, ADD PRIMARY KEY(block_id,source_row_key);

ALTER TABLE public.shared_product_region_records ADD FOREIGN KEY(shop_id) REFERENCES public.shops(id) ON DELETE RESTRICT;

CREATE VIEW public.read_product_region_records AS SELECT * FROM public.product_region_records UNION ALL SELECT 'shared:' || encode(convert_to(jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id,m.version_id AS dataset_version_id,f.source_row_key,f.source_record_id,f.shop_id,f.shop_match_status,f.source_shop_id,f.source_shop_name,f.source_style_code,f.source_product_code,f.source_product_name,f.source_color_specification,f.source_product_category,f.source_virtual_category,f.source_region_name,f.source_dt_raw,f.source_granularity,f.period_start,f.period_end,f.date_status,f.actual_shipped_qty,f.actual_shipped_amount,f.actual_returned_qty,f.actual_returned_amount,f.source_refund_rate,f.source_updated_time_raw,f.source_updated_at FROM public.fact_content_members m JOIN public.shared_product_region_records f ON f.block_id=m.block_id;

CREATE TABLE public.shared_product_sales_records(LIKE public.product_sales_records INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE public.shared_product_sales_records DROP COLUMN id, DROP COLUMN dataset_version_id;

ALTER TABLE public.shared_product_sales_records ADD COLUMN block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, ADD PRIMARY KEY(block_id,source_row_key);

ALTER TABLE public.shared_product_sales_records ADD FOREIGN KEY(shop_id) REFERENCES public.shops(id) ON DELETE RESTRICT;

CREATE VIEW public.read_product_sales_records AS SELECT * FROM public.product_sales_records UNION ALL SELECT 'shared:' || encode(convert_to(jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id,m.version_id AS dataset_version_id,f.source_row_key,f.source_record_id,f.row_kind,f.source_row_kind,f.shop_id,f.shop_match_status,f.source_shop_id,f.source_shop_name,f.source_style_code,f.source_product_code,f.source_product_name,f.source_color_specification,f.source_product_category,f.source_subcategory,f.source_virtual_category,f.source_period_label,f.period_start,f.period_end,f.date_status,f.sales_amount,f.source_sales_qty FROM public.fact_content_members m JOIN public.shared_product_sales_records f ON f.block_id=m.block_id;

CREATE TABLE public.shared_return_records(LIKE public.return_records INCLUDING DEFAULTS INCLUDING CONSTRAINTS);

ALTER TABLE public.shared_return_records DROP COLUMN id, DROP COLUMN dataset_version_id;

ALTER TABLE public.shared_return_records ADD COLUMN block_id text NOT NULL REFERENCES public.fact_content_blocks(id) ON DELETE RESTRICT, ADD PRIMARY KEY(block_id,source_row_key);

ALTER TABLE public.shared_return_records ADD FOREIGN KEY(shop_id) REFERENCES public.shops(id) ON DELETE RESTRICT;

CREATE VIEW public.read_return_records AS SELECT * FROM public.return_records UNION ALL SELECT 'shared:' || encode(convert_to(jsonb_build_array(m.version_id,f.source_row_key)::text,'UTF8'),'hex') AS id,m.version_id AS dataset_version_id,f.source_row_key,f.source_record_id,f.row_kind,f.source_row_kind,f.shop_id,f.shop_match_status,f.source_shop_id,f.source_shop_name,f.source_style_code,f.source_product_code,f.source_product_name,f.source_color_specification,f.source_product_category,f.source_subcategory,f.source_virtual_category,f.source_return_reason,f.source_analysis_fields,f.source_aftersale_type,f.source_aftersale_status,f.source_period_label,f.period_start,f.period_end,f.date_status,f.actual_returned_qty,f.actual_inbound_amount FROM public.fact_content_members m JOIN public.shared_return_records f ON f.block_id=m.block_id;

CREATE FUNCTION public.pdd_content_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
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
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_content_publish() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
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
 ELSE
   PERFORM 1 FROM public.dataset_versions WHERE id=NEW.dataset_version_id FOR UPDATE;
   IF EXISTS(SELECT 1 FROM public.fact_content_manifests
      WHERE version_id=NEW.dataset_version_id) THEN
     RAISE EXCEPTION 'CONTENT_MIXED_FORMAT'; END IF;
 END IF;
 RETURN NEW;
END;
$pdd_v2$;

CREATE FUNCTION public.pdd_content_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
 IF EXISTS(SELECT 1 FROM public.fact_content_blocks WHERE id=NEW.id AND state<>'sealed') THEN
   RAISE EXCEPTION 'CONTENT_BLOCK_INCOMPLETE'; END IF;
 RETURN NULL;
END;
$pdd_v2$;

CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.fact_content_blocks FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.fact_content_blocks FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.fact_content_manifests FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.fact_content_manifests FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.fact_content_members FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.fact_content_members FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.shared_product_daily_facts FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.shared_product_daily_facts FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.shared_product_promotion_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.shared_product_promotion_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.shared_product_region_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.shared_product_region_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.shared_product_sales_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.shared_product_sales_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();




CREATE TRIGGER pdd_content_guard BEFORE INSERT OR UPDATE OR DELETE ON public.shared_return_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_guard();

CREATE TRIGGER pdd_content_no_truncate BEFORE TRUNCATE ON public.shared_return_records FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate();





CREATE CONSTRAINT TRIGGER pdd_content_complete AFTER INSERT ON public.fact_content_blocks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.pdd_content_complete();

CREATE TRIGGER pdd_content_publish BEFORE UPDATE ON public.dataset_versions FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();

CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.product_daily_facts FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();



CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.product_promotion_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();



CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.product_region_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();



CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.product_sales_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();



CREATE TRIGGER pdd_content_format BEFORE INSERT ON public.return_records FOR EACH ROW EXECUTE FUNCTION public.pdd_content_publish();



CREATE OR REPLACE FUNCTION public.pdd_retention_context(version_key text, table_key text, action text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
BEGIN
 RETURN EXISTS(SELECT 1 FROM public.retention_operations o
 JOIN public.retention_operation_items i ON i.operation_id=o.id
 JOIN public.retention_archives a ON a.id=o.archive_id
 WHERE o.id=current_setting('pdd.retention_operation',true)
 AND o.transaction_id=txid_current() AND o.status='running' AND o.kind=action
 AND i.version_id=version_key AND i.table_name=table_key
 AND a.database_name=current_database() AND a.schema_head='20261110_14');
END;
$pdd_v2$;

CREATE OR REPLACE FUNCTION public.pdd_retention_item_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public
AS $pdd_v2$
DECLARE action text; archive text; ver_code text; expected_table text; exit_at timestamptz;
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'RETENTION_AUDIT_IMMUTABLE'; END IF;
      IF NOT pg_try_advisory_xact_lock(hashtext('pdd-database-maintenance')) THEN
        RAISE EXCEPTION 'RETENTION_BUSY'; END IF;
      IF current_database() !~ '^pdd_operations_v2_test_[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'RETENTION_PRODUCTION_DISABLED'; END IF;
      SELECT o.kind,o.archive_id INTO action,archive FROM public.retention_operations o
      JOIN public.retention_archives a ON a.id=o.archive_id
      WHERE o.id=NEW.operation_id AND o.transaction_id=txid_current() AND o.status='running'
        AND a.database_name=current_database() AND a.schema_head='20261110_14'
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
        IF EXISTS(SELECT 1 FROM (
WITH RECURSIVE roots(id) AS (
 SELECT id FROM public.dataset_versions
 WHERE dataset_code NOT IN ('product_daily','product_promotion','product_regions')
    OR state <> 'published'
 UNION SELECT version_id FROM public.fact_content_manifests UNION SELECT version_id FROM public.current_datasets
 UNION SELECT s.version_id FROM public.dataset_storage s WHERE keep_online
 UNION SELECT p.previous_version_id FROM public.dataset_publications p
 JOIN public.current_datasets c ON c.dataset_code=p.dataset_code AND c.revision=p.revision
 WHERE p.previous_version_id IS NOT NULL
), protected(id) AS (
 SELECT id FROM roots UNION
 SELECT i.input_version_id FROM public.version_inputs i JOIN protected p ON p.id=i.output_version_id
) SELECT id FROM protected
) p WHERE p.id=NEW.version_id)
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
$pdd_v2$;

UPDATE public.alembic_version_v2 SET version_num='20261110_14' WHERE public.alembic_version_v2.version_num = '20261110_13';


COMMIT;
