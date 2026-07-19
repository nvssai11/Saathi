export function timeAgo(iso: string, t?: (key: string, opts?: Record<string, unknown>) => string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return t ? t("notifications.justNow") : "just now";
  if (minutes < 60) return t ? t("notifications.minutesAgo", { count: minutes }) : `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t ? t("notifications.hoursAgo", { count: hours }) : `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return t ? t("notifications.daysAgo", { count: days }) : `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function dayLabel(iso: string, t?: (key: string) => string): string {
  const startOfDay = (d: Date) => {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x.getTime();
  };
  const diffDays = Math.round((startOfDay(new Date()) - startOfDay(new Date(iso))) / 86400000);
  if (diffDays === 0) return t ? t("notifications.today") : "Today";
  if (diffDays === 1) return t ? t("notifications.yesterday") : "Yesterday";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export type DeadlineUrgency = "critical" | "warning" | "normal";

export function daysUntil(deadline: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(deadline + "T00:00:00");
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

export function deadlineUrgency(deadline: string): DeadlineUrgency {
  const days = daysUntil(deadline);
  if (days < 0) return "critical";
  if (days <= 2) return "warning";
  return "normal";
}

export function deadlineLabel(
  deadline: string,
  t?: (key: string, opts?: Record<string, unknown>) => string
): string {
  const days = daysUntil(deadline);
  if (days < 0) {
    return t ? t("sublots.overdue", { count: Math.abs(days) }) : `${Math.abs(days)}d overdue`;
  }
  if (days === 0) return t ? t("sublots.dueToday") : "Due today";
  if (days === 1) return t ? t("sublots.dueTomorrow") : "Due tomorrow";
  return t ? t("sublots.daysLeft", { count: days }) : `${days}d left`;
}

export type CapacityUrgency = "critical" | "warning" | "normal";

export function capacityUrgency(servingCapacity: number, availableQty: number): CapacityUrgency {
  if (servingCapacity <= 0) return "critical";
  if (availableQty > 0 && servingCapacity / availableQty <= 0.2) return "warning";
  return "normal";
}

const BUYER_STATUS_KEYS: Record<string, string> = {
  Received: "received",
  Processing: "processing",
  Confirmed: "confirmed",
  "In Production": "inProduction",
  "Quality Check": "qualityCheck",
  Finalising: "finalising",
  Delivered: "delivered",
  Failed: "failed",
  Cancelled: "cancelled",
  "Delivered — with quality issues": "deliveredWithIssues",
  "Order failed quality check": "failedQualityCheck",
};

export function translateBuyerStatus(status: string, t: (key: string) => string): string {
  const key = BUYER_STATUS_KEYS[status];
  return key ? t(`buyerStatus.${key}`) : status;
}

export function localizedExplanation(
  explanation: string | null,
  explanations: Record<string, string>,
  language: string
): string | null {
  return explanations[language] ?? explanation;
}
