import { NotificationItem } from "../api/client";
import { catalogItemFor, formatProductType } from "../data/catalog";
import { timeAgo } from "../utils/format";
import { BellIcon } from "./icons";

const NEW_WINDOW_MS = 60 * 60 * 1000;

interface NotificationRowProps {
  item: NotificationItem;
  onClick: () => void;
}

export default function NotificationRow({ item, onClick }: NotificationRowProps) {
  const catalogEntry = catalogItemFor(item.product_type);
  const isNew = Date.now() - new Date(item.created_at).getTime() < NEW_WINDOW_MS;

  return (
    <button type="button" className={`notif-item ${isNew ? "is-new" : ""}`} onClick={onClick}>
      <div className="notif-icon">{catalogEntry ? <span>{catalogEntry.emoji}</span> : <BellIcon />}</div>
      <div className="notif-body">
        <div className="notif-title">
          {item.qty_assigned} units of {formatProductType(item.product_type)} assigned
          {isNew && <span className="notif-new-badge">New</span>}
        </div>
        <div className="notif-meta">
          Order #{item.order_id} · Sub-lot #{item.sublot_id} · View in My sub-lots
        </div>
      </div>
      <div className="notif-time">{timeAgo(item.created_at)}</div>
    </button>
  );
}
