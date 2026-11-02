import "./calendar-icon.css";

/** 原生日历图形可见高度与日期字号一致；来源及授权见calendar-icon.LICENSE。 */
export function CalendarIcon() {
  return (
    <svg
      className="dashboard-calendar-icon"
      width="1em"
      height="1em"
      viewBox="1 1 22 22"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M20 3h-1V1h-2v2H7V1H5v2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 18H4V8h16v13z"
      />
    </svg>
  );
}
