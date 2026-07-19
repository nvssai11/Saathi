import { useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { useWorkshopData } from "../context/WorkshopDataContext";
import { deadlineUrgency } from "../utils/format";
import { getLastSeenAt } from "../utils/notificationSeen";

const ACTIONABLE = new Set(["ASSIGNED", "IN_PRODUCTION", "DELIVERED"]);

export function useWorkshopBadges() {
  const { token } = useAuth();
  const { sublots, notifications } = useWorkshopData();

  return useMemo(() => {
    const urgentSublots = (sublots ?? []).filter(
      (s) => ACTIONABLE.has(s.status) && deadlineUrgency(s.deadline) !== "normal"
    ).length;
    const lastSeenAt = token ? getLastSeenAt(token) : 0;
    const newNotifications = (notifications ?? []).filter(
      (n) => new Date(n.created_at).getTime() > lastSeenAt
    ).length;
    return { urgentSublots, newNotifications };
  }, [sublots, notifications, token]);
}
