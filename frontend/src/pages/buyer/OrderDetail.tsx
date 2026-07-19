import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, OrderQuoteResponse, OrderStatusResponse, buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import OrderStepper from "../../components/OrderStepper";
import { SkeletonCard } from "../../components/Skeleton";
import PhotoPicker from "../../components/PhotoPicker";
import { formatProductType } from "../../data/catalog";
import { compressForRetry } from "../../utils/imageCompress";
import { localizedExplanation, translateBuyerStatus } from "../../utils/format";

const TERMINAL = new Set([
  "Delivered", "Delivered — with quality issues", "Order failed quality check", "Failed", "Cancelled",
]);
const SHORT_CIRCUITED = new Set(["Failed", "Cancelled", "Order failed quality check"]);
const CANCELLABLE = new Set(["Received", "Confirmed"]);
const CLOSED_LABELS = new Set(["Delivered", "Delivered — with quality issues", "Order failed quality check"]);
const STILL_FLAGGABLE_AFTER_CLOSE = new Set(["Delivered", "Delivered — with quality issues"]);

export default function OrderDetail() {
  const { t, i18n } = useTranslation();
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
  const [flagExplanation, setFlagExplanation] = useState<string | null>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);

  const [cancelling, setCancelling] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const [quote, setQuote] = useState<OrderQuoteResponse | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);
  const [quoteLoading, setQuoteLoading] = useState(false);

  const [defectPhotoUrl, setDefectPhotoUrl] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !id) return;
    try {
      const res = await buyerApi.getOrder(token, id);
      setOrder(res);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("orderDetail.loadError"));
    }
  }, [token, id, t]);

  useEffect(() => {
    setOrder(null);
    setQuote(null);
    setQuoteError(null);
    setFlagMessage(null);
    setDefectPhotoUrl(null);
  }, [id]);

  useEffect(() => {
    if (!token || !id || !order?.has_defect_photo) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    buyerApi
      .getDefectPhotoUrl(token, id)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setDefectPhotoUrl(url);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [token, id, order?.has_defect_photo]);

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
      setError(err instanceof ApiError ? err.message : t("orderDetail.cancelError"));
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
      setQuoteError(err instanceof ApiError ? err.message : t("orderDetail.quoteError"));
    } finally {
      setQuoteLoading(false);
    }
  }

  async function handleFlagDefect(e: FormEvent) {
    e.preventDefault();
    if (!token || !order || !photo) return;
    setFlagging(true);
    setFlagMessage(null);
    setFlagExplanation(null);
    setUploadPct(0);
    try {
      let res;
      try {
        res = await buyerApi.flagDefect(token, order.order_id, photo, defectQty, description, setUploadPct);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 0) throw err;
        setFlagMessage(t("orderDetail.connectionRetrying"));
        setUploadPct(0);
        const smaller = await compressForRetry(photo);
        res = await buyerApi.flagDefect(token, order.order_id, smaller, defectQty, description, setUploadPct);
      }
      setFlagMessage(
        res.verification_status === "FAILED"
          ? res.fault_party === "workshop"
            ? t("orderDetail.defectFailedWorkshopFaultMessage")
            : t("orderDetail.defectFailedMessage")
          : res.verification_status === "NEEDS_HUMAN_REVIEW"
          ? t("orderDetail.defectReviewMessage")
          : t("orderDetail.defectOkMessage")
      );
      setFlagExplanation(localizedExplanation(res.explanation, res.explanations, i18n.language));
      setDescription("");
      setPhoto(null);
      await load();
    } catch (err) {
      setFlagMessage(err instanceof ApiError ? err.message : t("orderDetail.submitError"));
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
              {t("common.retry")}
            </button>
          </div>
          <Link to="/buyer/orders">{t("orderDetail.backToOrders")}</Link>
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
          {t("orderDetail.backToOrders")}
        </Link>

        <div className="page-header">
          <h1>{t("orderDetail.orderTitle", { id: order.order_id })}</h1>
          <span className={`status-pill status-${order.status.toLowerCase().replace(/\s+/g, "-")}`}>
            {translateBuyerStatus(order.status, t)}
          </span>
        </div>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              {t("common.retry")}
            </button>
          </div>
        )}

        <div className="card">
          {SHORT_CIRCUITED.has(order.status) ? (
            <div
              className={`banner ${
                order.status === "Failed" || order.status === "Order failed quality check"
                  ? "banner-error"
                  : "banner-info"
              }`}
            >
              {order.status === "Order failed quality check"
                ? t("orderDetail.statQualityFailedBanner")
                : order.status === "Failed"
                ? t("orderDetail.statFailedBanner")
                : t("orderDetail.statCancelledBanner")}
            </div>
          ) : (
            <OrderStepper status={order.status} />
          )}
          <div className="stat-row">
            <div className="stat">
              <span className="stat-value">{order.total_qty}</span>
              <span className="stat-label">{t("orderDetail.statTotalQty")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_total}</span>
              <span className="stat-label">{t("orderDetail.statSublots")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_delivered}</span>
              <span className="stat-label">{t("orderDetail.statDelivered")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_verified}</span>
              <span className="stat-label">{t("orderDetail.statVerified")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{order.sublots_failed}</span>
              <span className="stat-label">{t("orderDetail.statFailed")}</span>
            </div>
          </div>
        </div>

        <div className="action-row">
          <button className="btn btn-secondary" onClick={handleShowQuote}>
            {quoteLoading ? t("orderDetail.loadingQuote") : t("orderDetail.viewQuote")}
          </button>
          {CLOSED_LABELS.has(order.status) && (
            <Link to={`/buyer/orders/${order.order_id}/invoice`} className="btn btn-secondary">
              {t("orderDetail.viewInvoice")}
            </Link>
          )}
          {CANCELLABLE.has(order.status) && !confirmCancel && (
            <button className="btn btn-danger" onClick={() => setConfirmCancel(true)}>
              {t("orderDetail.cancelOrder")}
            </button>
          )}
        </div>

        {confirmCancel && (
          <div className="confirm-box">
            <p>{t("orderDetail.confirmCancelText")}</p>
            <div className="confirm-actions">
              <button className="btn btn-danger btn-sm" onClick={handleCancel} disabled={cancelling}>
                {cancelling ? t("orderDetail.cancelling") : t("orderDetail.yesCancelOrder")}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setConfirmCancel(false)}
                disabled={cancelling}
              >
                {t("orderDetail.noKeepOrder")}
              </button>
            </div>
          </div>
        )}

        {quoteError && (
          <div className="banner banner-error">
            <span>{quoteError}</span>
            <button className="btn-retry" onClick={handleShowQuote}>
              {t("common.retry")}
            </button>
          </div>
        )}

        {quote && (
          <div className="card">
            <h2>{t("orderDetail.estimatedQuote")}</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t("orderDetail.colProduct")}</th>
                    <th>{t("orderDetail.colQty")}</th>
                    <th>{t("orderDetail.colUnitPrice")}</th>
                    <th>{t("orderDetail.colSubtotal")}</th>
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
              <span>{t("orderDetail.platformFee", { fee: quote.platform_fee })}</span>
              <span className="quote-total">{t("orderDetail.total", { total: quote.total })}</span>
            </div>
          </div>
        )}

        {order.has_defect_photo && defectPhotoUrl && (
          <div className="card">
            <h2>{t("orderDetail.yourSubmittedPhoto")}</h2>
            <p className="muted">{t("orderDetail.yourSubmittedPhotoSubtitle")}</p>
            <img
              src={defectPhotoUrl}
              alt={t("orderDetail.yourSubmittedPhoto")}
              className="defect-photo-preview"
            />
          </div>
        )}

        {order.sublots_total > 0 &&
          ((order.sublots_delivered === order.sublots_total && !TERMINAL.has(order.status)) ||
            STILL_FLAGGABLE_AFTER_CLOSE.has(order.status)) && (
          <div className="card form">
            <h2>{t("orderDetail.reportDefect")}</h2>
            <p className="muted">{t("orderDetail.reportDefectSubtitle")}</p>
            {STILL_FLAGGABLE_AFTER_CLOSE.has(order.status) && (
              <p className="muted">{t("orderDetail.deliveredNotice")}</p>
            )}
            <form onSubmit={handleFlagDefect}>
              {flagMessage && <div className="banner banner-info">{flagMessage}</div>}
              {flagExplanation && (
                <div className="ai-reasoning-card">
                  <span className="ai-reasoning-icon" aria-hidden="true">
                    🔍
                  </span>
                  <div>
                    <div className="ai-reasoning-label">{t("orderDetail.aiReasoningLabel")}</div>
                    <div className="ai-reasoning-text">&ldquo;{flagExplanation}&rdquo;</div>
                  </div>
                </div>
              )}
              <div className="field-row">
                <div className="field">
                  <label htmlFor="defect_qty">{t("orderDetail.defectiveQty")}</label>
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
                  <label htmlFor="photo">{t("orderDetail.photoEvidence")}</label>
                  <PhotoPicker id="photo" photo={photo} onChange={setPhoto} required />
                </div>
              </div>
              <div className="field">
                <label htmlFor="description">{t("orderDetail.description")}</label>
                <textarea
                  id="description"
                  required
                  maxLength={1000}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("orderDetail.descriptionPlaceholder")}
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
                    ? t("orderDetail.uploadingPhoto", { pct: uploadPct })
                    : t("orderDetail.checkingPhoto")
                  : t("orderDetail.submitDefectReport")}
              </button>
            </form>
          </div>
        )}
      </div>
    </Layout>
  );
}
