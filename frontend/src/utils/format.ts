export function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function dayLabel(iso: string): string {
  const startOfDay = (d: Date) => {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x.getTime();
  };
  const diffDays = Math.round((startOfDay(new Date()) - startOfDay(new Date(iso))) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
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

export function deadlineLabel(deadline: string): string {
  const days = daysUntil(deadline);
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  return `${days}d left`;
}

export type CapacityUrgency = "critical" | "warning" | "normal";

export function capacityUrgency(servingCapacity: number, availableQty: number): CapacityUrgency {
  if (servingCapacity <= 0) return "critical";
  if (availableQty > 0 && servingCapacity / availableQty <= 0.2) return "warning";
  return "normal";
}
