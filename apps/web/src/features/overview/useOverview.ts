import { usePublishedVersions } from "./publishedVersions";
import { useDomain } from "./data";
import { aftersalesSpec, productsSpec, promotionSpec, salesSpec } from "./adapters";
/** 四条负责域链路独立发布；某域失效不会阻断其余卡片和建议。 */
export function useOverview() {
  usePublishedVersions();
  const sales = useDomain(salesSpec);
  const products = useDomain(productsSpec);
  const promotion = useDomain(promotionSpec);
  const aftersales = useDomain(aftersalesSpec);
  return [sales, products, promotion, aftersales];
}
