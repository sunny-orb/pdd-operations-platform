"""公司数据库目录契约，供只读核验、增量升级和备份复用。"""

import hashlib
import json

START_REVISION = "20261104_12"
TARGET_REVISION = "20261114_18"
UPGRADE_STARTS = (START_REVISION, "20261111_16", "20261113_17")
RPA_TABLE = "aftersales_reports_rpa"
RPA_INDEX = "aftersale_reports_rpa_pkey"
RPA_SEQUENCE = "aftersale_reports_rpa_id_seq"
RPA_RELATIONS = [RPA_TABLE, RPA_INDEX, RPA_SEQUENCE]
COMPANY_EXTENSIONS = {
    "btree_gin": "public",
    "btree_gist": "public",
    "file_fdw": "public",
    "intagg": "public",
    "intarray": "public",
    "pageinspect": "monitor",
    "pg_buffercache": "monitor",
    "pg_freespacemap": "monitor",
    "pg_prewarm": "monitor",
    "pg_repack": "public",
    "pg_stat_statements": "monitor",
    "pg_trgm": "public",
    "pg_visibility": "monitor",
    "pgstattuple": "monitor",
    "plpgsql": "pg_catalog",
    "postgres_fdw": "public",
}
RPA_COLUMNS = [
    ("id", "bigint"),
    ("start_date", "date"),
    ("time_granularity", "text"),
    ("json_data", "jsonb"),
    ("updated_at", "timestamp with time zone"),
    ("class", "text"),
    ("end_date", "date"),
]

# 冻结的PG17目录指纹由隔离库迁移12、15、16、17及18后取得。按对象类别分开，错误不打印目录内容。
SCHEMA_SIGNATURES = {
    START_REVISION: {
        "columns": "d1ed987fceefdba4589b86d6f7f0fa1c5de19bb6406e1db3da9454d8585b75aa",
        "constraints": "187e6484c42f921dbddd6d922a42dcb92aca4f71b2f24701edd60e0bbfb6ec7c",
        "functions": "93a808c295e5d0046630dc35812e27bb702c8ab9bc710fc5082f85b117768626",
        "relations": "962cd30f49714ab400c3f5de59df9ef6680131f1d339cc121ba8fe415386d2fe",
        "triggers": "48605dbb40fbd77560fd0e2371ba68cca209b6eb6f28742da585db781dd995a5",
        "internal_triggers": "b8ae19a179d2263bdb1375105520182052351743d162d265149afc64a3cab329",
        "types": "c832100a057573225d9780f9c545ab71ab7173eb6a73ed69c6f1633430305f23",
    },
    "20261111_15": {
        "columns": "ab6725b4a7ab55e52f1d12755091dd76c4c4de6eeaa0210e9383323845e3d06b",
        "constraints": "1bfa5752f4dd2aecf5aa54a4e413f761d41dcbd82b00643247f3ee143204b672",
        "functions": "74b2caf2edc3f61981953dabb772c1adff6dcadd3ce43be04cff341669d2e0d6",
        "relations": "60eb0910ee8ca56fa420a93a736018ef56285981554a60cbe7a26119671c8b8a",
        "triggers": "d4badfa5457ad262ecb75d5785da8c0e97c81030d03728c62ef22abcec38d57d",
        "internal_triggers": "0b9c2b9f56802b45e2661b4030489ff8170223e4d6b316b9f66c16e3a5a1fc95",
        "types": "0560d24afec221311356e168b60600dd707906cb76ce88fe1da6c699de43256e",
    },
    "20261111_16": {
        "relations": "e64fbae3c6609e5893b28413b77ac38acbe4d16e80b73d9a877d022227f3ea23",
        "columns": "ab6725b4a7ab55e52f1d12755091dd76c4c4de6eeaa0210e9383323845e3d06b",
        "constraints": "1bfa5752f4dd2aecf5aa54a4e413f761d41dcbd82b00643247f3ee143204b672",
        "triggers": "d4badfa5457ad262ecb75d5785da8c0e97c81030d03728c62ef22abcec38d57d",
        "internal_triggers": "0b9c2b9f56802b45e2661b4030489ff8170223e4d6b316b9f66c16e3a5a1fc95",
        "functions": "111bde5d8ccc48ebcb8813f9fdf83cf7aec95780eca369cf2327a58d3c2f29b9",
        "types": "0560d24afec221311356e168b60600dd707906cb76ce88fe1da6c699de43256e",
    },
    "20261113_17": {
        "relations": "e64fbae3c6609e5893b28413b77ac38acbe4d16e80b73d9a877d022227f3ea23",
        "columns": "ab6725b4a7ab55e52f1d12755091dd76c4c4de6eeaa0210e9383323845e3d06b",
        "constraints": "6c0e2392ad7b8203d41eea33391637c4f1ff853f90fc141609b72f5c0f267714",
        "triggers": "d4badfa5457ad262ecb75d5785da8c0e97c81030d03728c62ef22abcec38d57d",
        "internal_triggers": "0b9c2b9f56802b45e2661b4030489ff8170223e4d6b316b9f66c16e3a5a1fc95",
        "functions": "d7431d3f56c7af0bc39076256d90fbfa3abcf1de0787c1c5c8d72dd38e6d01b7",
        "types": "0560d24afec221311356e168b60600dd707906cb76ce88fe1da6c699de43256e",
    },
    TARGET_REVISION: {
        "relations": "e64fbae3c6609e5893b28413b77ac38acbe4d16e80b73d9a877d022227f3ea23",
        "columns": "ab6725b4a7ab55e52f1d12755091dd76c4c4de6eeaa0210e9383323845e3d06b",
        "constraints": "6c0e2392ad7b8203d41eea33391637c4f1ff853f90fc141609b72f5c0f267714",
        "triggers": "d4badfa5457ad262ecb75d5785da8c0e97c81030d03728c62ef22abcec38d57d",
        "internal_triggers": "0b9c2b9f56802b45e2661b4030489ff8170223e4d6b316b9f66c16e3a5a1fc95",
        "functions": "ddee5f36ba4ccc640bce25618640adbf37af74490835b186bf047598a57a0870",
        "types": "0560d24afec221311356e168b60600dd707906cb76ce88fe1da6c699de43256e",
    },
}

# 只沿扩展成员(e)及不可独立存在的内部实现(i)识别归属。
# 普通/自动依赖(n/a/x)并不代表扩展成员，不能据此放行业务对象。
EXTENSION_MEMBERS = """WITH RECURSIVE extension_members(classid,objid,objsubid) AS (
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
"""


def rpa_manifest(connection, *, owner="dbrole_admin"):
    """返回已确认RPA来源的结构和权限；不存在时返回None，拒绝同名漂移。"""
    relations = connection.execute(
        """SELECT c.relname, c.oid, c.relkind, pg_get_userbyid(c.relowner),
           c.relpersistence, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text,
           obj_description(c.oid, 'pg_class')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname""",
        (RPA_RELATIONS,),
    ).fetchall()
    if not relations:
        return None
    objects = {row[0]: row for row in relations}
    expected = {RPA_TABLE: "r", RPA_INDEX: "i", RPA_SEQUENCE: "S"}
    if (
        set(objects) != set(expected)
        or any(
            row[2:7] != (expected[name], owner, "p", False, False) for name, row in objects.items()
        )
        or objects[RPA_TABLE][-1] != "RPA 抓取每周月售后报表"
    ):
        raise ValueError("DEPLOY_RPA_MISMATCH")
    table = objects[RPA_TABLE][1]
    seq = objects[RPA_SEQUENCE][1]
    index = objects[RPA_INDEX][1]
    columns = connection.execute(
        """SELECT a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull,
           a.attidentity, a.attgenerated, pg_get_expr(d.adbin,d.adrelid), a.attacl::text
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum)
        WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
        (table,),
    ).fetchall()
    constraints = connection.execute(
        """SELECT conname,contype,conindid,conkey,convalidated,condeferrable,condeferred
        FROM pg_constraint WHERE conrelid=%s OR confrelid=%s ORDER BY conname""",
        (table, table),
    ).fetchall()
    indexes = connection.execute(
        """SELECT i.indexrelid, am.amname, i.indkey::text, i.indisprimary, i.indisunique,
           i.indisvalid, i.indisready, i.indexprs IS NULL, i.indpred IS NULL
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
        JOIN pg_am am ON am.oid=c.relam WHERE i.indrelid=%s""",
        (table,),
    ).fetchall()
    sequence = connection.execute(
        """SELECT s.seqtypid::regtype::text,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,
           s.seqcache,s.seqcycle,d.deptype,d.refobjid,d.refobjsubid
        FROM pg_sequence s JOIN pg_depend d ON d.objid=s.seqrelid
        WHERE s.seqrelid=%s AND d.classid='pg_class'::regclass
          AND d.refclassid='pg_class'::regclass""",
        (seq,),
    ).fetchall()
    extra = connection.execute(
        """SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=%s AND NOT tgisinternal)
          OR EXISTS(SELECT 1 FROM pg_policy WHERE polrelid=%s)
          OR EXISTS(SELECT 1 FROM pg_inherits WHERE inhrelid=%s OR inhparent=%s)
          OR EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=%s)""",
        (table, table, table, table, table),
    ).fetchone()[0]
    if (
        [row[:6] for row in columns]
        != [
            (name, data_type, True, "a" if name == "id" else "", "", None)
            for name, data_type in RPA_COLUMNS
        ]
        or constraints != [(RPA_INDEX, "p", index, [1], True, False, False)]
        or indexes != [(index, "btree", "1", True, True, True, True, True, True)]
        or sequence != [("bigint", 1, 1, 9223372036854775807, 1, 1, False, "i", table, 1)]
        or extra
    ):
        raise ValueError("DEPLOY_RPA_MISMATCH")
    return {
        "relations": relations,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "sequence": sequence,
    }


APP_OBJECTS = (
    EXTENSION_MEMBERS
    + """, application_objects AS (
  SELECT 'pg_class'::regclass AS classid,oid,relnamespace AS namespace FROM pg_class
  UNION ALL SELECT 'pg_proc'::regclass,oid,pronamespace FROM pg_proc
  UNION ALL SELECT 'pg_type'::regclass,oid,typnamespace FROM pg_type
  UNION ALL SELECT 'pg_operator'::regclass,oid,oprnamespace FROM pg_operator
  UNION ALL SELECT 'pg_opclass'::regclass,oid,opcnamespace FROM pg_opclass
  UNION ALL SELECT 'pg_opfamily'::regclass,oid,opfnamespace FROM pg_opfamily
  UNION ALL SELECT 'pg_collation'::regclass,oid,collnamespace FROM pg_collation
  UNION ALL SELECT 'pg_conversion'::regclass,oid,connamespace FROM pg_conversion
  UNION ALL SELECT 'pg_ts_config'::regclass,oid,cfgnamespace FROM pg_ts_config
  UNION ALL SELECT 'pg_ts_dict'::regclass,oid,dictnamespace FROM pg_ts_dict
  UNION ALL SELECT 'pg_ts_parser'::regclass,oid,prsnamespace FROM pg_ts_parser
  UNION ALL SELECT 'pg_ts_template'::regclass,oid,tmplnamespace FROM pg_ts_template
  UNION ALL SELECT 'pg_statistic_ext'::regclass,oid,stxnamespace FROM pg_statistic_ext
), application_inventory AS (
  SELECT o.classid,o.oid,n.nspname FROM application_objects o
  JOIN pg_namespace n ON n.oid=o.namespace
  WHERE n.nspname NOT IN ('information_schema','monitor') AND n.nspname !~ '^pg_'
    AND NOT EXISTS (SELECT 1 FROM extension_members e
                    WHERE e.classid=o.classid AND e.objid=o.oid AND e.objsubid=0)
    AND NOT (n.nspname='public' AND %s AND (
      (o.classid='pg_class'::regclass AND o.oid IN
        (SELECT oid FROM pg_class WHERE relnamespace=n.oid AND relname=ANY(%s)))
      OR (o.classid='pg_type'::regclass AND o.oid IN
        (SELECT oid FROM pg_type t WHERE t.typrelid=to_regclass('public.aftersales_reports_rpa')
          OR t.typelem IN (SELECT oid FROM pg_type
                           WHERE typrelid=to_regclass('public.aftersales_reports_rpa'))))
    ))
), app_class AS (
  SELECT c.*,i.nspname FROM application_inventory i JOIN pg_class c ON c.oid=i.oid
  WHERE i.classid='pg_class'::regclass
), app_proc AS (
  SELECT p.*,i.nspname FROM application_inventory i JOIN pg_proc p ON p.oid=i.oid
  WHERE i.classid='pg_proc'::regclass
), app_type AS (
  SELECT t.*,i.nspname FROM application_inventory i JOIN pg_type t ON t.oid=i.oid
  WHERE i.classid='pg_type'::regclass
)
"""
)

# 内部触发器名带OID；用约束两端、事件与函数身份验证存在性和启用状态。
APP_SIGNATURE = (
    APP_OBJECTS
    + """
SELECT 'relations', jsonb_build_array(nspname,relname,relkind,relowner=current_user::regrole,
       relpersistence,relrowsecurity,relforcerowsecurity,
       CASE WHEN relkind IN ('i','I') THEN pg_get_indexdef(oid)
            WHEN relkind IN ('v','m') THEN pg_get_viewdef(oid,false) END)
FROM app_class
UNION ALL
SELECT 'columns', jsonb_build_array(c.nspname,c.relname,a.attnum,a.attname,
       format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
       pg_get_expr(d.adbin,d.adrelid))
FROM app_class c JOIN pg_attribute a ON a.attrelid=c.oid
LEFT JOIN pg_attrdef d ON (d.adrelid,d.adnum)=(a.attrelid,a.attnum)
WHERE c.relkind IN ('r','v','p','m','f') AND a.attnum>0 AND NOT a.attisdropped
UNION ALL
SELECT 'constraints',jsonb_build_array(c.nspname,c.relname,k.conname,k.contype,k.convalidated,
       pg_get_constraintdef(k.oid,false))
FROM app_class c JOIN pg_constraint k ON k.conrelid=c.oid
UNION ALL
SELECT 'triggers',jsonb_build_array(c.nspname,c.relname,t.tgname,t.tgenabled,
       pg_get_triggerdef(t.oid,false))
FROM app_class c JOIN pg_trigger t ON t.tgrelid=c.oid WHERE NOT t.tgisinternal
UNION ALL
SELECT 'internal_triggers',jsonb_build_array(c.nspname,c.relname,t.tgtype,t.tgenabled,
       t.tgdeferrable,t.tginitdeferred,pn.nspname,p.proname,
       pg_get_function_identity_arguments(p.oid),kn.nspname,k.conname,k.contype,
       sn.nspname,src.relname,dn.nspname,dst.relname)
FROM app_class c JOIN pg_trigger t ON t.tgrelid=c.oid
JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
LEFT JOIN pg_constraint k ON k.oid=t.tgconstraint
LEFT JOIN pg_namespace kn ON kn.oid=k.connamespace
LEFT JOIN pg_class src ON src.oid=k.conrelid LEFT JOIN pg_namespace sn ON sn.oid=src.relnamespace
LEFT JOIN pg_class dst ON dst.oid=k.confrelid LEFT JOIN pg_namespace dn ON dn.oid=dst.relnamespace
WHERE t.tgisinternal
UNION ALL
SELECT 'functions',jsonb_build_array(nspname,proname,pg_get_function_identity_arguments(oid),
       proowner=current_user::regrole,prokind,
       CASE WHEN prokind IN ('f','p','w') THEN pg_get_functiondef(oid) END)
FROM app_proc
UNION ALL
SELECT 'types',jsonb_build_array(nspname,typname,typtype,typowner=current_user::regrole)
FROM app_type
UNION ALL
SELECT 'other',jsonb_build_array(nspname,pg_describe_object(classid,oid,0))
FROM application_inventory
WHERE classid NOT IN ('pg_class'::regclass,'pg_proc'::regclass,'pg_type'::regclass)
UNION ALL
SELECT 'other',jsonb_build_array(c.nspname,c.relname,p.polname)
FROM app_class c JOIN pg_policy p ON p.polrelid=c.oid
UNION ALL
SELECT 'other',jsonb_build_array(c.nspname,c.relname,r.rulename)
FROM app_class c JOIN pg_rewrite r ON r.ev_class=c.oid
WHERE c.relkind NOT IN ('v','m') OR r.rulename <> '_RETURN'
"""
)


def application_signature(connection, *, has_rpa):
    """按对象类别生成无OID和实际角色名的目录指纹；不读取业务行。"""
    categories = {}
    for category, value in connection.execute(APP_SIGNATURE, (has_rpa, RPA_RELATIONS)):
        categories.setdefault(category, []).append(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
    return {
        category: hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()
        for category, values in categories.items()
    }


def verify_application(connection, revision, *, has_rpa):
    # 事件触发器全库生效，monitor或扩展的schema不构成DDL回调许可；本次无已审核回调。
    if connection.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_event_trigger)").fetchone()[0]:
        raise ValueError("DEPLOY_EVENT_TRIGGER_MISMATCH")
    if revision in ("20261111_16", "20261113_17", TARGET_REVISION):
        state = connection.execute(
            "SELECT indisvalid,indisready FROM pg_index WHERE indexrelid="
            "to_regclass('public.ix_return_records_summary_window')"
        ).fetchone()
        if state != (True, True):
            raise ValueError("DEPLOY_SCHEMA_MISMATCH:index_state")
    actual = application_signature(connection, has_rpa=has_rpa)
    expected = SCHEMA_SIGNATURES[revision]
    mismatches = sorted(
        category
        for category in actual.keys() | expected.keys()
        if actual.get(category) != expected.get(category)
    )
    if mismatches:
        raise ValueError("DEPLOY_SCHEMA_MISMATCH:" + ",".join(mismatches))


def application_acl(connection, *, has_rpa):
    """比较升级前已有对象的权限；新增对象沿原有默认权限。"""
    return dict(
        connection.execute(
            APP_OBJECTS
            + """
        SELECT format('relation:%%I.%%I',nspname,relname),relacl::text FROM app_class
        UNION ALL
        SELECT format('column:%%I.%%I.%%I',c.nspname,c.relname,a.attname),a.attacl::text
        FROM app_class c JOIN pg_attribute a ON a.attrelid=c.oid
        WHERE a.attacl IS NOT NULL
        UNION ALL
        SELECT format('routine:%%I.%%I(%%s)',nspname,proname,
                      pg_get_function_identity_arguments(oid)),proacl::text FROM app_proc
        UNION ALL
        SELECT format('type:%%I.%%I',nspname,typname),typacl::text FROM app_type
        """,
            (has_rpa, RPA_RELATIONS),
        ).fetchall()
    )


def verify_company_protections(connection):
    """核对本次生产库已知公司对象；扩展版本及实际ACL由事务前后快照保护。"""
    monitor = connection.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='monitor'"
    ).fetchone()
    extensions = dict(
        connection.execute("""
        SELECT e.extname,n.nspname FROM pg_extension e
        JOIN pg_namespace n ON n.oid=e.extnamespace
        """).fetchall()
    )
    if monitor != ("postgres",) or any(
        extensions.get(name) != schema for name, schema in COMPANY_EXTENSIONS.items()
    ):
        raise ValueError("DEPLOY_COMPANY_OBJECTS_MISMATCH")


def protected_catalog(connection):
    """读取数据库、monitor与登记扩展的元数据，不读取其中的业务数据。"""
    return connection.execute(
        EXTENSION_MEMBERS
        + """
        SELECT 'database', datname,to_jsonb(d)-ARRAY['datfrozenxid','datminmxid']
        FROM pg_database d WHERE datname=current_database()
        UNION ALL
        SELECT 'schema', nspname,to_jsonb(n)
        FROM pg_namespace n WHERE nspname IN ('public','monitor')
        UNION ALL
        SELECT 'extension', extname,to_jsonb(e) FROM pg_extension e
        UNION ALL
        SELECT 'extension member', pg_describe_object(classid,objid,objsubid),
               jsonb_build_array(classid,objid,objsubid,
                 to_jsonb(c)-ARRAY['relpages','reltuples','relallvisible','relallfrozen',
                                  'relfrozenxid','relminmxid','relfilenode'],
                 to_jsonb(p),to_jsonb(t))
        FROM extension_members e
        LEFT JOIN pg_class c ON e.classid='pg_class'::regclass AND c.oid=e.objid
        LEFT JOIN pg_proc p ON e.classid='pg_proc'::regclass AND p.oid=e.objid
        LEFT JOIN pg_type t ON e.classid='pg_type'::regclass AND t.oid=e.objid
        UNION ALL
        SELECT 'monitor relation', c.relname,
               to_jsonb(c)-ARRAY['relpages','reltuples','relallvisible','relallfrozen',
                                'relfrozenxid','relminmxid','relfilenode']
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='monitor'
        UNION ALL
        SELECT 'monitor routine', pg_get_function_identity_arguments(p.oid),to_jsonb(p)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='monitor'
        UNION ALL
        SELECT 'monitor type', t.typname,to_jsonb(t)
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='monitor'
        ORDER BY 1,2,3
        """
    ).fetchall()
