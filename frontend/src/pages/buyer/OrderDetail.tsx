import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { ApiError, OrderQuoteResponse, OrderStatusResponse, buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import OrderStepper from "../../components/OrderStepper";
import { SkeletonCard } from "../../components/Skeleton";
import PhotoPicker from "../../components/PhotoPicker";
import { formatProductType } from "../../data/catalog";
import { compressForRetry } from "../../utils/imageCompress";

const TERMINAL = new Set(["Delivered", "Failed", "Cancelled"]);
const SHORT_CIRCUITED = new Set(["Failed", "Cancelled"]);
const CANCELLABLE = new Set(["Received", "Confirmed"]);

export default function OrderDetail() {
  const { token } = useAuth();
  const { orderId } = useParams<{ orderId: string }>();
  const id = Number(orderId);

  const [order, setOrder] = useState<OrderStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [defectQty, setDefectQty] = useState(1);
  const [description, setDescription] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [flagging, setFlagging] = useState(false);
  const [flagMessage, setFlagMessage] = useState<string | null>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const [quote, setQuote] = useState<OrderQuoteResponse | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);

  const load = useCallback(async () => {
    if (!token || !id) return;
    try {
      const res = await buyerApi.getOrder(token, id);
      setOrder(res);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load order.");
    }
  }, [token, id]);

  useEffect(() => {
    setOrder(null);
    setQuote(null);
    setQuoteError(null);
    setFlagMessage(null);
  }, [id]);

  useEffect(() => {
    load();
    const interval = setInterval(() => {
      setOrder((current) => {
        if (current && TERMINAL.has(current.status)) return current;
        load();
        return current;
      });
    }, 4000);
    return () => clearInterval(interval);
  }, [load]);

  async function handleCancel() {
    if (!token || !order) return;
    setCancelling(true);
    try {
      await buyerApi.cancelOrder(token, order.order_id);
      setConfirmCancel(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not cancel order.");
    } finally {
      setCancelling(false);
    }
  }

  async function handleShowQuote() {
    if (!token || !order) return;
    setQuoteLoading(true);
    setQuoteError(null);
    try {
      const res = await buyerApi.getQuote(token, order.order_id);
      setQuote(res);
    } catch (err) {
      setQuoteError(err instanceof ApiError ? err.message : "Could not load quote.");
    } finally {
      setQuoteLoading(false);
    }
  }

  async function handleFlagDefect(e: FormEvent) {
    e.preventDefault();
    if (!token || !order || !photo) return;
    setFlagging(true);
    setFlagMessage(null);
    setUploadPct(0);
    try {
      let res;
      try {
        res = await buyerApi.flagDefect(token, order.order_id, photo, defectQty, description, setUploadPct);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 0) throw err;
        setFlagMessage("Connection struggled with the full-size photo — retrying with a smaller version…");
        setUploadPct(0);
        const smaller = await compressForRetry(photo);
        res = await buyerApi.flagDefect(token, order.order_id, smaller, defectQty, description, setUploadPct);
      }
      setFlagMessage(
        res.verification_status === "FAILED"
          ? "Defect confirmed — the responsible workshop's trust score has been updated."
          : res.verification_status === "NEEDS_HUMAN_REVIEW"
          ? "Couldn't automatically verify this one — flagged for manual review."
          : "Checked — no workshop-fault defect found against the spec."
      );
      setDescription("");
      setPhoto(null);
      await load();
    } catch (err) {
      setFlagMessage(err instanceof ApiError ? err.message : "Could not submit defect report.");
    } finally {
      setFlagging(false);
      setUploadPct(null);
    }
  }

  if (error && !order) {
    return (
      <Layout>
        <div className="page">
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              Retry
            </button>
          </div>
          <Link to="/buyer/orders">&larr; Back to orders</Link>
        </div>
      </Layout>
    );
  }

  if (!order) {
    return (
      <Layout>
        <div className="page">
          <SkeletonCard lines={4} />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="page">
        <Link to="/buyer/orders" className="back-link">
          &larr; All orders
        </Link>

        <div className="page-header">
          <h1>Order #{order.order_id}</h1>
          <span className={`status-pill status-${order.status.toLowerCase().replace(/\s+/g, "-")}`}>
            {order.status}
          </span>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              Retry
            </button>
          </div>
        )}

        <div className="card">
          {SHORT_CIRCUITED.has(order.status) ? (
            <div className={`banner ${order.status === "Failed" ? "banner-error" : "banner-info"}`}>
              {order.status === "Failed"
                ? "This order could not be fulfilled. Contact support for details."
                : "This order was cancelled and reserved capacity has been released."}
            </div>
          ) : (
            <OrderStepper status={order.status} />
          )}
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">{order.total_qty}</span>
              <span className="stat-label">Total qty</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_total}</span>
              <span className="stat-label">Sub-lots</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_delivered}</span>
              <span className="stat-label">Delivered</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_verified}</span>
              <span className="stat-label">Verified</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_failed}</span>
              <span className="stat-label">Failed</span>
            </div>
          </div>
        </div>

        <div className="action-row">
          <button className="btn btn-secondary" onClick={handleShowQuote}>
            {quoteLoading ? "Loading quote…" : "View quote"}
          </button>
          {order.status === "Delivered" && (
            <Link to={`/buyer/orders/${order.order_id}/invoice`} className="btn btn-secondary">
              View invoice
            </Link>
          )}
          {CANCELLABLE.has(order.status) && !confirmCancel && (
            <button className="btn btn-danger" onClick={() => setConfirmCancel(true)}>
              Cancel order
            </button>
          )}
        </div>

        {confirmCancel && (
          <div className="confirm-box">
            <p>Cancel this order? Reserved workshop capacity will be released and this can't be undone.</p>
            <div className="confirm-actions">
              <button className="btn btn-danger btn-sm" onClick={handleCancel} disabled={cancelling}>
                {cancelling ? "Cancelling…" : "Yes, cancel order"}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setConfirmCancel(false)}
                disabled={cancelling}
              >
                No, keep order
              </button>
            </div>
          </div>
        )}

        {quoteError && (
          <div className="banner banner-error">
            <span>{quoteError}</span>
            <button className="btn-retry" onClick={handleShowQuote}>
              Retry
            </button>
          </div>
        )}

        {quote && (
          <div className="card">
            <h2>Estimated quote</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Qty</th>
                    <th>Unit price</th>
                    <th>Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {quote.line_items.map((item) => (
                    <tr key={item.product_type}>
                      <td>{formatProductType(item.product_type)}</td>
                      <td>{item.total_qty}</td>
                      <td>₹{item.unit_price}</td>
                      <td>₹{item.subtotal}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="quote-summary">
              <span>Platform fee: ₹{quote.platform_fee}</span>
              <span className="quote-total">Total: ₹{quote.total}</span>
            </div>
          </div>
        )}

        {order.sublots_total > 0 &&
          ((order.sublots_delivered === order.sublots_total && !TERMINAL.has(order.status)) ||
            order.status === "Delivered") && (
          <div className="card form">
            <h2>Report a defect</h2>
            <p className="muted">
              We flag defects at the order level — individual workshops are never identified.
            </p>
            {order.status === "Delivered" && (
              <p className="muted">
                This order is already marked delivered — a defect reported now still runs a real
                quality check and affects the responsible workshop's trust score.
              </p>
            )}
            <form onSubmit={handleFlagDefect}>
              {flagMessage && <div className="banner banner-info">{flagMessage}</div>}
              <div className="field-row">
                <div className="field">
                  <label htmlFor="defect_qty">Defective quantity</label>
                  <input
                    id="defect_qty"
                    type="number"
                    min={1}
                    required
                    value={defectQty}
                    onChange={(e) => setDefectQty(Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label htmlFor="photo">Photo evidence</label>
                  <PhotoPicker id="photo" photo={photo} onChange={setPhoto} required />
                </div>
              </div>
              <div className="field">
                <label htmlFor="description">Description</label>
                <textarea
                  id="description"
                  required
                  maxLength={1000}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What's wrong with the delivered goods?"
                />
              </div>
              {flagging && (
                <div className="upload-progress" role="progressbar" aria-valuenow={uploadPct ?? 0}>
                  <div className="upload-progress-fill" style={{ width: `${uploadPct ?? 0}%` }} />
                </div>
              )}
              <button type="submit" className="btn btn-primary" disabled={flagging}>
                {flagging
                  ? uploadPct !== null && uploadPct < 100
                    ? `Uploading photo… ${uploadPct}%`
                    : "Checking photo…"
                  : "Submit defect report"}
              </button>
            </form>
          </div>
        )}
      </div>
    </Layout>
  );
}
