import {
  createContext,
  useCallback,
  useContext,
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import {
  ArrowRightOutlined,
  ArrowUpOutlined,
  BarChartOutlined,
  BulbOutlined,
  CloseOutlined,
  CommentOutlined,
  EditOutlined,
  LineChartOutlined,
  NodeIndexOutlined,
  TagOutlined,
} from "@ant-design/icons";
import { dashboardPages } from "../layouts/dashboardNavigation";
import {
  sameProduct,
  type AssistantScope,
  type AssistantProduct,
  type ChatAnswer,
  type PageContext,
} from "./contracts";
import { RegisterScope, AnalyzeScope } from "./scopeContext";
import { PanelResizer } from "../components/PanelResizer";
import { useDashboardPanel, updatePanel } from "../layouts/panelLayout";
import "./assistant.css";
import { globalScope, samePageFilters, scopeSummary } from "./pageScope";
import { readAnswer } from "./stream";

const loadReply = () => import("./AssistantReply");
const AssistantReply = lazy(async () => ({
  default: (await loadReply()).AssistantReply,
}));

const AssistantEvidence = lazy(async () => ({
  default: (await import("./AssistantEvidence")).AssistantEvidence,
}));

const Entry = createContext<{ open: boolean; toggle: () => void } | null>(null);
const questionIcons = [
  <LineChartOutlined aria-hidden />,
  <BulbOutlined aria-hidden />,
  <BarChartOutlined aria-hidden />,
];

export function AssistantEntry() {
  const entry = useContext(Entry);
  if (!entry) return null;
  return (
    <button
      id="assistant-entry"
      className="assistant-entry"
      aria-controls="assistant-panel"
      aria-expanded={entry.open}
      onClick={entry.toggle}
    >
      <CommentOutlined aria-hidden /> AI助手
    </button>
  );
}

const productQuestions = ["这款近 7 天表现怎样？", "这款的异常主要在哪？", "再看看这款的推广"];
const pageQuestions: Record<string, string[]> = {
  运营看板中心: [
    "最近 7 天整体经营怎么样？",
    "哪些商品和推广问题要优先关注？",
    "当前数据更新到哪一天？",
  ],
  商品数据: [
    "当前期间商品整体表现怎么样？",
    "当前期间哪些商品有经营风险？",
    "看看当前期间的新品评分",
  ],
  店铺销售: [
    "当前期间店铺销售表现怎么样？",
    "本月GMV目标达成怎么样？",
    "当前期间哪个店铺贡献最多？",
  ],
  推广分析: [
    "当前期间推广投产和花费怎么样？",
    "看看当前范围的推广诊断",
    "当前期间推广趋势怎么样？",
  ],
  售后分析: [
    "当前报告退款情况怎么样？",
    "当前报告哪些款的质量问题集中？",
    "当前报告和上期相比有哪些变化？",
  ],
};
type Turn = {
  scopeId: number;
  historyScopeId?: number;
  historyThrough?: number;
  resolved?: AssistantScope;
  scope: AssistantScope;
  context: PageContext & {
    listing_id?: string;
    filter_shop_id?: string;
    candidate_confirmed?: boolean;
  };
  result?: ChatAnswer;
  question: string;
  state: "loading" | "done" | "stopped" | "error";
  error?: string;
  partial?: string;
  progress?: string;
};

/** 取数或规则缺证沿用选款；服务端明确换款失败或给出新候选时清除旧对象。 */
function conversationSubject(result: ChatAnswer, scope: AssistantScope) {
  if (result.subject) return result.subject;
  if (result.subject_cleared || (result.status === "need_selection" && result.candidates.length))
    return null;
  return result.status !== "answered" ||
    !result.reports.length ||
    result.reports.some((report) => report.kind === "dashboard")
    ? scope.subject
    : null;
}

/** 位于路由上层，切页仅更新可选的页面范围，收起不会丢失草稿和对话。 */
export function AssistantWorkspace({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const path = pathname === "/store-sales" ? "/sales" : pathname;
  const currentPage = dashboardPages.find((page) => page.path === path)?.title ?? "运营看板中心";
  const { open, width } = useDashboardPanel("assistant");
  const panelStyle = { "--assistant-width": `${width}px` } as CSSProperties;
  const [pageScope, publishScope] = useState<AssistantScope | null>(null);
  const [conversation, setConversation] = useState<AssistantScope | null>(null);
  const currentScope: AssistantScope =
    pageScope?.page === currentPage ? pageScope : { page: currentPage, subject: null, context: {} };
  const [scopeId, setScopeId] = useState(0);
  const [origin, setOrigin] = useState<AssistantScope | null>(null);
  const [following, setFollowing] = useState(true);
  const page = following ? currentScope : globalScope;
  const product = page.subject;
  const pageReady = !following || Object.keys(currentScope.context).length > 0;
  const sameOrigin = !!origin && samePageFilters(origin, page);
  const summary = following ? scopeSummary(page) : "全局问答";
  const [businessEnabled, setBusinessEnabled] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const questions = product
    ? productQuestions
    : (pageQuestions[page.page] ?? pageQuestions[globalScope.page]);
  const dialog = useRef<HTMLDialogElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const followAnswer = useRef(true);
  const scrollFrame = useRef(0);
  const followText = useCallback(() => {
    if (scrollFrame.current) return;
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = 0;
      if (followAnswer.current && history.current)
        history.current.scrollTop = history.current.scrollHeight;
    });
  }, []);
  useEffect(() => () => cancelAnimationFrame(scrollFrame.current), []);
  const pending = turns.at(-1)?.state === "loading";

  const active = useRef<{ controller: AbortController; generation: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    // 打开助手即预热正文排版，首次输入时与用户操作并行加载。
    void loadReply().catch(() => {});
    const controller = new AbortController();
    async function checkAvailability() {
      try {
        const response = await fetch("/api/v1/assistant/models", { signal: controller.signal });
        if (!response.ok) return;
        const data = await response.json();
        if (!controller.signal.aborted && Array.isArray(data.models)) {
          setBusinessEnabled(data.models.length > 0 && data.business_data_enabled !== false);
        }
      } catch {
        // 状态查询失败仍可由正常发送入口重试，并显示服务端的具体错误。
      }
    }
    void checkAvailability();
    return () => controller.abort();
  }, [open]);

  useEffect(() => {
    if (!open || !dialog.current) return;
    const panel = dialog.current;
    const wide = window.matchMedia("(min-width: 1400px)");
    function place() {
      if (panel.open) panel.close();
      if (wide.matches) panel.show();
      else panel.showModal();
    }
    place();
    wide.addEventListener("change", place);
    return () => {
      wide.removeEventListener("change", place);
      if (panel.open) panel.close();
    };
  }, [open]);

  useEffect(() => {
    if (open) followText();
  }, [turns.length, open, pending, followText]);

  function close() {
    updatePanel("assistant", { open: false });
    requestAnimationFrame(() => document.getElementById("assistant-entry")?.focus());
  }
  function toggle() {
    if (open) {
      close();
      return;
    }
    updatePanel("assistant", { open: true });
  }
  function cancelActive() {
    const request = active.current;
    if (!request) return;
    active.current = null;
    request.controller.abort();
    // 显式结束指定生成，跨代理也能取消，迟到的停止请求不影响下一问。
    void fetch("/api/v1/assistant/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ generation: request.generation }),
      keepalive: true,
    }).catch(() => {});
  }
  useEffect(() => () => cancelActive(), []);

  function clearActiveScope() {
    setScopeId((id) => id + 1);
    setOrigin(null);
    setConversation(null);
  }

  function reset() {
    cancelActive();
    setTurns([]);
    clearActiveScope();
    input.current?.focus();
    setDraft("");
  }

  /** 一次生成绑定提问当时的范围。重答/候选确认重用该快照，跨页不会改变请求。 */
  async function requestTurn(turn: Turn, previous: Turn[]) {
    if (active.current) return;
    const controller = new AbortController();
    const generation = crypto.randomUUID();
    active.current = { controller, generation };
    followAnswer.current = true;
    turn = { ...turn, partial: "", progress: "正在理解问题…" };
    setTurns([...previous, turn]);
    let paint = 0;
    const publish = () => {
      if (paint) return;
      paint = requestAnimationFrame(() => {
        paint = 0;
        if (!controller.signal.aborted) setTurns([...previous, turn]);
      });
    };
    try {
      const response = await fetch("/api/v1/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        signal: controller.signal,
        body: JSON.stringify({
          page: turn.scope.page,
          generation,
          question: turn.question,
          context: turn.context,
          history: previous
            .slice(0, turn.historyThrough)
            .filter(
              (t) =>
                t.scopeId === (turn.historyScopeId ?? turn.scopeId) &&
                t.state === "done" &&
                t.result &&
                sameProduct(conversationSubject(t.result, t.scope), turn.scope.subject),
            )
            .slice(-6)
            .map((t) => ({
              page: t.scope.page,
              question: t.question,
              answer: t.result!.answer.slice(0, 8000),
              status: t.result!.status,
            })),
        }),
      });
      const result = await readAnswer(response, (event) => {
        if (controller.signal.aborted) return;
        if (event.type === "delta") turn = { ...turn, partial: (turn.partial ?? "") + event.text };
        else turn = { ...turn, progress: event.text };
        publish();
      });
      if (controller.signal.aborted) return;
      const subject = conversationSubject(result, turn.scope);
      const nextContext = { ...turn.scope.context };
      if (result.subject_cleared) {
        delete nextContext.listing_ids;
        delete nextContext.product_key;
        delete nextContext.shop_id;
        delete nextContext.explanation;
        delete nextContext.date_from;
        delete nextContext.date_to;
      }
      const mergedContext = { ...nextContext, ...result.query_context };
      if (mergedContext.explanation === null) delete mergedContext.explanation;
      if (mergedContext.risk_reference === null) delete mergedContext.risk_reference;
      const resolved = { ...turn.scope, subject, context: mergedContext };
      setConversation((current) => current && resolved);
      setTurns([...previous, { ...turn, state: "done", result, resolved }]);
    } catch (error) {
      if (!controller.signal.aborted)
        setTurns([
          ...previous,
          {
            ...turn,
            state: "error",
            error:
              error instanceof TypeError
                ? "未能连接本机问答服务，请稍后重试"
                : error instanceof Error
                  ? error.message
                  : "回答失败，请重试",
          },
        ]);
    } finally {
      cancelAnimationFrame(paint);
      if (active.current?.controller === controller) active.current = null;
    }
  }

  function send(question: string, chosen?: AssistantScope, previous = turns, keepDraft = false) {
    const value = question.trim();
    if (!value || active.current || businessEnabled === false) return;
    // 指向上文时取当次已确认的对象；“这款”和页面快捷问题使用界面当前选款。
    const recall =
      !chosen && /刚才|方才|上一问|上面(?:提到|说|那)|之前(?:说|聊|提到|那)/.test(value);
    if (!pageReady && !recall && !chosen) return;
    const earlier = recall ? previous.slice().reverse() : [];
    const remembered =
      earlier.find((t) => t.resolved?.subject?.name && value.includes(t.resolved.subject.name)) ??
      earlier.find((t) => t.resolved);
    const historyThrough = remembered ? previous.indexOf(remembered) + 1 : undefined;
    const pageRequest =
      !!chosen ||
      /本页|当前页|当前筛选|所选/.test(value) ||
      (!!product &&
        !sameProduct(product, conversation?.subject ?? null) &&
        /这款|这个商品/.test(value));
    const target =
      chosen ??
      remembered?.resolved ??
      (pageRequest || !sameOrigin ? page : (conversation ?? page));
    const startsRange =
      !sameOrigin ||
      (pageRequest && conversation && !samePageFilters(conversation, target)) ||
      keepDraft;
    const nextId =
      startsRange || (remembered && remembered.scopeId !== scopeId) ? scopeId + 1 : scopeId;
    const historyId = remembered?.scopeId ?? nextId;
    setScopeId(nextId);
    setOrigin(page);
    setConversation(target);
    const { date_from, date_to, ...base } = target.context;
    const thread = previous.slice(0, historyThrough).filter((t) => t.scopeId === historyId);
    const clearedAt = thread.reduce(
      (last, turn, index) => (turn.state === "done" && turn.result?.subject_cleared ? index : last),
      -1,
    );
    const scopedTurns = clearedAt < 0 ? thread : thread.slice(clearedAt);
    const lastReport = scopedTurns
      .flatMap((t) => t.result?.reports ?? [])
      .filter((report) => "product" in report && sameProduct(report.product, target.subject))
      .at(-1);
    const lastContext = scopedTurns
      .filter((turn) => turn.state === "done" && turn.result?.query_context)
      .at(-1)?.result?.query_context;
    const period = /当前期间|当前时段|当前范围|本页|所选|页面|筛选/.test(value)
      ? { date_from, date_to }
      : lastContext?.date_from && lastContext?.date_to
        ? { date_from: lastContext.date_from, date_to: lastContext.date_to }
        : lastReport?.period
          ? { date_from: lastReport.period.date_from, date_to: lastReport.period.date_to }
          : { date_from, date_to };
    const context = {
      ...base,
      ...lastContext,
      versions: { ...base.versions, ...lastContext?.versions },
      filter_shop_id: lastContext?.shop_id ?? base.shop_id,
      ...(lastReport?.kind !== "dashboard" ? lastReport?.versions : {}),
      ...period,
      ...(target.subject
        ? { listing_id: target.subject.listing_id, shop_id: target.subject.shop_id }
        : {}),
    };
    if (context.explanation === null) delete context.explanation;
    if (context.risk_reference === null) delete context.risk_reference;
    if (!keepDraft) setDraft("");
    void requestTurn(
      {
        scopeId: nextId,
        historyScopeId: historyId,
        historyThrough,
        question: value,
        scope: target,
        context,
        state: "loading",
      },
      previous,
    );
  }

  function selectCandidate(turn: Turn, candidate: AssistantProduct) {
    if (active.current) return;
    const target = { ...turn.scope, subject: candidate };
    setConversation(target);
    const previous = turns.slice(0, -1);
    void requestTurn(
      {
        ...turn,
        scope: target,
        context: {
          ...turn.context,
          listing_id: candidate.listing_id,
          shop_id: candidate.shop_id,
          candidate_confirmed: true,
        },
        result: undefined,
        state: "loading",
      },
      previous,
    );
  }

  function analyze(target: Omit<AssistantScope, "page">) {
    const next = { ...target, page: currentPage };
    cancelActive();
    updatePanel("assistant", { open: true });
    const previous = turns.map((t): Turn =>
      t.state === "loading" ? { ...t, state: "stopped" } : t,
    );
    send(productQuestions[0], next, previous, true);
  }
  function stop() {
    cancelActive();
    setTurns((items) =>
      items.map((item) => (item.state === "loading" ? { ...item, state: "stopped" } : item)),
    );
  }
  return (
    <RegisterScope.Provider value={publishScope}>
      <AnalyzeScope.Provider value={analyze}>
        <Entry.Provider value={{ open, toggle }}>
          <div
            style={panelStyle}
            className={open ? "assistant-workspace is-open" : "assistant-workspace"}
          >
            {children}
          </div>
          {open && (
            <dialog
              id="assistant-panel"
              className="assistant-panel"
              style={panelStyle}
              aria-labelledby="assistant-title"
              ref={dialog}
              onCancel={(event) => {
                event.preventDefault();
                close();
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape" && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  close();
                }
              }}
            >
              <PanelResizer
                panel="assistant"
                width={width}
                label="调整助手宽度"
                controls="assistant-panel"
              />
              <header className="assistant-head">
                <span className="assistant-mark">
                  <CommentOutlined aria-hidden />
                </span>
                <h2 id="assistant-title">AI助手</h2>
                <button className="assistant-new" onClick={reset} title="新对话">
                  <EditOutlined aria-hidden /> 新对话
                </button>
                <button className="assistant-icon-button" aria-label="收起助手" onClick={close}>
                  <CloseOutlined />
                </button>
              </header>
              <div className="assistant-scope">
                {product ? <TagOutlined aria-hidden /> : <NodeIndexOutlined aria-hidden />}
                <strong title={summary} aria-label={summary}>
                  {summary}
                </strong>
                <button
                  type="button"
                  role="switch"
                  aria-checked={following}
                  className="assistant-follow-mode"
                  onClick={() => {
                    setFollowing(!following);
                    clearActiveScope();
                  }}
                >
                  <span className="assistant-follow-track" aria-hidden="true" />
                  跟随页面
                </button>
              </div>
              <div
                ref={history}
                onScroll={(event) => {
                  const el = event.currentTarget;
                  followAnswer.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
                }}
                className="assistant-history"
              >
                {!turns.length && (
                  <section className="assistant-welcome">
                    <div className="assistant-welcome-icon">
                      <LineChartOutlined aria-hidden />
                    </div>
                    <h3>{product ? "想了解这款商品的什么情况？" : "想了解哪些经营情况？"}</h3>
                    <p>
                      {product
                        ? "可以查看近期表现、异常和推广。"
                        : "直接提问，也可以从下面的问题开始。"}
                    </p>
                  </section>
                )}
                {turns.map((turn, i) => (
                  <article className="assistant-turn" key={i}>
                    <div className="assistant-question">{turn.question}</div>
                    <div className="assistant-byline">
                      <CommentOutlined aria-hidden /> AI助手 <span>· {turn.scope.page}</span>
                    </div>
                    <Suspense fallback={null}>
                      <AssistantReply
                        text={turn.result?.answer ?? turn.partial ?? ""}
                        streaming={turn.state === "loading"}
                        paused={turn.state === "stopped" || turn.state === "error"}
                        onReveal={followText}
                      />
                    </Suspense>
                    {turn.state === "done" && turn.result ? (
                      <div className="assistant-answer">
                        {turn.result.candidates.length > 0 &&
                          turn.scopeId === scopeId &&
                          i === turns.length - 1 && (
                            <div className="assistant-candidates" aria-label="选择商品">
                              {turn.result.candidates.map((candidate) => (
                                <button
                                  key={candidate.listing_id}
                                  onClick={() => selectCandidate(turn, candidate)}
                                >
                                  <b>{candidate.name ?? candidate.listing_id}</b>
                                  <small>
                                    {candidate.shop_name ?? candidate.shop_id} ·{" "}
                                    {candidate.product_id ?? candidate.listing_id}
                                  </small>
                                </button>
                              ))}
                            </div>
                          )}
                        {(turn.result.reports.length > 0 || !!turn.result.evidence.trim()) && (
                          <Suspense fallback={null}>
                            <AssistantEvidence result={turn.result} question={turn.question} />
                          </Suspense>
                        )}
                      </div>
                    ) : (
                      <div className="assistant-answer">
                        <div role="status" className="assistant-status">
                          {turn.state === "loading"
                            ? turn.partial
                              ? "正在回答…"
                              : (turn.progress ?? "正在查询…")
                            : (turn.error ?? "已停止回答")}
                          {(turn.state === "stopped" || turn.state === "error") &&
                            turn.scopeId === scopeId &&
                            i === turns.length - 1 && (
                              <button
                                onClick={() =>
                                  void requestTurn(
                                    { ...turn, state: "loading", error: undefined },
                                    turns.slice(0, -1),
                                  )
                                }
                              >
                                重新回答
                              </button>
                            )}
                        </div>
                      </div>
                    )}
                  </article>
                ))}
                {!pending && businessEnabled !== false && (
                  <div
                    className={turns.length ? "assistant-followups" : "assistant-suggestions"}
                    aria-label="建议问题"
                  >
                    {questions.map((q, i) => (
                      <button key={q} disabled={!pageReady} onClick={() => send(q, page)}>
                        {!turns.length && questionIcons[i]}
                        <span>
                          {turns.length && product ? ["近期表现", "异常在哪", "再看推广"][i] : q}
                        </span>
                        {!turns.length && <ArrowRightOutlined aria-hidden />}
                      </button>
                    ))}
                    {product && page.context.date_from && page.context.date_to && (
                      <button onClick={() => send("看看这款在当前期间的表现", page)}>
                        <span>查看当前期间</span>
                      </button>
                    )}
                  </div>
                )}
              </div>
              {businessEnabled === false && (
                <p role="status" className="assistant-unavailable">
                  本机尚未启用经营数据问答。
                </p>
              )}
              <form
                className="assistant-compose"
                onSubmit={(event) => {
                  event.preventDefault();
                  send(draft);
                }}
              >
                <div>
                  <textarea
                    ref={input}
                    aria-label="输入运营问题"
                    placeholder={"输入问题，或继续追问…"}
                    maxLength={2000}
                    rows={2}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing &&
                        e.nativeEvent.keyCode !== 229
                      ) {
                        e.preventDefault();
                        send(draft);
                      }
                    }}
                  />
                  <div className="assistant-compose-tools">
                    {pending ? (
                      <button type="button" onClick={stop}>
                        停止
                      </button>
                    ) : (
                      <button
                        type="submit"
                        aria-label="发送问题"
                        disabled={!draft.trim() || businessEnabled === false || !pageReady}
                      >
                        <ArrowUpOutlined />
                      </button>
                    )}
                  </div>
                </div>
              </form>
            </dialog>
          )}
        </Entry.Provider>
      </AnalyzeScope.Provider>
    </RegisterScope.Provider>
  );
}
