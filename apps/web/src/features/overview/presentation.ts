import type { ActionObject, AlertObject } from "./model";

/** 仅为六行展示容量，不是异常阈值或业务总数。 */
export const OVERVIEW_PAGE_SIZE = 6;

/** 输入已归并排序的完整来源清单；只取首页预览及对应建议，不重判风险或补建议。 */
export function homePreview(alerts: readonly AlertObject[], actions: readonly ActionObject[]) {
  const shownAlerts = alerts.slice(0, OVERVIEW_PAGE_SIZE);
  const objectIds = new Set(shownAlerts.filter((item) => item.objectKey).map((item) => item.id));
  const sourceIds = new Set(
    shownAlerts.flatMap((item) =>
      item.members.map((member) => JSON.stringify([member.domain, member.id])),
    ),
  );
  return {
    alerts: shownAlerts,
    actions: actions
      .filter(
        (item) =>
          objectIds.has(item.id) ||
          item.members.some((member) => sourceIds.has(JSON.stringify([member.domain, member.id]))),
      )
      .slice(0, OVERVIEW_PAGE_SIZE),
  };
}
