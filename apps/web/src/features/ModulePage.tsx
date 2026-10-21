type Props = { title: string; description: string };

/** 业务迁移占位；输入业务名称和用途，输出无模拟数字的待接入状态。 */
export function ModulePage({ title, description }: Props) {
  return (
    <section className="module-placeholder" aria-label={`${title}接入状态`}>
      <span className="eyebrow">业务模块 · 待迁移</span>
      <h2>{title}</h2>
      <p>{description}</p>
      <div className="empty-icon" aria-hidden="true">
        ↗
      </div>
      <h3>数据接入后，在这里开始分析</h3>
      <p>当前尚未接入业务数据。先确认指标、筛选范围和更新规则，再迁移图表与明细。</p>
      <span className="quiet-tag">尚无经营数据</span>
    </section>
  );
}
