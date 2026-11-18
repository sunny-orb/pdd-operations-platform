"""补齐中文目录说明；结构与18相同，归档格式及维护guard继续绑定18。"""

from alembic import op

from migrations_v2.baseline_20261028.schema import build_metadata

revision = "20261116_19"
down_revision = "20261114_18"
branch_labels = depends_on = None

TABLE_COMMENTS = {
    "alembic_version_v2": "应用数据库的Alembic迁移修订登记，不是经营数据集版本",
    "dataset_storage": "数据集版本的在线或离线驻留状态及保留钉，离线内容绑定已验证归档",
    "dataset_publications": "当前数据集指针真实切换的追加事件，保留当前版与前一不同版的保护证据",
    "retention_archives": "已校验密文并完成隔离恢复演练的归档回执，不存储密钥或经营原行",
    "retention_operations": "绑定归档、计划和单一数据库事务的离线或恢复操作账本",
    "retention_operation_items": "维护事务的逐版本逐表行数及内容摘要，限定获准的三类事实表",
    "fact_content_blocks": "按事实类型、来源、行结构及日期分桶寻址的不可变事实内容块",
    "fact_content_manifests": "一个数据集版本的完整共享事实表示清单，与旧事实表示互斥",
    "fact_content_members": "数据集版本与内容块的完整成员关系，每版本每桶唯一，不形成差异链",
}

COLUMN_COMMENTS = {
    "alembic_version_v2": {
        "version_num": "当前已执行的Alembic修订号；19仅补说明，经营结构及归档格式兼容18",
    },
    "dataset_storage": {
        "version_id": "驻留状态所属数据集版本ID，引用dataset_versions，一版本一行",
        "state": "驻留状态：online在线、offline已归档离线；不代表来源完整性",
        "archive_id": "已验证归档回执ID；离线必须有值，恢复后保留归档关联",
        "keep_online": "显式在线保留钉；为真时禁止离线，且当前状态必须为online",
        "changed_at": "最近一次驻留状态或归档关联改变的数据库时刻，带时区",
    },
    "dataset_publications": {
        "id": "发布切换事件ID，由数据集代码与指针修订号组成",
        "dataset_code": "发生当前指针切换的逻辑数据集代码",
        "revision": "当前指针的修订号；与数据集代码联合唯一，不是内容版本号",
        "previous_version_id": "切换前的数据集版本ID；首次发布为空，不补造旧切换历史",
        "version_id": "切换后当前生效的数据集版本ID",
        "occurred_at": "当前指针真实切换并登记事件的数据库时刻，带时区",
    },
    "retention_archives": {
        "id": "不可变归档回执ID，绑定经过验证的密文包及恢复演练",
        "database_name": "归档所对应的原数据库名称；恢复或离线维护时必须匹配",
        "schema_head": "归档数据结构兼容版本；说明迁移19仍使用18格式，与当前修订号可不同",
        "format_revision": "归档文件格式修订号，当前仅允许1",
        "cipher_sha256": "加密清单文件的SHA-256摘要，64位小写十六进制",
        "manifest_sha256": "解密清单规范内容的SHA-256摘要，64位小写十六进制",
        "locations": "归档副本位置及故障域的JSON数组，仅含无秘密的定位信息",
        "verified_at": "完整解密及完整性验证完成时刻，带时区",
        "restored_at": "随机隔离数据库恢复演练成功时刻，非业务库恢复操作时刻",
    },
    "retention_operations": {
        "id": "本次受控维护操作ID，一次操作绑定一个数据库事务",
        "archive_id": "本次操作使用的已验证归档回执ID",
        "kind": "维护动作：offline离线移出、restore从归档恢复",
        "plan_sha256": "本次确认维护计划的SHA-256摘要，防止计划与执行对象漂移",
        "policy_revision": "本次维护采用的保留策略修订标识",
        "code_revision": "维护实现及迁移文件内容摘要，绑定实际执行代码",
        "transaction_id": "执行维护的PostgreSQL事务ID，只允许当前事务登记及完成",
        "status": "事务内操作状态：running执行中、succeeded已完成；失败全事务回滚",
        "created_at": "维护操作登记时刻，带时区",
        "finished_at": "维护操作成功结束时刻；执行中为空，成功时不早于登记时刻",
    },
    "retention_operation_items": {
        "operation_id": "所属维护操作ID，与版本ID和事实表名组成联合主键",
        "version_id": "本次离线或恢复的已发布数据集版本ID",
        "table_name": "获准维护的原事实表名，仅商品日、商品推广、商品地区三表",
        "row_count": "归档或恢复前核定的该版本完整事实行数，允许真实零行",
        "content_sha256": "该版本按稳定主键顺序导出的完整事实内容SHA-256摘要",
    },
    "fact_content_blocks": {
        "id": "事实类型、数据集、行结构、桶及规范行内容的SHA-256寻址ID",
        "table_name": "内容块对应的原事实表名，限定五类类型明确的事实表",
        "dataset_code": "内容块所属逻辑数据集代码；相同数值不跨来源用途复用",
        "row_schema_revision": "事实行解释及结构修订标识；语义改变须提升修订",
        "bucket": "日期或实际期间与0至63稳定分桶后缀组成的桶键，缺日期独立表示",
        "row_count": "本内容块的完整事实行数，必须大于零；空桶不存储",
        "state": "内容块状态：building构建中、sealed已封存；封存后不可改删",
        "created_at": "内容块首次建立的数据库时刻，不是业务日期或版本发布时间",
    },
    "fact_content_manifests": {
        "version_id": "使用共享表示的数据集版本ID，引用dataset_versions，一版本一清单",
        "table_name": "版本完整事实对应的原表名，须与版本数据集用途相容",
        "format_revision": "共享事实清单格式修订号，当前仅允许1",
        "row_count": "本版本所有成员块合计的完整事实行数，允许真实零行",
    },
    "fact_content_members": {
        "version_id": "所属整版共享事实清单ID，与桶键联合唯一",
        "bucket": "该版本的完整成员桶键，必须与引用内容块的桶一致",
        "block_id": "引用已封存不可变内容块ID，同一块可由多个相容版本复用",
    },
    "source_checkpoints": {
        "source_id": "实际物理来源ID；同一来源的不同业务用途共用检查状态",
        "checked_at": "最近一次检查开始或完成时刻，与业务数据日期分开",
        "check_status": "最近检查状态：checking检查中、succeeded成功、failed失败",
        "observation": "最近成功观测的日期、行数和来源修订等小型摘要，失败保留旧值",
        "platform_snapshot": "按已发布版本绑定的各用途日期、历史覆盖及上游更新时间证据摘要",
        "observed_fingerprint": "最近成功观测内容的确定性摘要，检查成功时必须存在",
        "accepted_fingerprint": "同步成功且前后复查稳定后接受的来源摘要，不是CDC水位",
        "accepted_at": "接受来源摘要的时刻，与accepted_fingerprint同时有值或同时为空",
        "error_code": "固定安全错误码，不保存凭据、原始异常或经营原行",
        "duration_ms": "最近一次检查的耗时，单位毫秒，非负",
        "observed_at": "最近成功取得来源观测摘要的时刻；失败不推进",
        "web_updated_at": "数仓网页数据更新时间原文；来源时区未确认，不转换为平台时刻",
        "web_checked_at": "实际读取网页更新时间的带时区时刻，与web_updated_at成对",
        "scheduled_slot_at": "最近开始处理的固定逻辑调度槽时刻，按源登记并跨日重算",
        "scheduled_attempts": "同一固定槽的中断恢复尝试数，数据库范围0至2，不代表跨日失败重试总数",
        "sync_started_at": "本物理来源最近接收开始时刻，不由共享任务整体时间代替",
        "sync_finished_at": "本来源接收结束时刻；执行中为空，结束不早于开始",
        "sync_status": "本来源接收状态：running、succeeded、failed；尚无接收记录为空",
    },
    "source_reconciliations": {
        "source_id": "每周对账的物理来源ID，限定原八来源及商品地区实发实退",
        "cycle_at": "已登记对账周期的计划起点，按北京时间解释计划槽",
        "status": "周期状态：waiting等待、running执行中、failed失败、deferred延后、succeeded成功",
        "last_attempt_at": "当前周期最近一次对账尝试开始时刻；未运行时为空",
        "last_finished_at": "本次对账尝试结束时刻；等待或执行中为空，用于失败重试计时",
        "last_success_at": "最近完成的成功对账时刻；失败、延后和重试不推进",
        "legacy_task_id": "首次对账前已存在普通任务的回退证据ID；任务被删时置空",
        "error_code": "尚未解决的固定对账错误码；重试中保留上次原因，成功清空",
    },
    "weather_publications": {
        "id": "天气发布所绑定的数据集版本ID，与dataset_versions一对一",
        "issue_date": "本份天气发布的基准日期，与父版本起日一致",
        "revision_no": "同一基准日期下的发布修订序号，从1开始，不覆盖旧修订",
        "source_system": "天气来源系统，固定为nmc_official中央气象台官方发布",
        "content_sha256": "天气确定内容的SHA-256摘要，全局唯一，用于相同内容幂等复用",
        "parser_revision": "天气正文解析及解释修订，与父版本行结构修订一致",
        "document": "固定字段的不可变天气发布JSON，含两官文来源及确定解释，不补造群卡链接",
        "received_at": "平台接收并登记本份天气发布的时刻，带时区，非官文发布时间",
    },
}

FACTS = (
    "product_daily_facts",
    "product_promotion_records",
    "product_region_records",
    "product_sales_records",
    "return_records",
)


def _comments():
    """只使用本revision和冻结基线，生成既有空说明对象的完整清单。"""
    for table, description in TABLE_COMMENTS.items():
        yield "TABLE", table, None, description
    for table, columns in COLUMN_COMMENTS.items():
        for column, description in columns.items():
            yield "COLUMN", table, column, description
    metadata = build_metadata()
    for name in FACTS:
        original = metadata.tables["public." + name]
        yield (
            "TABLE", "shared_" + name, None,
            f"{original.comment}的类型化共享事实，按不可变内容块复用",
        )
        yield (
            "VIEW", "read_" + name, None,
            f"{original.comment}的只读兼容视图，统一旧事实与版本成员共享事实",
        )
        for column in original.c:
            if not column.comment:
                raise RuntimeError("SCHEMA_COMMENT_BASELINE_MISSING")
            description = column.comment
            if column.name == "id":
                description = "旧事实原ID，或共享表示下由版本ID和原行键可逆编码生成的稳定事实ID"
            elif column.name == "dataset_version_id":
                description = "本行所属逻辑数据集版本ID；共享表示由版本成员清单投影"
            yield "COLUMN", "read_" + name, column.name, description
            if column.name not in ("id", "dataset_version_id"):
                yield (
                    "COLUMN", "shared_" + name, column.name,
                    "本内容块内原行定位键，与block_id联合唯一；整版封版时仍校验原行键唯一"
                    if column.name == "source_row_key" else column.comment,
                )
        yield "COLUMN", "shared_" + name, "block_id", "所属不可变内容块ID，引用fact_content_blocks"


def _apply(*, remove=False):
    """仅补空说明或重复同说明；冲突整笔拒绝，降级不覆盖后续人工说明。"""
    def literal(value):
        return "NULL::text" if value is None else "'" + value.replace("'", "''") + "'"

    values = ",\n".join("(" + ",".join(map(literal, row)) + ")" for row in _comments())
    target = "NULL::text" if remove else "item.description"
    op.execute(f"""DO $comments$
    DECLARE item record; relation_id oid; previous text;
    BEGIN
      FOR item IN SELECT * FROM (VALUES {values})
          AS descriptions(kind,relation_name,column_name,description) LOOP
        relation_id := ('public.' || item.relation_name)::regclass;
        IF item.kind='COLUMN' THEN
          SELECT col_description(relation_id,attnum) INTO previous FROM pg_attribute
          WHERE attrelid=relation_id AND attname=item.column_name AND NOT attisdropped;
          IF NOT FOUND THEN RAISE EXCEPTION 'SCHEMA_COMMENT_COLUMN_MISSING'; END IF;
        ELSE
          previous := obj_description(relation_id,'pg_class');
        END IF;
        IF previous IS NOT NULL AND previous<>item.description THEN
          RAISE EXCEPTION 'SCHEMA_COMMENT_CONFLICT: %.%',item.relation_name,item.column_name;
        END IF;
        IF item.kind='COLUMN' THEN
          EXECUTE format('COMMENT ON COLUMN public.%I.%I IS %L',
            item.relation_name,item.column_name,{target});
        ELSE
          EXECUTE format('COMMENT ON %s public.%I IS %L',
            item.kind,item.relation_name,{target});
        END IF;
      END LOOP;
    END $comments$""")


def upgrade():
    _apply()


def downgrade():
    _apply(remove=True)
