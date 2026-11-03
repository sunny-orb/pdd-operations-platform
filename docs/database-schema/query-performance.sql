-- 仅审阅：Alembic 08→09日期查询索引；执行使用受控本地升级入口。
BEGIN;

-- Running upgrade 20261028_08 -> 20261031_09

CREATE INDEX ix_product_promotion_records_version_period_start ON public.product_promotion_records (dataset_version_id, period_start);

CREATE INDEX ix_product_region_records_version_period_start ON public.product_region_records (dataset_version_id, period_start);

UPDATE public.alembic_version_v2 SET version_num='20261031_09' WHERE public.alembic_version_v2.version_num = '20261028_08';

COMMIT;
