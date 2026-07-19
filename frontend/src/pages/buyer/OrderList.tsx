import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, OrderListItem, buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import { PackageIcon } from "../../components/icons";
import { SkeletonTable } from "../../components/Skeleton";
import { formatProductType } from "../../data/catalog";
import { translateBuyerStatus } from "../../utils/format";

const TERMINAL_STATUSES = new Set(["Delivered", "Failed", "Cancelled"]);

export default function OrderList() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const [orders, setOrders] = useState<OrderListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const res = await buyerApi.listOrders(token, 1, 50);
      setOrders(res.orders);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("orderList.loadError"));
    }
  }, [token, t]);

  useEffect(() => {
    load();
    const interval = setInterval(() => {
      setOrders((current) => {
        const allTerminal = current !== null && current.every((o) => TERMINAL_STATUSES.has(o.status));
        if (!allTerminal) load();
        return current;
      });
    }, 4000);
    return () => clearInterval(interval);
  }, [load]);

  return (
    <Layout>
      <div className="page">
        <div className="page-header">
          <h1>{t("orderList.title")}</h1>
          <Link to="/buyer/new-order" className="btn btn-accent">
            {t("orderList.placeOrder")}
          </Link>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              {t("common.retry")}
            </button>
          </div>
        )}

        {orders === null && !error && <SkeletonTable cols={5} />}

        {orders !== null && orders.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>{t("orderList.noOrders")}</p>
            <Link to="/buyer/new-order" className="btn btn-primary">
              {t("orderList.placeFirstOrder")}
            </Link>
          </div>
        )}

        {orders !== null && orders.length > 0 && (
          <div className="card table-card">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t("orderList.colOrder")}</th>
                    <th>{t("orderList.colProduct")}</th>
                    <th>{t("orderList.colQty")}</th>
                    <th>{t("orderList.colDeadline")}</th>
                    <th>{t("orderList.colStatus")}</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.order_id}>
                      <td>
                        <Link to={`/buyer/orders/${o.order_id}`}>#{o.order_id}</Link>
                      </td>
                      <td>{formatProductType(o.product_type)}</td>
                      <td>{o.total_qty}</td>
                      <td>{o.deadline}</td>
                      <td>
                        <span className={`status-pill status-${o.status.toLowerCase().replace(/\s+/g, "-")}`}>
                          {translateBuyerStatus(o.status, t)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
