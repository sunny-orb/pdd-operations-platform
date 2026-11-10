"""四域发布变更标识：只读当前输入头与售后报告目录ID，不建立业务快照。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.modules.aftersales import repository as aftersales_repository
from app.modules.aftersales.repository import ROLES
from app.modules.product_board import repository as products_repository
from app.modules.product_board.repository import CODES, BoardError
from app.modules.promotion import metrics as promotion_metrics
from app.modules.promotion import repository as promotion_repository
from app.modules.promotion.repository import PRODUCT_CODES, STORE_CODES
from app.modules.sales.datasets import HISTORY_SPEC, READ_SPECS

DOMAIN_DATASETS = {
    "products": (*CODES, "score_standards", "product_scores"),
    "promotion": (*PRODUCT_CODES, *STORE_CODES, HISTORY_SPEC.code),
    "sales": ("shop_daily", *READ_SPECS, "sales_targets"),
    "aftersales": ("aftersales_report", "refund_targets", *(role[0] for role in ROLES.values())),
}


def read_published_versions(engine):
    """返回各域稳定SHA256；缺头以null入摘要，售后历史补报也触发变版，无写入。"""
    if engine is None:
        raise BoardError("DATABASE_UNAVAILABLE", 503)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(sa.text("SET TRANSACTION READ ONLY"))
            heads = dict(
                c.execute(
                    sa.text(
                        "SELECT d.dataset_code,d.version_id FROM public.current_datasets d "
                        "JOIN public.dataset_versions v ON v.id=d.version_id "
                        "WHERE v.state='published' AND d.dataset_code=ANY(:codes)"
                    ),
                    dict(
                        codes=sorted({code for codes in DOMAIN_DATASETS.values() for code in codes})
                    ),
                ).all()
            )
            # 售后可见目录包含历史报告；旧期补报不会推进最新current指针。
            # 与售后Repository一致过滤，只取目录身份/规则ID，不读报告内容或事实。
            reports = [
                tuple(row)
                for row in c.execute(
                    sa.text(
                        "SELECT r.id,r.dataset_version_id,v.rule_set_id "
                        "FROM public.aftersales_reports r "
                        "JOIN public.dataset_versions v ON v.id=r.dataset_version_id "
                        "WHERE v.state='published' AND v.dataset_code='aftersales_report' "
                        "ORDER BY r.id"
                    )
                )
            ]
            data = {}
            business_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            definitions = {
                "products": products_repository.DEFINITION_REVISION,
                "promotion": promotion_repository.definition_revision(),
                "sales": promotion_metrics.definition_revision(),
                "aftersales": aftersales_repository.revision(),
            }
            for domain, codes in DOMAIN_DATASETS.items():
                vector = {
                    "inputs": {code: heads.get(code) for code in codes},
                    "definition": definitions[domain],
                }
                # 推广昨日状态与销售默认月份依赖上海业务日，即使输入头不变也需重取。
                if domain in {"promotion", "sales"}:
                    vector["business_date"] = business_date
                if domain == "aftersales":
                    vector["reports"] = reports
                data[domain] = hashlib.sha256(
                    json.dumps(vector, sort_keys=True).encode()
                ).hexdigest()
            return {"data": data}
    except SQLAlchemyError as exc:
        raise BoardError("DATABASE_UNAVAILABLE", 503) from exc
