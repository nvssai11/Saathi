import { ReactNode, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useAuth } from "./AuthContext";
import {
  ApiError,
  NotificationItem,
  SubLotSummary,
  TrustScoreResponse,
  WorkshopCapacityListItem,
  workshopApi,
} from "../api/client";

const POLL_INTERVAL_MS = 6000;

interface WorkshopDataState {
  sublots: SubLotSummary[] | null;
  sublotsError: string | null;
  capacity: WorkshopCapacityListItem[] | null;
  capacityError: string | null;
  notifications: NotificationItem[] | null;
  notificationsError: string | null;
  trust: TrustScoreResponse | null;
  trustError: string | null;
  refresh: () => void;
}

const WorkshopDataContext = createContext<WorkshopDataState | undefined>(undefined);

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof ApiError ? reason.message : fallback;
}

export function WorkshopDataProvider({ children }: { children: ReactNode }) {
  const { role, token } = useAuth();
  const [sublots, setSublots] = useState<SubLotSummary[] | null>(null);
  const [sublotsError, setSublotsError] = useState<string | null>(null);
  const [capacity, setCapacity] = useState<WorkshopCapacityListItem[] | null>(null);
  const [capacityError, setCapacityError] = useState<string | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[] | null>(null);
  const [notificationsError, setNotificationsError] = useState<string | null>(null);
  const [trust, setTrust] = useState<TrustScoreResponse | null>(null);
  const [trustError, setTrustError] = useState<string | null>(null);

  const loadRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (role !== "workshop" || !token) return;
    let cancelled = false;

    async function load() {
      const results = await Promise.allSettled([
        workshopApi.listSublots(token as string),
        workshopApi.listCapacity(token as string),
        workshopApi.listNotifications(token as string),
        workshopApi.getTrustScore(token as string),
      ]);
      if (cancelled) return;

      if (results[0].status === "fulfilled") {
        setSublots(results[0].value);
        setSublotsError(null);
      } else {
        setSublotsError(errorMessage(results[0].reason, "Could not load sub-lots."));
      }
      if (results[1].status === "fulfilled") {
        setCapacity(results[1].value);
        setCapacityError(null);
      } else {
        setCapacityError(errorMessage(results[1].reason, "Could not load capacity."));
      }
      if (results[2].status === "fulfilled") {
        setNotifications(results[2].value);
        setNotificationsError(null);
      } else {
        setNotificationsError(errorMessage(results[2].reason, "Could not load notifications."));
      }
      if (results[3].status === "fulfilled") {
        setTrust(results[3].value);
        setTrustError(null);
      } else {
        setTrustError(errorMessage(results[3].reason, "Could not load trust score."));
      }
    }

    loadRef.current = load;
    load();

    let interval: ReturnType<typeof setInterval> | null = null;
    function startInterval() {
      if (interval) return;
      interval = setInterval(load, POLL_INTERVAL_MS);
    }
    function stopInterval() {
      if (!interval) return;
      clearInterval(interval);
      interval = null;
    }
    function handleVisibilityChange() {
      if (document.hidden) {
        stopInterval();
      } else {
        load();
        startInterval();
      }
    }

    if (!document.hidden) startInterval();
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      cancelled = true;
      stopInterval();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [role, token]);

  const refresh = useCallback(() => {
    loadRef.current();
  }, []);

  return (
    <WorkshopDataContext.Provider
      value={{
        sublots,
        sublotsError,
        capacity,
        capacityError,
        notifications,
        notificationsError,
        trust,
        trustError,
        refresh,
      }}
    >
      {children}
    </WorkshopDataContext.Provider>
  );
}

export function useWorkshopData(): WorkshopDataState {
  const ctx = useContext(WorkshopDataContext);
  if (!ctx) throw new Error("useWorkshopData must be used within WorkshopDataProvider");
  return ctx;
}
