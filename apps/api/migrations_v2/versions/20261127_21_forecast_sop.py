"""单篇预测 SOP 的后台修订、只读生效入口及同月真实运行快照。"""

from alembic import op

revision = "20261127_21"
down_revision = "20261118_20"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE public.forecast_sop_source (
      document_id text PRIMARY KEY CHECK(document_id ~ '^[A-Za-z0-9]{20,64}$'
        AND (document_id='H3svdLyEYoxsK3xgeBOceOxfnMc'
          OR current_database() ~ '^pdd_operations_v2_test_[0-9a-f]{12}$')),
      source_url text NOT NULL CHECK(source_url='https://kocotree.feishu.cn/docx/'||document_id),
      revision bigint CHECK(revision>0),
      version_revision bigint,
      state text NOT NULL CHECK(state IN ('pending','ready','stale','unavailable')),
      reason text CHECK(reason IN ('updating','forbidden','not_found','deleted','temporary',
        'revision_changed','revision_regressed','compiler_failed','unsupported_rule')),
      checked_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      verified_at timestamptz,
      CHECK(version_revision IS NULL OR (revision IS NOT NULL AND version_revision=revision)),
      CHECK((state IN ('ready','stale'))=(version_revision IS NOT NULL)),
      CHECK(version_revision IS NULL OR verified_at IS NOT NULL))""")
    op.execute(
        "CREATE UNIQUE INDEX uq_forecast_single_source ON public.forecast_sop_source((true))"
    )
    op.execute("""CREATE TABLE public.forecast_sop_versions (
      document_id text NOT NULL
        REFERENCES public.forecast_sop_source(document_id) ON DELETE RESTRICT,
      revision bigint NOT NULL CHECK(revision>0),
      content text NOT NULL,
      content_sha256 text NOT NULL
        CHECK(content_sha256=encode(sha256(convert_to(content,'UTF8')),'hex')),
      rule jsonb CHECK(jsonb_typeof(rule)='object'),
      synced_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY(document_id,revision))""")
    op.execute("""ALTER TABLE public.forecast_sop_source
      ADD FOREIGN KEY(document_id,version_revision)
      REFERENCES public.forecast_sop_versions(document_id,revision) ON DELETE RESTRICT""")
    op.execute("""CREATE TABLE public.forecast_monthly_snapshots (
      month date NOT NULL CHECK(extract(day FROM month)=1),
      input_version text NOT NULL CHECK(btrim(input_version)<>''),
      cutoff date NOT NULL CHECK(date_trunc('month',cutoff)::date=month),
      document_id text NOT NULL,
      rule_revision bigint NOT NULL,
      previous_input_version text CHECK(previous_input_version<>input_version),
      payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'
        AND payload ? 'cutoff' AND jsonb_typeof(payload->'cutoff')='string'
        AND (payload->>'cutoff')::date=cutoff
        AND payload ? 'rule_revision' AND jsonb_typeof(payload->'rule_revision')='number'
        AND (payload->>'rule_revision')::bigint=rule_revision),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(month,input_version),
      FOREIGN KEY(document_id,rule_revision)
        REFERENCES public.forecast_sop_versions(document_id,revision) ON DELETE RESTRICT,
      FOREIGN KEY(month,previous_input_version)
        REFERENCES public.forecast_monthly_snapshots(month,input_version) ON DELETE RESTRICT)""")
    op.execute("""CREATE INDEX ix_forecast_monthly_previous
      ON public.forecast_monthly_snapshots(month,cutoff DESC,created_at DESC,input_version)""")
    op.execute("""CREATE VIEW public.read_forecast_sop WITH (security_barrier=true) AS
      WITH current_source AS (
        SELECT s.document_id,s.source_url,s.revision,s.reason,s.checked_at,s.verified_at,
          v.content,v.rule,v.synced_at,
          CASE WHEN s.version_revision IS NULL THEN s.state
               WHEN s.verified_at+interval '24 hours'<=statement_timestamp() THEN 'expired'
               WHEN v.rule IS NULL THEN 'unsupported'
               WHEN s.state='ready' THEN 'active' ELSE s.state END AS state
        FROM public.forecast_sop_source s
        LEFT JOIN public.forecast_sop_versions v
          ON (s.document_id,s.version_revision)=(v.document_id,v.revision))
      SELECT document_id,source_url,revision,state,
        CASE WHEN state='expired' THEN 'verification_expired'
             WHEN state='unsupported' THEN 'unsupported_rule' ELSE reason END AS reason,
        CASE WHEN state IN ('active','stale','unsupported') THEN content END AS content,
        CASE WHEN state IN ('active','stale') THEN rule END AS rule,checked_at,verified_at,synced_at
      FROM current_source""")
    op.execute("GRANT SELECT ON public.read_forecast_sop TO pdd_app")
    op.execute("GRANT SELECT ON public.read_forecast_sop TO pdd_ingest")
    op.execute("GRANT SELECT ON public.forecast_monthly_snapshots TO pdd_app")
    op.execute("GRANT SELECT,INSERT ON public.forecast_monthly_snapshots TO pdd_ingest")
    op.execute("GRANT SELECT,INSERT ON public.forecast_sop_versions TO pdd_ingest")
    op.execute("GRANT SELECT,INSERT ON public.forecast_sop_source TO pdd_ingest")
    op.execute("""GRANT UPDATE(revision,version_revision,state,reason,checked_at,verified_at)
      ON public.forecast_sop_source TO pdd_ingest""")
    comments = {
        "forecast_sop_source": (
            "单篇预测 SOP 的检查状态与已核对修订",
            {
                "document_id": "固定飞书文档标识；隔离验收可指定测试副本",
                "source_url": "飞书原文地址",
                "revision": "最近已知最高飞书修订号",
                "version_revision": "当前可供读取的同一修订",
                "state": "本地检查和发布状态",
                "reason": "最近状态的固定原因码",
                "checked_at": "最近尝试检查的时刻",
                "verified_at": "最近成功核对所用修订的时刻",
            },
        ),
        "forecast_sop_versions": (
            "按飞书修订冻结的预测原文与同版规则",
            {
                "document_id": "所属飞书文档标识",
                "revision": "原文修订号",
                "content": "完整原文 Markdown 含原生表格",
                "content_sha256": "UTF-8 原文摘要",
                "rule": "已核对原文的受控计算模板及参数；空表示该修订暂不可执行",
                "synced_at": "本地首次接收此修订的时刻",
            },
        ),
        "forecast_monthly_snapshots": (
            "同月真实预测运行及其前次比较版本",
            {
                "month": "预测月份的第一日",
                "input_version": "固定经营输入、范围和规则的运行指纹",
                "cutoff": "本次预测经营截止日",
                "document_id": "计算所依据的 SOP 文档标识",
                "rule_revision": "本次计算的 SOP 修订号",
                "payload": "本次预测输出及聚合依据",
                "previous_input_version": "首次记录时相容的同月前次运行指纹，可空",
                "created_at": "本次输入首次记录的时刻",
            },
        ),
    }
    for table, (description, columns) in comments.items():
        op.execute(f"COMMENT ON TABLE public.{table} IS '{description}'")
        for column, description in columns.items():
            op.execute(f"COMMENT ON COLUMN public.{table}.{column} IS '{description}'")
    op.execute("COMMENT ON VIEW public.read_forecast_sop IS '当前可引用的预测 SOP、同版规则和状态'")
    view_comments = {
        **comments["forecast_sop_source"][1],
        **comments["forecast_sop_versions"][1],
        "state": "供问答使用的当前有效、暂时未核、不可执行、过期、待同步或已撤下状态",
    }
    for column in (
        "document_id",
        "source_url",
        "revision",
        "state",
        "reason",
        "content",
        "rule",
        "checked_at",
        "verified_at",
        "synced_at",
    ):
        op.execute(
            f"COMMENT ON COLUMN public.read_forecast_sop.{column} IS '{view_comments[column]}'"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
      LOCK TABLE public.forecast_sop_source,public.forecast_sop_versions,
        public.forecast_monthly_snapshots IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT FROM public.forecast_sop_source)
         OR EXISTS(SELECT FROM public.forecast_sop_versions)
         OR EXISTS(SELECT FROM public.forecast_monthly_snapshots) THEN
        RAISE EXCEPTION 'FORECAST_SOP_DATA_PRESENT'; END IF;
    END $$""")
    op.execute("DROP VIEW public.read_forecast_sop")
    op.execute("DROP TABLE public.forecast_monthly_snapshots")
    op.execute("""ALTER TABLE public.forecast_sop_source
      DROP CONSTRAINT forecast_sop_source_document_id_version_revision_fkey""")
    op.execute("DROP TABLE public.forecast_sop_versions")
    op.execute("DROP TABLE public.forecast_sop_source")
