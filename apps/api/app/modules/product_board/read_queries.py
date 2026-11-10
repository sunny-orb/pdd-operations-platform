"""商品只读投影与分页；沿用调用方固定的版本和事务，不缓存跨请求事实。"""

import sqlalchemy as sa


def listing_filter(listing_ids):
    """空集合不放大为全量；NULL身份仍与原Python分组保持一致。"""
    return dict(ids=[x for x in listing_ids if x is not None], null_id=None in listing_ids)


LISTING_FILTER = "(listing_id=ANY(:ids) OR (:null_id AND listing_id IS NULL))"


def identity_rows(connection, version, start, end):
    """只取首行身份及最后非空名称日的原行，保留名称冲突和原始顺序。"""
    return [
        dict(row)
        for row in connection.execute(
            sa.text("""WITH ranked AS (
                SELECT listing_id,business_date,source_shop_id,source_shop_name,
                       source_product_id,source_product_name,source_row_key,
                       row_number() OVER (PARTITION BY listing_id ORDER BY business_date,
                           source_shop_id,source_product_id,source_row_key) AS first_row,
                       max(business_date) FILTER (WHERE source_product_name IS NOT NULL
                           AND source_product_name<>'') OVER (PARTITION BY listing_id) AS last_name
                FROM public.read_product_daily_facts
                WHERE dataset_version_id=:v AND business_date BETWEEN :start AND :end
            ) SELECT listing_id,business_date,source_shop_id,source_shop_name,
                     source_product_id,source_product_name FROM ranked
              WHERE first_row=1 OR (business_date=last_name AND source_product_name IS NOT NULL
                                    AND source_product_name<>'')
              ORDER BY business_date,source_shop_id,source_product_id,source_row_key"""),
            dict(v=version, start=start, end=end),
        ).mappings()
    ]


def page_rows(connection, version, start, end, listing_ids, page, size):
    """在同一RR快照中计数、稳定分页；C排序与原Python字符串排序一致。"""
    if not listing_ids:
        return [], 0
    params = dict(v=version, start=start, end=end, **listing_filter(listing_ids))
    where = (
        "FROM public.read_product_daily_facts WHERE dataset_version_id=:v "
        "AND business_date BETWEEN :start AND :end AND " + LISTING_FILTER
    )
    count = connection.execute(sa.text("SELECT count(*) " + where), params).scalar_one()
    if (page - 1) * size >= count:
        return [], count
    rows = connection.execute(
        sa.text(
            "SELECT id,listing_id,business_date,source_product_name,visitors,buyers,"
            "product_revenue " + where + ' ORDER BY business_date DESC,source_shop_id COLLATE "C",'
            'source_product_id COLLATE "C",id COLLATE "C" LIMIT :limit OFFSET :offset'
        ),
        dict(params, limit=size, offset=(page - 1) * size),
    ).mappings()
    return [dict(row) for row in rows], count


def rows_at(connection, version, keys):
    """仅查询当前页实际需要的同店同款对应日；闰日缺对应日由调用方排除。"""
    if not keys:
        return []
    pairs = [(lid, day) for lid, day in keys if lid is not None]
    null_days = [day for lid, day in keys if lid is None]
    clauses, params = [], dict(v=version)
    if pairs:
        clauses.append("(listing_id,business_date) IN :pairs")
        params["pairs"] = pairs
    if null_days:
        clauses.append("(listing_id IS NULL AND business_date=ANY(:null_days))")
        params["null_days"] = null_days
    statement = sa.text(
        "SELECT listing_id,business_date,product_revenue FROM public.read_product_daily_facts "
        "WHERE dataset_version_id=:v AND (" + " OR ".join(clauses) + ") "
        "ORDER BY business_date,source_shop_id,source_product_id,source_row_key"
    )
    if pairs:
        statement = statement.bindparams(sa.bindparam("pairs", expanding=True))
    return [dict(row) for row in connection.execute(statement, params).mappings()]
