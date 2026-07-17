import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { ApiError, ReviewItem, adminApi } from "../../api/client";
import Layout from "../../components/Layout";
import { PackageIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { catalogItemFor, formatProductType } from "../../data/catalog";
import { timeAgo } from "../../utils/format";

const POLL_INTERVAL_MS = 8000;

export default function NeedsReview() {
  const { token } = useAuth();
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowMessage, setRowMessage] = useState<{ id: number; text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setItems(await adminApi.listNeedsReview(token));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load review items.");
    }
  }, [token]);

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
        text: `Retried — new status: ${res.status}${res.explanation ? ` (${res.explanation})` : ""}`,
      });
      await load();
    } catch (err) {
      setRowMessage({
        id: sublotId,
        ok: false,
        text: err instanceof ApiError ? err.message : "Retry failed.",
      });
    } finally {
      setBusyId(null);
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
                Order #{item.order_id} · Sub-lot #{item.sublot_id} · Workshop #{item.workshop_id}
              </div>
            </div>
            <span className={`status-pill status-${item.status.toLowerCase()}`}>
              {item.status === "VERIFYING" ? "Stuck" : "Needs review"}
            </span>
          </div>

          <div className="sublot-meta-row">
            <span>
              Qty <strong>{item.qty_assigned}</strong>
            </span>
            <span className="deadline-tag tone-warning">Since {timeAgo(item.updated_at)}</span>
          </div>

          {item.verdict && (
            <p className="muted" style={{ marginTop: "0.6rem" }}>
              Last attempt: <strong>{item.verdict}</strong>
              {item.fault_party && item.fault_party !== "none" ? ` (fault: ${item.fault_party})` : ""}
              {item.confidence !== null ? ` · confidence ${(item.confidence * 100).toFixed(0)}%` : ""}
              {item.explanation ? ` — "${item.explanation}"` : ""}
            </p>
          )}
          {!item.verdict && (
            <p className="muted" style={{ marginTop: "0.6rem" }}>
              No verdict was ever recorded — the previous attempt likely failed before reaching Gemini.
            </p>
          )}

          <div className="sublot-actions">
            <button
              className="btn btn-primary btn-sm"
              disabled={busyId === item.sublot_id}
              onClick={() => handleRetry(item.sublot_id)}
            >
              {busyId === item.sublot_id ? "Retrying…" : "Retry verification"}
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
        <h1>Needs review</h1>
        <p className="muted">
          Sub-lots whose verification never reached a final answer — either a call crashed mid-flight
          (Stuck) or the model deliberately deferred to a human (Needs review). Retrying redoes the
          same verification call; nothing here is guessed or auto-resolved.
        </p>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              Retry
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
            <p>Nothing needs review right now.</p>
          </div>
        )}

        {stuck.length > 0 && (
          <div className="wp-section is-urgent">
            <div className="wp-section-head">
              <h2 className="wp-section-title">Stuck — never finished</h2>
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
              <h2 className="wp-section-title">Needs a human decision</h2>
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
