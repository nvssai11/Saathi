import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { adminApi } from "../api/client";

const POLL_INTERVAL_MS = 8000;

export function useAdminReviewCount(): number {
  const { role, token } = useAuth();
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (role !== "admin" || !token) return;
    let cancelled = false;

    async function load() {
      try {
        const items = await adminApi.listNeedsReview(token!);
        if (!cancelled) setCount(items.length);
      } catch {}
    }

    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [role, token]);

  return count;
}
