import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, OrderAllocationResponse, ReviewItem, adminApi } from "../../api/client";
import Layout from "../../components/Layout";
import { PackageIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { catalogItemFor, formatProductType } from "../../data/catalog";
import { localizedExplanation, timeAgo } from "../../utils/format";

const POLL_INTERVAL_MS = 8000;

export default function NeedsReview() {
  const { t, i18n } = useTranslation();
  const { token } = useAuth();
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowMessage, setRowMessage] = useState<{ id: number; text: string; ok: boolean } | null>(null);

  const [allocationOrderId, setAllocationOrderId] = useState("");
  const [allocation, setAllocation] = useState<OrderAllocationResponse | null>(null);
  const [allocationError, setAllocationError] = useState<string | null>(null);
  const [allocationBusy, setAllocationBusy] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setItems(await adminApi.listNeedsReview(token));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("admin.loadError"));
    }
  }, [token, t]);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  const { stuck, pendingDecision } = useMemo(() => {
    const list = items ?? [];
    return {
      stuck: list.filter((i) => i.status === "VERIFYING"),
      pendingDecision: list.filter((i) => i.status === "NEEDS_HUMAN_REVIEW"),
    };
  }, [items]);

  async function handleRetry(sublotId: number) {
    if (!token) return;
    setBusyId(sublotId);
    setRowMessage(null);
    try {
      const res = await adminApi.retryVerification(token, sublotId);
      setRowMessage({
        id: sublotId,
        ok: true,
        text: res.explanation
          ? t("admin.retriedMessageWithExplanation", { status: res.status, explanation: res.explanation })
          : t("admin.retriedMessage", { status: res.status }),
      });
      await load();
    } catch (err) {
      setRowMessage({
        id: sublotId,
        ok: false,
        text: err instanceof ApiError ? err.message : t("admin.retryFailed"),
      });
    } finally {
      setBusyId(null);
    }
  }

  async function handleCheckAllocation(e: FormEvent) {
    e.preventDefault();
    const orderId = Number(allocationOrderId);
    if (!token || !orderId) return;
    setAllocationBusy(true);
    setAllocationError(null);
    setAllocation(null);
    try {
      setAllocation(await adminApi.getOrderAllocation(token, orderId));
    } catch (err) {
      setAllocationError(
        err instanceof ApiError && err.status === 404
          ? t("admin.allocationNotFound")
          : err instanceof ApiError
          ? err.message
          : t("admin.loadError")
      );
    } finally {
      setAllocationBusy(false);
    }
  }

  function ReviewCard({ item }: { item: ReviewItem }) {
    const catalogEntry = catalogItemFor(item.product_type);
    const message = rowMessage?.id === item.sublot_id ? rowMessage : null;

    return (
      <div className="sublot-card">
        <div className="sublot-swatch" style={{ background: catalogEntry?.swatch ?? "var(--neutral-bg)" }}>
          <span>{catalogEntry?.emoji ?? "📦"}</span>
        </div>
        <div className="sublot-body">
          <div className="sublot-top">
            <div>
              <div className="sublot-name">{formatProductType(item.product_type)}</div>
              <div className="sublot-ids">
                {t("admin.orderSublotWorkshop", {
                  orderId: item.order_id,
                  sublotId: item.sublot_id,
                  workshopId: item.workshop_id,
                })}
              </div>
            </div>
            <span className={`status-pill status-${item.status.toLowerCase()}`}>
              {item.status === "VERIFYING" ? t("admin.statusStuck") : t("admin.statusNeedsReview")}
            </span>
          </div>

          <div className="sublot-meta-row">
            <span>
              {t("admin.qty")} <strong>{item.qty_assigned}</strong>
            </span>
            <span className="deadline-tag tone-warning">
              {t("admin.sinceLabel", { time: timeAgo(item.updated_at) })}
            </span>
          </div>

          {item.verdict && (
            <p className="muted" style={{ marginTop: "0.6rem" }}>
              {t("admin.lastAttemptLabel")} <strong>{item.verdict}</strong>
              {item.fault_party && item.fault_party !== "none"
                ? t("admin.faultSuffix", { fault: t(`common.role.${item.fault_party}`, item.fault_party) })
                : ""}
              {item.confidence !== null
                ? t("admin.confidenceSuffix", { pct: (item.confidence * 100).toFixed(0) })
                : ""}
              {item.explanation
                ? t("admin.explanationSuffix", {
                    explanation: localizedExplanation(
                      item.explanation, item.explanations, i18n.language
                    ),
                  })
                : ""}
            </p>
          )}
          {!item.verdict && (
            <p className="muted" style={{ marginTop: "0.6rem" }}>
              {t("admin.noVerdict")}
            </p>
          )}

          <div className="sublot-actions">
            <button
              className="btn btn-primary btn-sm"
              disabled={busyId === item.sublot_id}
              onClick={() => handleRetry(item.sublot_id)}
            >
              {busyId === item.sublot_id ? t("admin.retrying") : t("admin.retryVerification")}
            </button>
          </div>
          {message && (
            <p className={message.ok ? "tone-good" : "inline-error"} style={{ marginTop: "0.5rem" }}>
              {message.text}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <Layout>
      <div className="page">
        <h1>{t("admin.title")}</h1>
        <p className="muted">{t("admin.subtitle")}</p>

        <div className="card">
          <h2>{t("admin.allocationTitle")}</h2>
          <p className="muted">{t("admin.allocationSubtitle")}</p>
          <form className="inline-form" onSubmit={handleCheckAllocation}>
            <label>
              {t("admin.allocationInputLabel")}
              <input
                type="number"
                min={1}
                value={allocationOrderId}
                onChange={(e) => setAllocationOrderId(e.target.value)}
              />
            </label>
            <button className="btn btn-primary btn-sm" type="submit" disabled={allocationBusy || !allocationOrderId}>
              {allocationBusy ? t("admin.allocationChecking") : t("admin.allocationCheckButton")}
            </button>
            {allocationError && <span className="inline-error">{allocationError}</span>}
          </form>

          {allocation && (
            <div style={{ marginTop: "1rem" }}>
              <p>
                {allocation.workshop_count === 0
                  ? t("admin.allocationSummaryFactoryOnly", { total: allocation.total_qty })
                  : t("admin.allocationSummary", {
                      count: allocation.workshop_count,
                      total: allocation.total_qty,
                    })}
              </p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>{t("admin.allocationColWorkshop")}</th>
                      <th>{t("admin.allocationColQty")}</th>
                      <th>{t("admin.allocationColCost")}</th>
                      <th>{t("admin.allocationColStatus")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {allocation.sublots.map((s) => (
                      <tr key={s.sublot_id}>
                        <td>
                          {s.workshop_name}
                          {s.is_factory && (
                            <span className="capacity-tag tone-warning" style={{ marginLeft: "0.5rem" }}>
                              {t("admin.allocationFactoryTag")}
                            </span>
                          )}
                        </td>
                        <td>{s.qty_assigned}</td>
                        <td>₹{s.cost_per_unit}</td>
                        <td>
                          <span className={`status-pill status-${s.status.toLowerCase()}`}>
                            {t(`status.${s.status}`, s.status)}
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

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              {t("common.retry")}
            </button>
          </div>
        )}
        {items === null && !error && (
          <>
            <SkeletonCard lines={2} />
            <SkeletonCard lines={2} />
          </>
        )}

        {items !== null && items.length === 0 && (
          <div className="card empty-state">
            <div className="empty-icon">
              <PackageIcon />
            </div>
            <p>{t("admin.nothingToReview")}</p>
          </div>
        )}

        {stuck.length > 0 && (
          <div className="wp-section is-urgent">
            <div className="wp-section-head">
              <h2 className="wp-section-title">{t("admin.stuckSection")}</h2>
              <span className="wp-section-count">{stuck.length}</span>
            </div>
            {stuck.map((item) => (
              <ReviewCard key={item.sublot_id} item={item} />
            ))}
          </div>
        )}

        {pendingDecision.length > 0 && (
          <div className="wp-section">
            <div className="wp-section-head">
              <h2 className="wp-section-title">{t("admin.decisionSection")}</h2>
              <span className="wp-section-count">{pendingDecision.length}</span>
            </div>
            {pendingDecision.map((item) => (
              <ReviewCard key={item.sublot_id} item={item} />
            ))}
          </div>
        )}
      </div>
    </Layout>
  );
}
