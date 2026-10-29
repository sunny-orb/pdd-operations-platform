-- 仅审阅：Alembic 07→08目标区间与最小权限派生SQL；执行使用受控升级入口。
-- API只读下限为SELECT；writer三表SELECT/INSERT及sales_targets.id UPDATE；回退保留API只读和03已有销售目标权限。
BEGIN;

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

GRANT SELECT ON public.sales_targets, public.sales_target_shops, public.refund_targets TO pdd_app;

GRANT SELECT, INSERT ON public.sales_targets, public.sales_target_shops, public.refund_targets TO pdd_ingest;

GRANT UPDATE(id) ON public.sales_targets TO pdd_ingest;

UPDATE public.alembic_version_v2 SET version_num='20261028_08' WHERE public.alembic_version_v2.version_num = '20261028_07';

COMMIT;
