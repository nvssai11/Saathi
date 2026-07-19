import { useTranslation } from "react-i18next";
import { NotificationItem } from "../api/client";
import { catalogItemFor, formatProductType } from "../data/catalog";
import { timeAgo } from "../utils/format";
import { BellIcon } from "./icons";

interface NotificationRowProps {
  item: NotificationItem;
  lastSeenAt: number;
  onClick: () => void;
}

export default function NotificationRow({ item, lastSeenAt, onClick }: NotificationRowProps) {
  const { t } = useTranslation();
  const catalogEntry = catalogItemFor(item.product_type);
  const isNew = new Date(item.created_at).getTime() > lastSeenAt;

  return (
    <button type="button" className={`notif-item ${isNew ? "is-new" : ""}`} onClick={onClick}>
      <div className="notif-icon">{catalogEntry ? <span>{catalogEntry.emoji}</span> : <BellIcon />}</div>
      <div className="notif-body">
        <div className="notif-title">
          {t("notifications.unitsAssigned", {
            qty: item.qty_assigned,
            product: formatProductType(item.product_type),
          })}
          {isNew && <span className="notif-new-badge">{t("notifications.new")}</span>}
        </div>
        <div className="notif-meta">
          {t("notifications.meta", { orderId: item.order_id, sublotId: item.sublot_id })}
        </div>
      </div>
      <div className="notif-time">{timeAgo(item.created_at, t)}</div>
    </button>
  );
}
