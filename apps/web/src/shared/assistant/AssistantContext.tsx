import { useContext, useEffect } from "react";
import { AnalyzeScope, RegisterScope } from "./scopeContext";
import type { AssistantScope } from "./contracts";

/** 页面发布已应用的对象、筛选和版本；发送时冻结本轮快照。 */
export function AssistantPageContext({ page, subject, context, selectionLabel }: AssistantScope) {
  const publish = useContext(RegisterScope);
  const listingId = subject?.listing_id,
    shopId = subject?.shop_id;
  const productId = subject?.product_id;
  const name = subject?.name,
    shopName = subject?.shop_name;
  const contextKey = JSON.stringify(context);
  useEffect(() => {
    publish?.({
      page,
      selectionLabel,
      subject:
        listingId && shopId
          ? {
              listing_id: listingId,
              product_id: productId,
              shop_id: shopId,
              name: name ?? null,
              shop_name: shopName ?? null,
            }
          : null,
      context: JSON.parse(contextKey),
    });
    return () => publish?.(null);
  }, [publish, page, listingId, productId, shopId, name, shopName, contextKey, selectionLabel]);
  return null;
}

export function AnalyzeProduct({ subject, context }: Omit<AssistantScope, "page">) {
  const analyze = useContext(AnalyzeScope);
  return analyze && subject ? (
    <button
      type="button"
      className="assistant-analyze-product"
      onClick={() => analyze({ subject, context })}
    >
      分析这款
    </button>
  ) : null;
}
