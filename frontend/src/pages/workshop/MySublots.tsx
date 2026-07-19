import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, SubLotSummary, workshopApi } from "../../api/client";
import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import InfoTip from "../../components/InfoTip";
import { PackageIcon, ClockIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { catalogItemFor, formatProductType } from "../../data/catalog";
import {
  capacityUrgency,
  deadlineLabel,
  deadlineUrgency,
  daysUntil,
  localizedExplanation,
} from "../../utils/format";

const DELIVERABLE = new Set(["ASSIGNED", "IN_PRODUCTION"]);
const ACTIONABLE = new Set(["ASSIGNED", "IN_PRODUCTION", "DELIVERED"]);
const GRADE_TONE: Record<string, string> = { A: "good", B: "good", C: "warning", D: "critical" };

export default function MySublots() {
  const { t, i18n } = useTranslation();
  const { token } = useAuth();
  const navigate = useNavigate();
  const { sublots, sublotsError: error, trust, capacity, refresh } = useWorkshopData();
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [openDeliverId, setOpenDeliverId] = useState<number | null>(null);
  const [confirmDeliverId, setConfirmDeliverId] = useState<number | null>(null);
  const [deliveredQty, setDeliveredQty] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [rowMessage, setRowMessage] = useState<{ id: number; text: string } | null>(null);

  function flashSuccess(message: string) {
    setSuccessMessage(message);
    setTimeout(() => setSuccessMessage(null), 3000);
  }

  const [searchParams] = useSearchParams();
  const highlightId = Number(searchParams.get("highlight")) || null;
  const [flashId, setFlashId] = useState<number | null>(null);
  const didHighlightRef = useRef(false);
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    if (didHighlightRef.current || !highlightId || !sublots) return;
    const el = cardRefs.current[highlightId];
    if (!el) return;
    didHighlightRef.current = true;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setFlashId(highlightId);
    const timer = setTimeout(() => setFlashId(null), 2500);
    return () => clearTimeout(timer);
  }, [highlightId, sublots]);

  const { actionable, history } = useMemo(() => {
    const list = sublots ?? [];
    const actionable = list
      .filter((s) => ACTIONABLE.has(s.status))
      .sort((a, b) => daysUntil(a.deadline) - daysUntil(b.deadline));
    const history = list.filter((s) => !ACTIONABLE.has(s.status));
    return { actionable, history };
  }, [sublots]);

  const urgentCount = actionable.filter((s) => deadlineUrgency(s.deadline) !== "normal").length;

  const capacityWarnings = useMemo(
    () => (capacity ?? []).filter((c) => capacityUrgency(c.serving_capacity, c.available_qty) !== "normal"),
    [capacity]
  );

  function openDeliver(s: SubLotSummary) {
    setOpenDeliverId(s.sublot_id);
    setConfirmDeliverId(null);
    setDeliveredQty(s.qty_assigned);
    setRowMessage(null);
  }

  function closeForm() {
    setOpenDeliverId(null);
  }

  async function submitStartProduction(sublotId: number) {
    if (!token) return;
    setBusy(true);
    try {
      await workshopApi.startProduction(token, sublotId);
      refresh();
      flashSuccess(t("sublots.startedProduction"));
    } catch (err) {
      setRowMessage({
        id: sublotId,
        text: err instanceof ApiError ? err.message : t("sublots.startProductionError"),
      });
    } finally {
      setBusy(false);
    }
  }

  async function submitDeliver(sublotId: number, qty: number) {
    if (!token) return;
    setBusy(true);
    try {
      await workshopApi.markDelivered(token, sublotId, qty);
      setOpenDeliverId(null);
      setConfirmDeliverId(null);
      refresh();
      flashSuccess(t("sublots.markedDelivered"));
    } catch (err) {
      setRowMessage({
        id: sublotId,
        text: err instanceof ApiError ? err.message : t("sublots.deliverError"),
      });
    } finally {
      setBusy(false);
    }
  }

  function SublotCard({ s, compact }: { s: SubLotSummary; compact?: boolean }) {
    const item = catalogItemFor(s.product_type);
    const urgency = deadlineUrgency(s.deadline);
    const formOpen = openDeliverId === s.sublot_id;

    return (
      <div
        ref={(el) => {
          cardRefs.current[s.sublot_id] = el;
        }}
        className={`sublot-card ${compact ? "is-compact" : ""} ${flashId === s.sublot_id ? "is-highlighted" : ""}`}
      >
        <div className="sublot-swatch" style={{ background: item?.swatch ?? "var(--neutral-bg)" }}>
          <span>{item?.emoji ?? "📦"}</span>
        </div>
        <div className="sublot-body">
          <div className="sublot-top">
            <div>
              <div className="sublot-name">{formatProductType(s.product_type)}</div>
              <div className="sublot-ids">
                {t("sublots.orderSublotIds", { orderId: s.order_id, sublotId: s.sublot_id })}
              </div>
            </div>
            <span className={`status-pill status-${s.status.toLowerCase()}`}>
              {t(`status.${s.status}`, s.status)}
            </span>
          </div>

          <div className="sublot-meta-row">
            <span>
              {t("sublots.qty")} <strong>{s.qty_assigned}</strong>
              {s.delivered_qty !== null ? ` ${t("sublots.deliveredCount", { count: s.delivered_qty })}` : ""}
            </span>
            {!compact && (
              <span
                className={`deadline-tag ${
                  urgency === "critical" ? "tone-critical" : urgency === "warning" ? "tone-warning" : ""
                }`}
              >
                <ClockIcon />
                {deadlineLabel(s.deadline, t)}
              </span>
            )}
            {compact && <span>{s.deadline}</span>}
          </div>

          {compact && s.status === "FAILED" && s.explanation && (
            <p className="muted">
              {t("sublots.failureReason", {
                reason: localizedExplanation(s.explanation, s.explanations, i18n.language),
              })}
            </p>
          )}
          {compact && s.status === "NEEDS_HUMAN_REVIEW" && (
            <p className="muted">
              {s.explanation
                ? t("sublots.failureReason", {
                    reason: localizedExplanation(s.explanation, s.explanations, i18n.language),
                  })
                : t("sublots.reviewPending")}
            </p>
          )}

          {!compact && (
            <div className="sublot-actions">
              {s.status === "ASSIGNED" && (
                <button
                  className="btn btn-primary btn-sm"
                  disabled={busy}
                  onClick={() => submitStartProduction(s.sublot_id)}
                >
                  {t("sublots.startProduction")}
                </button>
              )}
              {DELIVERABLE.has(s.status) && !formOpen && confirmDeliverId !== s.sublot_id && (
                <>
                  <button
                    className={`btn ${s.status === "IN_PRODUCTION" ? "btn-primary" : "btn-secondary"} btn-sm`}
                    disabled={busy}
                    onClick={() => setConfirmDeliverId(s.sublot_id)}
                  >
                    {t("sublots.deliverAll", { qty: s.qty_assigned })}
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => openDeliver(s)}>
                    {t("sublots.fewer")}
                  </button>
                </>
              )}
              {s.status === "DELIVERED" && (
                <span className="muted">{t("sublots.deliveredAwaiting")}</span>
              )}
              {rowMessage?.id === s.sublot_id && !formOpen && confirmDeliverId !== s.sublot_id && (
                <span className="inline-error">{rowMessage.text}</span>
              )}
            </div>
          )}

          {confirmDeliverId === s.sublot_id && (
            <div className="confirm-box">
              <p>{t("sublots.confirmDeliverPrompt", { qty: s.qty_assigned })}</p>
              <div className="confirm-actions">
                <button
                  className="btn btn-primary btn-sm"
                  disabled={busy}
                  onClick={() => submitDeliver(s.sublot_id, s.qty_assigned)}
                >
                  {t("sublots.yesMarkDelivered")}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDeliverId(null)}>
                  {t("sublots.noKeepAsIs")}
                </button>
              </div>
              {rowMessage?.id === s.sublot_id && (
                <span className="inline-error">{rowMessage.text}</span>
              )}
            </div>
          )}

          {formOpen && DELIVERABLE.has(s.status) && (
            <div className="sublot-inline-form">
              <div className="inline-form">
                <label>
                  {t("sublots.howManyDelivered", { qty: s.qty_assigned })}
                  <input
                    type="number"
                    min={0}
                    max={s.qty_assigned}
                    value={deliveredQty}
                    onChange={(e) => setDeliveredQty(Number(e.target.value))}
                  />
                </label>
                <button
                  className="btn btn-primary btn-sm"
                  disabled={busy}
                  onClick={() => submitDeliver(s.sublot_id, deliveredQty)}
                >
                  {t("sublots.confirm")}
                </button>
                <button className="btn btn-ghost btn-sm" onClick={closeForm}>
                  {t("common.cancel")}
                </button>
                {rowMessage?.id === s.sublot_id && (
                  <span className="inline-error">{rowMessage.text}</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <Layout>
      <div className="page">
        <h1>{t("sublots.title")}</h1>
        <p className="muted">{t("sublots.subtitle")}</p>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
              {t("common.retry")}
            </button>
          </div>
        )}
        {successMessage && (
          <div className="banner banner-info">
            <span>{successMessage}</span>
          </div>
        )}

        {(trust || capacityWarnings.length > 0) && (
          <div className="card home-trust-row">
            <div className="summary-inline">
              {trust && (
                <span className="summary-inline-item">
                  <span className={`status-pill status-${GRADE_TONE[trust.grade] ?? "neutral"}`}>
                    {t("sublots.trustScoreLabel", {
                      grade: trust.grade,
                      pct: (trust.score * 100).toFixed(0),
                    })}
                  </span>
                  <InfoTip text={t("sublots.trustTooltip")} />
                </span>
              )}
              {capacityWarnings.length > 0 && (
                <span className="capacity-tag tone-warning">
                  {t("sublots.lowCapacityWarning", { count: capacityWarnings.length })}
                </span>
              )}
            </div>
            <div className="summary-inline">
              {capacityWarnings.length > 0 && (
                <button className="btn btn-ghost btn-sm" onClick={() => navigate("/my-workshop/capacity")}>
                  {t("sublots.viewCapacity")}
                </button>
              )}
              {trust && (
                <button className="btn btn-ghost btn-sm" onClick={() => navigate("/my-workshop/trust")}>
                  {t("sublots.viewTrustScore")}
                </button>
              )}
            </div>
          </div>
        )}

        {sublots === null && !error && (
          <>
            <SkeletonCard lines={2} />
            <SkeletonCard lines={2} />
          </>
        )}

        {sublots !== null && sublots.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>{t("sublots.noneYet")}</p>
          </div>
        )}

        {sublots !== null && actionable.length > 0 && (
          <div className={`wp-section ${urgentCount > 0 ? "is-urgent" : ""}`}>
            <div className="wp-section-head">
              <h2 className="wp-section-title">{t("sublots.needsAction")}</h2>
              <span className="wp-section-count">{actionable.length}</span>
            </div>
            {actionable.map((s) => (
              <SublotCard key={s.sublot_id} s={s} />
            ))}
          </div>
        )}

        {sublots !== null && history.length > 0 && (
          <div className="wp-section">
            <div className="wp-section-head">
              <h2 className="wp-section-title">{t("sublots.completed")}</h2>
              <span className="wp-section-count">{history.length}</span>
            </div>
            {history.map((s) => (
              <SublotCard key={s.sublot_id} s={s} compact />
            ))}
          </div>
        )}
      </div>
    </Layout>
  );
}
