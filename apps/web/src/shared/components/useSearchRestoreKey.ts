import { useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/** 历史恢复即使关键词相同也清除草稿；普通筛选提交不触发这一重置。 */
export function useSearchRestoreKey() {
  const { key } = useLocation();
  const action = useNavigationType();
  const [seen, setSeen] = useState({ key, generation: 0 });
  if (seen.key !== key) {
    const generation = seen.generation + (action === "POP" ? 1 : 0);
    setSeen({ key, generation });
    return String(generation);
  }
  return String(seen.generation);
}
