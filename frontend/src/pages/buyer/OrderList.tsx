import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { ApiError, OrderListItem, buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import { PackageIcon } from "../../components/icons";
import { SkeletonTable } from "../../components/Skeleton";
import { formatProductType } from "../../data/catalog";

const TERMINAL_STATUSES = new Set(["Delivered", "Failed", "Cancelled"]);

export default function OrderList() {
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
      setError(err instanceof ApiError ? err.message : "Could not load orders.");
    }
  }, [token]);

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
          <h1>Your orders</h1>
          <Link to="/buyer/new-order" className="btn btn-accent">
            + Place order
          </Link>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              Retry
            </button>
          </div>
        )}

        {orders === null && !error && <SkeletonTable cols={5} />}

        {orders !== null && orders.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>No orders yet.</p>
            <Link to="/buyer/new-order" className="btn btn-primary">
              Place your first order
            </Link>
          </div>
        )}

        {orders !== null && orders.length > 0 && (
          <div className="card table-card">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Product</th>
                    <th>Qty</th>
                    <th>Deadline</th>
                    <th>Status</th>
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
                          {o.status}
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
