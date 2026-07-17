import { useMemo } from "react";
import { useWorkshopData } from "../context/WorkshopDataContext";
import { deadlineUrgency } from "../utils/format";

const ACTIONABLE = new Set(["ASSIGNED", "IN_PRODUCTION", "DELIVERED"]);
const NEW_WINDOW_MS = 60 * 60 * 1000;

export function useWorkshopBadges() {
  const { sublots, notifications } = useWorkshopData();

  return useMemo(() => {
    const urgentSublots = (sublots ?? []).filter(
      (s) => ACTIONABLE.has(s.status) && deadlineUrgency(s.deadline) !== "normal"
    ).length;
    const newNotifications = (notifications ?? []).filter(
      (n) => Date.now() - new Date(n.created_at).getTime() < NEW_WINDOW_MS
    ).length;
    return { urgentSublots, newNotifications };
  }, [sublots, notifications]);
}
