"""在生成报告的同一只读事务中，计算两个页面可核对的版本。"""

from sqlalchemy import text

from app.modules.product_board.repository import query_version as product_version
from app.modules.promotion.repository import PRODUCT_CODES
from app.modules.promotion.repository import query_version as promotion_version


def report_versions(repo, *, from_promotion: bool) -> dict[str, str]:
    ids = {code: repo.versions[code]["id"] for code in PRODUCT_CODES}
    if not from_promotion:
        return {
            "product_version": repo.token,
            "promotion_version": promotion_version("product", ids),
        }
    # 商品推广和商品页共享四个来源头；商品页版本还包含已发布的评分头。
    ids.update(
        dict(
            repo.c.execute(
                text(
                    "SELECT d.dataset_code,d.version_id FROM public.current_datasets d "
                    "JOIN public.dataset_versions v ON v.id=d.version_id WHERE v.state='published' "
                    "AND d.dataset_code IN ('product_scores','score_standards')"
                )
            ).all()
        )
    )
    return {"product_version": product_version(ids), "promotion_version": repo.token}
