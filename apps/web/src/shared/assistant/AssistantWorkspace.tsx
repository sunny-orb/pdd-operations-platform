import {
  createContext,
  useCallback,
  memo,
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
  DownOutlined,
  ApiOutlined,
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
import { readAnswer } from "./stream";
import { AssistantReply } from "./AssistantReply";

const AssistantReportView = lazy(async () => ({
  default: memo((await import("./AssistantReport")).AssistantReportView),
}));

const BusinessReportView = lazy(async () => ({
  default: (await import("./BusinessReport")).BusinessReportView,
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

function modelLabel(id: string) {
  const name = id.split(":").at(-1) ?? id;
  return name.replace(
    /^gpt-(.+)-(sol|astra)$/i,
    (_, version: string, family: string) =>
      `GPT-${version} ${family[0].toUpperCase()}${family.slice(1)}`,
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

/** 通用看板不重新定位商品；仅成功的通用查询保留用户已选款，选择/缺证结果可清空。 */
function conversationSubject(result: ChatAnswer, scope: AssistantScope) {
  return (
    result.subject ??
    (result.status === "answered" && result.reports.some((report) => report.kind === "dashboard")
      ? scope.subject
      : null)
  );
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
  const scope = conversation ?? currentScope;
  const product = scope.subject;
  const questions = product
    ? productQuestions
    : (pageQuestions[scope.page] ?? pageQuestions["运营看板中心"]);
  const [businessEnabled, setBusinessEnabled] = useState<boolean | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [choice, setChoice] = useState("");
  const model = models.includes(choice) ? choice : models[0];
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
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
  const differentPage = currentPage !== scope.page;
  const differentSelection = !!currentScope.subject && !sameProduct(currentScope.subject, product);
  const differentPeriod =
    currentScope.context.date_from !== scope.context.date_from ||
    currentScope.context.date_to !== scope.context.date_to;
  const differentVersion =
    currentScope.context.product_version !== scope.context.product_version ||
    currentScope.context.promotion_version !== scope.context.promotion_version;
  const canUsePage =
    JSON.stringify(currentScope.context) !== JSON.stringify(scope.context) ||
    differentPage ||
    differentSelection ||
    differentPeriod ||
    differentVersion ||
    currentScope.context.shop_id !== scope.context.shop_id;
  const pending = turns.at(-1)?.state === "loading";

  const active = useRef<{ controller: AbortController; generation: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    async function loadModels() {
      try {
        const response = await fetch("/api/v1/assistant/models", { signal: controller.signal });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (
          !Array.isArray(data.models) ||
          !data.models.every((name: unknown) => typeof name === "string")
        )
          throw new Error();
        if (!controller.signal.aborted) {
          setModels(data.models);
          setBusinessEnabled(data.business_data_enabled ?? true);
        }
      } catch {
        if (!controller.signal.aborted) setModels([]);
      }
    }
    void loadModels();
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
    if (!conversation) setConversation(currentScope);
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

  function reset(nextScope: AssistantScope) {
    cancelActive();
    setTurns([]);
    setConversation(nextScope);
    setDraft("");
    input.current?.focus();
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
          model,
          question: turn.question,
          context: turn.context,
          history: previous
            .filter(
              (t) =>
                t.result?.status === "answered" &&
                sameProduct(conversationSubject(t.result, t.scope), turn.scope.subject),
            )
            .slice(-6)
            .map((t) => ({
              page: t.scope.page,
              question: t.question,
              answer: t.result!.answer.slice(0, 8000),
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
      const changed = subject && !sameProduct(turn.scope.subject, subject);
      setConversation({
        ...turn.scope,
        subject,
        context: { ...turn.scope.context, ...result.query_context },
      });
      setTurns([...(changed ? [] : previous), { ...turn, state: "done", result }]);
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

  function send(question: string, target = scope, previous = turns) {
    const value = question.trim();
    if (!value || active.current || businessEnabled === false) return;
    const { date_from, date_to, ...base } = target.context;
    const lastReport = previous
      .flatMap((t) => t.result?.reports ?? [])
      .filter((report) => "product" in report && sameProduct(report.product, target.subject))
      .at(-1);
    const lastContext = previous.filter((turn) => turn.result?.status === "answered").at(-1)
      ?.result?.query_context;
    const period = /当前期间|当前时段|当前范围|本页|所选|页面|筛选/.test(value)
      ? { date_from, date_to }
      : lastContext?.date_from && lastContext?.date_to
        ? { date_from: lastContext.date_from, date_to: lastContext.date_to }
        : lastReport?.period
          ? { date_from: lastReport.period.date_from, date_to: lastReport.period.date_to }
          : {};
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
    setDraft("");
    void requestTurn({ question: value, scope: target, context, state: "loading" }, previous);
  }

  function selectCandidate(turn: Turn, candidate: AssistantProduct) {
    if (active.current) return;
    const target = { ...turn.scope, subject: candidate };
    setConversation(target);
    const previous = sameProduct(turn.scope.subject, candidate) ? turns.slice(0, -1) : [];
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
    reset(next);
    updatePanel("assistant", { open: true });
    send(productQuestions[0], next, []);
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
                <button
                  className="assistant-new"
                  onClick={() => reset({ page: currentPage, subject: null, context: {} })}
                  title="新对话"
                >
                  <EditOutlined aria-hidden /> 新对话
                </button>
                <button className="assistant-icon-button" aria-label="收起助手" onClick={close}>
                  <CloseOutlined />
                </button>
              </header>
              <div className="assistant-scope">
                <div>
                  <NodeIndexOutlined aria-hidden /> <strong>{scope.page}</strong>
                  {product && <span>{product.shop_name ?? product.shop_id}</span>}
                </div>
                {product && (
                  <div className="assistant-object">
                    <TagOutlined aria-hidden />
                    <span>
                      {product.name ?? product.listing_id}
                      <small>
                        {product.product_id ? "商品 ID " + product.product_id : product.listing_id}
                      </small>
                    </span>
                  </div>
                )}
                {scope.context.date_from && scope.context.date_to && (
                  <small>
                    页面期间 {scope.context.date_from} 至 {scope.context.date_to}
                  </small>
                )}
                {canUsePage && (
                  <button type="button" onClick={() => reset(currentScope)}>
                    使用当前页范围 · {currentPage}
                  </button>
                )}
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
                      <CommentOutlined aria-hidden /> AI助手{" "}
                      <span>
                        · {turn.scope.page}
                        {turn.result?.model && ` · ${modelLabel(turn.result.model)}`}
                      </span>
                    </div>
                    <AssistantReply
                      text={turn.result?.answer ?? turn.partial ?? ""}
                      streaming={turn.state === "loading"}
                      paused={turn.state === "stopped" || turn.state === "error"}
                      onReveal={followText}
                    />
                    {turn.state === "done" && turn.result ? (
                      <div className="assistant-answer">
                        {turn.result.candidates.length > 0 && i === turns.length - 1 && (
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
                        <Suspense fallback={<small>正在显示指标…</small>}>
                          {turn.result.reports.map((r, idx) =>
                            r.kind === "dashboard" ? (
                              <BusinessReportView
                                key={idx}
                                report={r}
                                showChart={/走势|趋势|图表|曲线|哪天|每天|逐日/.test(turn.question)}
                              />
                            ) : (
                              <AssistantReportView
                                key={idx}
                                report={r}
                                question={turn.question}
                                showChart={/走势|趋势|图表|曲线|哪天|每天|逐日/.test(turn.question)}
                              />
                            ),
                          )}
                        </Suspense>
                        {!turn.result.reports.length && turn.result.evidence && (
                          <details>
                            <summary>查看回答依据</summary>
                            <p className="assistant-reply">{turn.result.evidence}</p>
                          </details>
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
                      <button key={q} onClick={() => send(q)}>
                        {!turns.length && questionIcons[i]}
                        <span>
                          {turns.length && product ? ["近期表现", "异常在哪", "再看推广"][i] : q}
                        </span>
                        {!turns.length && <ArrowRightOutlined aria-hidden />}
                      </button>
                    ))}
                    {scope.context.date_from && scope.context.date_to && (
                      <button onClick={() => send("看看这款在当前期间的表现")}>
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
                    <div className="assistant-model">
                      <ApiOutlined aria-hidden />
                      <select
                        aria-label="回答模型"
                        value={model ?? ""}
                        disabled={pending || models.length < 2}
                        onChange={(event) => setChoice(event.target.value)}
                      >
                        {models.length ? (
                          models.map((id) => (
                            <option key={id} value={id}>
                              {modelLabel(id)}
                            </option>
                          ))
                        ) : (
                          <option value="">默认模型</option>
                        )}
                      </select>
                      {models.length > 1 && (
                        <DownOutlined className="assistant-model-caret" aria-hidden />
                      )}
                    </div>
                    {pending ? (
                      <button type="button" onClick={stop}>
                        停止
                      </button>
                    ) : (
                      <button
                        type="submit"
                        aria-label="发送问题"
                        disabled={!draft.trim() || businessEnabled === false}
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
