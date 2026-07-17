import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { NotificationItem } from "../../api/client";
import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import NotificationRow from "../../components/NotificationRow";
import { PackageIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { dayLabel } from "../../utils/format";

export default function Notifications() {
  const navigate = useNavigate();
  const { notifications: items, notificationsError: error, refresh } = useWorkshopData();

  const groups = useMemo(() => {
    const out: { label: string; items: NotificationItem[] }[] = [];
    for (const item of items ?? []) {
      const label = dayLabel(item.created_at);
      const current = out[out.length - 1];
      if (current && current.label === label) {
        current.items.push(item);
      } else {
        out.push({ label, items: [item] });
      }
    }
    return out;
  }, [items]);

  return (
    <Layout>
      <div className="page page-narrow">
        <h1>Notifications</h1>
        <p className="muted">Sub-lots assigned to you by the allocation engine, most recent first.</p>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
              Retry
            </button>
          </div>
        )}
        {items === null && !error && <SkeletonCard lines={3} />}

        {items !== null && items.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>No allocations yet.</p>
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
