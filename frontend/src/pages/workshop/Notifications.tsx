import { useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { NotificationItem } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import NotificationRow from "../../components/NotificationRow";
import { PackageIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { dayLabel } from "../../utils/format";
import { getLastSeenAt, markNotificationsSeen } from "../../utils/notificationSeen";

export default function Notifications() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { token } = useAuth();
  const { notifications: items, notificationsError: error, refresh } = useWorkshopData();

  const priorSeenAtRef = useRef<number | null>(null);
  if (priorSeenAtRef.current === null) {
    priorSeenAtRef.current = token ? getLastSeenAt(token) : 0;
  }

  useEffect(() => {
    if (token) markNotificationsSeen(token);
  }, [token, items]);

  const groups = useMemo(() => {
    const out: { label: string; items: NotificationItem[] }[] = [];
    for (const item of items ?? []) {
      const label = dayLabel(item.created_at, t);
      const current = out[out.length - 1];
      if (current && current.label === label) {
        current.items.push(item);
      } else {
        out.push({ label, items: [item] });
      }
    }
    return out;
  }, [items, t]);

  return (
    <Layout>
      <div className="page page-narrow">
        <h1>{t("notifications.title")}</h1>
        <p className="muted">{t("notifications.subtitle")}</p>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
              {t("common.retry")}
            </button>
          </div>
        )}
        {items === null && !error && <SkeletonCard lines={3} />}

        {items !== null && items.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>{t("notifications.noneYet")}</p>
          </div>
        )}

        {items !== null && items.length > 0 && (
          <div className="card">
            {groups.map((g) => (
              <div className="notif-day-group" key={g.label}>
                <div className="notif-day-label">{g.label}</div>
                <div className="notif-list">
                  {g.items.map((n) => (
                    <NotificationRow
                      key={n.notification_id}
                      item={n}
                      lastSeenAt={priorSeenAtRef.current ?? 0}
                      onClick={() => navigate(`/my-workshop/sublots?highlight=${n.sublot_id}`)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Layout>
  );
}
