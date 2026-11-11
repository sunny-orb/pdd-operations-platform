import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Input, type InputProps } from "antd";

type Props = Omit<
  InputProps,
  | "value"
  | "defaultValue"
  | "onChange"
  | "onPressEnter"
  | "onCompositionStart"
  | "onCompositionEnd"
  | "onClear"
> & {
  value: string;
  onSearch: (value: string) => void;
  /** 重置或更换所属筛选范围时取消草稿；不随查询响应改变。 */
  resetKey?: string;
};

const SEARCH_DELAY_MS = 500;

/** 输入草稿同步更新；中文组词结束后停顿提交，普通回车/清空立即提交。
 * value是已应用条件，onSearch只提交完整关键词；请求结果不参与输入状态。
 */
export function SearchInput({ value, onSearch, resetKey, ...props }: Props) {
  const [draft, setDraft] = useState(value);
  const composing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const submitted = useRef<string | null>(null);
  const latest = useRef({ value, onSearch });
  const previous = useRef({ value, resetKey });
  const cancel = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = undefined;
  }, []);

  useLayoutEffect(() => {
    latest.current = { value, onSearch };
    if (previous.current.value === value && previous.current.resetKey === resetKey) return;
    const acknowledged = previous.current.resetKey === resetKey && submitted.current === value;
    previous.current = { value, resetKey };
    submitted.current = null;
    // 自己提交的回显不能覆盖之后继续输入的文字；外部恢复/重置则立即生效。
    if (!acknowledged) {
      cancel();
      composing.current = false;
      setDraft(value);
    }
  }, [value, onSearch, resetKey, cancel]);
  useEffect(() => cancel, [cancel]);

  const submit = useCallback(
    (next: string) => {
      cancel();
      if (
        submitted.current === next ||
        (submitted.current === null && latest.current.value === next)
      )
        return;
      submitted.current = next;
      latest.current.onSearch(next);
    },
    [cancel],
  );
  const schedule = (next: string) => {
    cancel();
    if (next === "") submit(next);
    else timer.current = setTimeout(() => submit(next), SEARCH_DELAY_MS);
  };

  return (
    <Input
      {...props}
      value={draft}
      onChange={(event) => {
        const next = event.target.value;
        setDraft(next);
        if (!composing.current && !(event.nativeEvent as InputEvent).isComposing) schedule(next);
      }}
      onCompositionStart={() => {
        composing.current = true;
        cancel();
      }}
      onCompositionEnd={(event) => {
        composing.current = false;
        const next = event.currentTarget.value;
        setDraft(next);
        schedule(next);
      }}
      onPressEnter={(event) => {
        if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        submit(draft);
      }}
      onClear={() => {
        composing.current = false;
        setDraft("");
        submit("");
      }}
    />
  );
}
