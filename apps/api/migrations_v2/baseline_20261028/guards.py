"""新基线保护触发器的离线SQL生成器；本模块不创建引擎或执行SQL。

这些保护只兜底身份、事件时间、版本状态/封版不可变和当前入口相容性。
它们不实现业务发布器：规则JSON完整schema、来源权限/摘要/两位原值、集合
完整性、十项评分与标准相容、输入角色窗口/无环、报告比较及默认排序仍须
后续不可绕过的发布过程校验。任务租约、角色授权也不由本模块代替。
函数使用调用者权限；迁移/表所有者仍能禁用触发器，须由部署权限隔离防止。
"""

import sqlalchemy as sa

from .helpers import name

DIRECT_CONTENT = (
    "profile_source_records",
    "profile_listing_links",
    "product_daily_facts",
    "shop_daily_facts",
    "product_promotion_records",
    "shop_channel_promotion_daily",
    "shop_rating_daily",
    "shop_refund_records",
    "shop_region_daily",
    "product_region_records",
    "product_sales_records",
    "return_records",
    "sales_targets",
    "refund_targets",
    "score_standards",
    "product_scores",
    "aftersales_reports",
    "coverage_records",
)
CHILD_CONTENT = (
    ("sales_target_shops", "sales_targets", "target_id"),
    ("refund_target_categories", "refund_targets", "target_id"),
    ("refund_target_styles", "refund_targets", "target_id"),
    ("score_standard_items", "score_standards", "standard_id"),
    ("product_score_items", "product_scores", "score_id"),
)
FUNCTION_NAMES = (
    "pdd_v2_identity_guard",
    "pdd_v2_version_guard",
    "pdd_v2_assert_draft_version",
    "pdd_v2_content_guard",
    "pdd_v2_child_content_guard",
    "pdd_v2_inputs_guard",
    "pdd_v2_rule_guard",
    "pdd_v2_current_guard",
    "pdd_v2_forbid_truncate",
)


def _function(
    function_name: str, body: str, *, arguments: str = "", returns: str = "trigger"
) -> str:
    """只包装冻结的内部SQL正文，不接受来源文本或用户参数。"""
    return (
        f"CREATE FUNCTION public.{function_name}({arguments}) RETURNS {returns}\n"
        "LANGUAGE plpgsql SECURITY INVOKER\n"
        "SET search_path = pg_catalog, public\nAS $pdd_v2$\n" + body.strip() + "\n$pdd_v2$"
    )


def _trigger_specs() -> list[tuple[str, str, str, tuple[str, ...], str]]:
    """行级保护和TRUNCATE保护共用冻结清单；不依赖当前数据库内容。"""
    result = [
        ("shops", "identity", "pdd_v2_identity_guard", (), "INSERT OR UPDATE OR DELETE"),
        ("listings", "identity", "pdd_v2_identity_guard", (), "INSERT OR UPDATE OR DELETE"),
        ("dataset_versions", "version", "pdd_v2_version_guard", (), "INSERT OR UPDATE OR DELETE"),
        ("rule_sets", "rule", "pdd_v2_rule_guard", (), "UPDATE OR DELETE"),
        ("current_datasets", "current", "pdd_v2_current_guard", (), "INSERT OR UPDATE OR DELETE"),
        ("version_inputs", "inputs", "pdd_v2_inputs_guard", (), "INSERT OR UPDATE OR DELETE"),
    ]
    result.extend(
        (table_name, "content", "pdd_v2_content_guard", (), "INSERT OR UPDATE OR DELETE")
        for table_name in DIRECT_CONTENT
    )
    result.extend(
        (
            table_name,
            "child",
            "pdd_v2_child_content_guard",
            (parent, key),
            "INSERT OR UPDATE OR DELETE",
        )
        for table_name, parent, key in CHILD_CONTENT
    )
    return result


def upgrade_statements(metadata: sa.MetaData) -> list[str]:
    """校核冻结列后返回函数/触发器DDL；调用者另行决定是否获准执行。"""
    expected_direct = {
        value.name
        for value in metadata.tables.values()
        if value.schema == "public" and "dataset_version_id" in value.c
    }
    if expected_direct != set(DIRECT_CONTENT):
        raise ValueError("版本内容表与冻结保护清单不一致，须重新审查迁移")
    for table_name, _, _, _, _ in _trigger_specs():
        if f"public.{table_name}" not in metadata.tables:
            raise ValueError(f"保护表不存在：{table_name}")
    for child, parent, key in CHILD_CONTENT:
        if key not in metadata.tables[f"public.{child}"].c:
            raise ValueError(f"成员关联列不存在：{child}.{key}")
        if "dataset_version_id" not in metadata.tables[f"public.{parent}"].c:
            raise ValueError(f"父版本列不存在：{parent}")

    statements = [
        _function(
            "pdd_v2_identity_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_version_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_assert_draft_version",
            """
DECLARE
    version_state text;
BEGIN
    SELECT state INTO version_state FROM public.dataset_versions
        WHERE id = version_key FOR SHARE;
    IF NOT FOUND OR version_state IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION '内容仅可写入存在的draft版本' USING ERRCODE = '23514';
    END IF;
END;
""",
            arguments="version_key text",
            returns="void",
        ),
        _function(
            "pdd_v2_content_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_child_content_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_inputs_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_rule_guard",
            """
BEGIN
    RAISE EXCEPTION '规则包只可追加修订，不可原地更新或删除' USING ERRCODE = '23514';
END;
""",
        ),
        _function(
            "pdd_v2_current_guard",
            """
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
""",
        ),
        _function(
            "pdd_v2_forbid_truncate",
            """
BEGIN
    RAISE EXCEPTION '受保护表不允许TRUNCATE，维护须另行评审'
        USING ERRCODE = '23514';
END;
""",
        ),
    ]
    for table_name, suffix, function_name, arguments, events in _trigger_specs():
        trigger_name = name("pdd_v2_trg", table_name, suffix)
        sql_arguments = ", ".join("'" + value + "'" for value in arguments)
        statements.append(
            f"CREATE TRIGGER {trigger_name} BEFORE {events} ON public.{table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}({sql_arguments})"
        )
    # 行级触发器不覆盖TRUNCATE，额外封住此旁路；不影响逆序DROP TABLE回退。
    for table_name in sorted({spec[0] for spec in _trigger_specs()}):
        trigger_name = name("pdd_v2_trg", table_name, "truncate")
        statements.append(
            f"CREATE TRIGGER {trigger_name} BEFORE TRUNCATE ON public.{table_name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.pdd_v2_forbid_truncate()"
        )
    return statements


def downgrade_statements() -> list[str]:
    """供调用者先逆序DROP TABLE后使用；表上的触发器已随表移除，再移除函数。

    调用者必须先完成全表锁定及空结构检查；本列表不能用于仍保留表的环境。
    不用CASCADE删依赖，不自动删任何业务行。
    """
    result = []
    for function_name in reversed(FUNCTION_NAMES):
        arguments = "text" if function_name == "pdd_v2_assert_draft_version" else ""
        result.append(f"DROP FUNCTION public.{function_name}({arguments})")
    return result
