import { Alert, Button, Empty, Select, Spin, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { localTime, safeLink } from "./model";
import { useWeather } from "./weather";
/** 全国天气按真实发布日/修订展示；天气筛选不进入经营请求参数。 */
export function WeatherSection() {
  const state = useWeather();
  const d = state.publication?.document;
  const blockStatus = (status: string) =>
    ({ parsed: "已识别", unidentified: "待核", boundary: "跨范围原文，待核边界" })[status] ??
    status;
  const block = (title: string, value: NonNullable<typeof d>["today_rain"]) => (
    <article className="m6-weather-metric">
      <span>{title}</span>
      <strong>涉及地区：{value.regions.length ? value.regions.join("、") : "未识别"}</strong>
      <p>{value.detail || "未取得可核正文"}</p>
      <Tag>{blockStatus(value.status)}</Tag>
    </article>
  );
  return (
    <section className="m6-section m6-weather" id="weather">
      <div className="m6-section-title">
        <div>
          <p className="m6-eyebrow">NATIONAL WEATHER WATCH</p>
          <h2>全国关键天气</h2>
        </div>
        <div className="m6-weather-control">
          <label htmlFor="weather-publication">查询日期</label>
          <Select
            id="weather-publication"
            aria-label="天气发布日"
            value={state.selection}
            onChange={state.select}
            options={[
              { value: "latest", label: "跟随最新发布" },
              ...state.options.map((p) => ({
                value: p.id,
                label: `${p.issue_date} · 修订 ${p.revision_no}`,
              })),
            ]}
          />
          <Button
            aria-label="重试天气本地查询"
            icon={<ReloadOutlined />}
            loading={state.refreshing}
            onClick={state.refresh}
          />
        </div>
      </div>
      {state.error && (
        <Alert
          type="warning"
          showIcon
          title={state.error}
          description="天气来源独立；其余经营卡片可继续查看。"
        />
      )}
      {state.meta && state.meta.status !== "ready" && (
        <Alert
          type="warning"
          showIcon
          title={state.meta.status === "partial" ? "天气部分字段待核" : "天气来源不可用"}
          description="成功接收不代表解析齐备；未识别文本不能解释为无天气风险。"
        />
      )}
      {state.meta?.last_attempt_status === "failed" && (
        <Alert
          type="warning"
          showIcon
          title="天气最近更新失败"
          description={`保留已发布内容${state.publication ? `（${state.publication.issue_date} · 修订 ${state.publication.revision_no}）` : "，尚无可用发布"}；原因：${state.meta.error_code ?? "未提供"}。`}
        />
      )}
      {state.loading && !d ? (
        <div className="m6-loading">
          <Spin size="small" /> 正在读取天气发布归档…
        </div>
      ) : d ? (
        <article className="m6-weather-card">
          <header>
            <div>
              <span className="m6-weather-kicker">{d.title}</span>
              <h3>{d.issue_time} 发布</h3>
              <small>
                发布日 {state.publication?.issue_date} · 修订 {state.publication?.revision_no}
              </small>
            </div>
            {safeLink(d.forecast_url) && (
              <Button href={safeLink(d.forecast_url)} target="_blank" rel="noreferrer">
                当前官方预报 ↗
              </Button>
            )}
          </header>
          <p className="m6-weather-focus">
            <strong>重点关注地区</strong>
            <span>
              {d.focus_regions.length ? d.focus_regions.join("、") : "未识别到可靠地区，详见原文"}
            </span>
          </p>
          <div className="m6-weather-grid">
            {block("今日降水", d.today_rain)}
            {block("今日气温", d.today_temperature)}
          </div>
          <div className="m6-weather-outlook">
            <b>未来 7 天天气</b>
            {d.next_7_days.length ? (
              <ul>
                {d.next_7_days.map((p, i) => (
                  <li key={`${p.period}:${i}`}>
                    <strong>{p.period}</strong> {p.regions.join("、")}
                    <p>{p.detail}</p>
                    <Tag>{blockStatus(p.status)}</Tag>
                  </li>
                ))}
              </ul>
            ) : (
              <p>未取得可靠裁剪的过程文本，不能据此判定无风险。</p>
            )}
          </div>
          <footer>
            {d.sources.map((s) => (
              <div key={s.url}>
                <b>
                  {safeLink(s.url) ? (
                    <a href={safeLink(s.url)} target="_blank" rel="noreferrer">
                      {s.name} ↗
                    </a>
                  ) : (
                    s.name
                  )}
                </b>
                <span>实际发布：{s.issue_time}</span>
                <details>
                  <summary>查看来源原文</summary>
                  <p>{s.text}</p>
                </details>
              </div>
            ))}
            {safeLink(d.reference_url) && (
              <a href={safeLink(d.reference_url)} target="_blank" rel="noreferrer">
                全国天气日报表参考 ↗
              </a>
            )}
          </footer>
          <p className="m6-note">{d.note}</p>
        </article>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无可核天气发布，保留查询和展示位置"
        />
      )}
      <p className="m6-note">
        最近成功：{localTime(state.meta?.last_success_at)} · 最近尝试：
        {localTime(state.meta?.last_attempt_at)}（{state.meta?.last_attempt_status ?? "尚无记录"}
        ）。历史发布不显示当前预报为历史原图。
      </p>
    </section>
  );
}
