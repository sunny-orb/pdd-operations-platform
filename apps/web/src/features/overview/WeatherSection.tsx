import { useId, useState } from "react";
import { Alert, Button, Empty, Select, Spin, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { localTime, safeLink } from "./model";
import { useWeather, type WeatherDocument, type WeatherState } from "./weather";
import { weatherTime } from "./weatherFormat";

const blockStatus = (status: string) => (status === "boundary" ? "跨范围原文，待核边界" : "待核");

/** 仅折叠来源长文，不截改正文或生成新的天气结论。 */
function WeatherText({ title, detail }: { title: string; detail: string }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const long = detail.length > 120;
  return (
    <>
      <p id={id} className={long && !expanded ? "m6-weather-preview" : undefined}>
        {detail || "未取得可核正文"}
      </p>
      {long && (
        <Button
          type="link"
          className="m6-weather-expand"
          aria-label={`${expanded ? "收起" : "展开"}${title}全文`}
          aria-controls={id}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起正文" : "展开全文"}
        </Button>
      )}
    </>
  );
}

function WeatherBlock({ title, value }: { title: string; value: WeatherDocument["today_rain"] }) {
  return (
    <article className="m6-weather-metric">
      <span>
        {title}
        {!["parsed", "not_mentioned"].includes(value.status) && (
          <Tag color="gold">{blockStatus(value.status)}</Tag>
        )}
      </span>
      <strong>
        涉及地区：
        {value.regions.length
          ? value.regions.join("、")
          : value.status === "not_mentioned"
            ? "无当日气温描述"
            : value.status === "parsed"
              ? "未提及重点关注地区"
              : "暂无法确定"}
      </strong>
      <WeatherText title={title} detail={value.detail} />
    </article>
  );
}

function WeatherRecords({ state }: { state: WeatherState }) {
  const d = state.publication?.document;
  return (
    <details className="m6-weather-records" aria-label="天气来源与更新记录">
      <summary>来源与更新记录</summary>
      <div className="m6-weather-record-body">
        <p>
          发布日 {state.publication?.issue_date ?? "尚无发布"} · 修订{" "}
          {state.publication?.revision_no ?? "—"}
        </p>
        <p>
          最近成功：{localTime(state.meta?.last_success_at)} · 最近尝试：
          {localTime(state.meta?.last_attempt_at)}（{state.meta?.last_attempt_status ?? "尚无记录"}
          ）。
        </p>
        <p>
          解析状态：{state.meta?.status ?? "尚无数据"}
          。未提及或未识别不代表无天气风险；历史发布中的当前官方预报链接不代表历史原图。
        </p>
        {d?.note && <p>{d.note}</p>}
        {d?.sources.map((source, i) => (
          <section key={`${source.url}:${i}`}>
            <b>
              {source.name} · 实际发布 {weatherTime(source.issue_time)}
            </b>
            <p>{source.text}</p>
          </section>
        ))}
      </div>
    </details>
  );
}

/** 全国天气只展示已有发布；折叠与日期格式化不改变来源、解析状态和查询范围。 */
export function WeatherSection() {
  const state = useWeather();
  const d = state.publication?.document;
  const latest = state.options.find((p) => p.id === state.meta?.default_publication_id);
  const notices = [
    state.error,
    state.meta?.status === "source_unavailable" ? "天气来源不可用" : null,
    state.meta?.last_attempt_status === "failed"
      ? `天气最近更新失败（原因：${state.meta.error_code ?? "未提供"}）`
      : null,
  ].filter(Boolean);
  const futureRegions = [...new Set(d?.next_7_days.flatMap((p) => p.regions) ?? [])];
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
              { value: "latest", label: latest ? `${latest.issue_date}（最新）` : "跟随最新发布" },
              ...state.options.map((p) => ({
                value: p.id,
                label: `${p.issue_date} · 修订 ${p.revision_no}`,
              })),
            ]}
          />
          <Button
            aria-label="重试天气本地查询"
            title="刷新本地天气"
            icon={<ReloadOutlined />}
            loading={state.refreshing}
            onClick={state.refresh}
          />
        </div>
      </div>
      {notices.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={
            <>
              <span>{notices.join("；")}</span>
              {state.publication && (
                <span>
                  ；当前显示 {state.publication.issue_date} · 修订 {state.publication.revision_no}{" "}
                  的发布内容
                </span>
              )}
            </>
          }
        />
      )}
      {state.loading && !d ? (
        <div className="m6-loading">
          <Spin size="small" /> 正在读取天气发布归档…
        </div>
      ) : d ? (
        <article className="m6-weather-card" key={state.publication!.id}>
          <header>
            <div>
              <span className="m6-weather-kicker">{d.title}</span>
              <h3>{weatherTime(d.issue_time)} 发布</h3>
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
            <WeatherBlock title="今日降水" value={d.today_rain} />
            <WeatherBlock title="今日气温" value={d.today_temperature} />
          </div>
          <div className="m6-weather-outlook">
            <b>未来 7 天天气</b>
            {d.next_7_days.length ? (
              <details className="m6-weather-outlook-details" aria-label="未来7天天气">
                <summary>
                  <span>
                    共 {d.next_7_days.length} 条预报过程 · 涉及地区：
                    {futureRegions.length ? futureRegions.join("、") : "未识别"}
                    {d.next_7_days.some((p) => p.status !== "parsed") && (
                      <Tag color="gold">含待核时段</Tag>
                    )}
                  </span>
                  <span className="m6-weather-disclosure-label">
                    <span className="m6-weather-open-label">查看全部预报</span>
                    <span className="m6-weather-close-label">收起预报</span>
                  </span>
                </summary>
                <ul>
                  {d.next_7_days.map((p, i) => (
                    <li key={`${p.period}:${i}`}>
                      <strong>{p.period}</strong> {p.regions.join("、")}
                      <p>{p.detail}</p>
                      {p.status !== "parsed" && <Tag color="gold">{blockStatus(p.status)}</Tag>}
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p>未取得可靠裁剪的过程文本，不能据此判定无风险。</p>
            )}
          </div>
          <footer>
            {d.sources.map((source, i) => (
              <span className="m6-weather-source" key={`${source.url}:${i}`}>
                {safeLink(source.url) ? (
                  <a href={safeLink(source.url)} target="_blank" rel="noreferrer">
                    {source.name} ↗
                  </a>
                ) : (
                  source.name
                )}{" "}
                {weatherTime(source.issue_time)}
              </span>
            ))}
            {safeLink(d.reference_url) && (
              <a href={safeLink(d.reference_url)} target="_blank" rel="noreferrer">
                全国天气日报表参考 ↗
              </a>
            )}
            <WeatherRecords state={state} />
          </footer>
        </article>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无可核天气发布，保留查询和展示位置"
        />
      )}
      {!d && state.meta && <WeatherRecords state={state} />}
    </section>
  );
}
