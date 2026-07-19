import { FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, WorkshopCapacityListItem, workshopApi } from "../../api/client";
import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import InfoTip from "../../components/InfoTip";
import { PackageIcon } from "../../components/icons";
import { SkeletonCard } from "../../components/Skeleton";
import { catalogItemFor, formatProductType } from "../../data/catalog";
import { capacityUrgency } from "../../utils/format";

interface CapacityDraft {
  available_qty: string;
  cost_per_unit: string;
  lead_time_days: string;
}

const EMPTY_DRAFT: CapacityDraft = { available_qty: "", cost_per_unit: "", lead_time_days: "" };
const URGENCY_RANK = { critical: 0, warning: 1, normal: 2 } as const;

export default function MyCapacity() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const { capacity: items, capacityError: error, refresh } = useWorkshopData();

  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<CapacityDraft>(EMPTY_DRAFT);
  const [rowMessage, setRowMessage] = useState<{ product: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const [addOpen, setAddOpen] = useState(false);
  const [addProductType, setAddProductType] = useState("");
  const [addDraft, setAddDraft] = useState<CapacityDraft>(EMPTY_DRAFT);
  const [addError, setAddError] = useState<string | null>(null);

  function openEdit(item: WorkshopCapacityListItem) {
    setEditing(item.product_type);
    setEditDraft({
      available_qty: String(item.available_qty),
      cost_per_unit: item.cost_per_unit,
      lead_time_days: String(item.lead_time_days),
    });
    setRowMessage(null);
  }

  async function submitEdit(productType: string) {
    if (!token) return;
    setBusy(true);
    try {
      await workshopApi.updateCapacity(token, {
        product_type: productType,
        available_qty: Number(editDraft.available_qty),
        cost_per_unit: Number(editDraft.cost_per_unit),
        lead_time_days: Number(editDraft.lead_time_days),
      });
      setEditing(null);
      refresh();
    } catch (err) {
      setRowMessage({
        product: productType,
        text: err instanceof ApiError ? err.message : t("capacity.updateError"),
      });
    } finally {
      setBusy(false);
    }
  }

  async function submitAdd(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setAddError(null);
    try {
      await workshopApi.updateCapacity(token, {
        product_type: addProductType.trim(),
        available_qty: Number(addDraft.available_qty),
        cost_per_unit: Number(addDraft.cost_per_unit),
        lead_time_days: Number(addDraft.lead_time_days),
      });
      setAddOpen(false);
      setAddProductType("");
      setAddDraft(EMPTY_DRAFT);
      refresh();
    } catch (err) {
      setAddError(err instanceof ApiError ? err.message : t("capacity.addError"));
    } finally {
      setBusy(false);
    }
  }

  const sortedItems = useMemo(() => {
    if (!items) return null;
    return [...items].sort((a, b) => {
      const rankDiff =
        URGENCY_RANK[capacityUrgency(a.serving_capacity, a.available_qty)] -
        URGENCY_RANK[capacityUrgency(b.serving_capacity, b.available_qty)];
      if (rankDiff !== 0) return rankDiff;
      return a.serving_capacity - b.serving_capacity;
    });
  }, [items]);

  return (
    <Layout>
      <div className="page">
        <h1>{t("capacity.title")}</h1>
        <p className="muted">{t("capacity.subtitle")}</p>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
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
            <p>{t("capacity.noneYet")}</p>
          </div>
        )}

        {sortedItems !== null &&
          sortedItems.map((item) => {
            const catalogEntry = catalogItemFor(item.product_type);
            const total = Math.max(item.available_qty, 1);
            const reservedPct = Math.min(100, (item.in_transit_qty / total) * 100);
            const servingPct = Math.max(0, 100 - reservedPct);
            const isEditing = editing === item.product_type;
            const urgency = capacityUrgency(item.serving_capacity, item.available_qty);

            return (
              <div className="capacity-card" key={item.product_type}>
                <div className="capacity-card-head">
                  <div className="capacity-card-title">
                    <div
                      className="capacity-swatch"
                      style={{ background: catalogEntry?.swatch ?? "var(--neutral-bg)" }}
                    >
                      <span>{catalogEntry?.emoji ?? "📦"}</span>
                    </div>
                    <div>
                      <div className="capacity-name">{formatProductType(item.product_type)}</div>
                      <div className="capacity-sub">
                        {t("capacity.costLeadTime", {
                          cost: item.cost_per_unit,
                          days: item.lead_time_days,
                        })}
                      </div>
                    </div>
                  </div>
                  <div className="capacity-stats">
                    <div className="capacity-stat">
                      <div className="capacity-stat-label">
                        {t("capacity.servingCapacity")}
                        <InfoTip text={t("capacity.servingCapacityTooltip")} />
                      </div>
                      <div className="capacity-stat-value">{item.serving_capacity}</div>
                      {urgency !== "normal" && (
                        <div className={`capacity-tag ${urgency === "critical" ? "tone-critical" : "tone-warning"}`}>
                          {urgency === "critical" ? t("capacity.fullyCommitted") : t("capacity.runningLow")}
                        </div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="capacity-bar-track">
                  <div className="capacity-bar-fill reserved" style={{ width: `${reservedPct}%` }} />
                  <div className="capacity-bar-fill serving" style={{ width: `${servingPct}%` }} />
                </div>
                <div className="capacity-legend">
                  <span>
                    <span className="capacity-legend-dot" style={{ background: "var(--brand-500)" }} />
                    {t("capacity.servingCapacityLegend", { qty: item.serving_capacity })}
                  </span>
                  <span>
                    <span className="capacity-legend-dot" style={{ background: "var(--accent-400)" }} />
                    {t("capacity.inTransitLegend", { qty: item.in_transit_qty })}
                  </span>
                  <span>{t("capacity.availableInventoryLegend", { qty: item.available_qty })}</span>
                </div>
                <p className="capacity-plain-help">{t("capacity.plainHelp")}</p>

                <div className="capacity-card-actions">
                  {!isEditing ? (
                    <button className="btn btn-secondary btn-sm" onClick={() => openEdit(item)}>
                      {t("capacity.edit")}
                    </button>
                  ) : (
                    <div className="inline-form">
                      <label>
                        {t("capacity.availableInventoryLabel")}
                        <input
                          type="number"
                          min={item.in_transit_qty}
                          value={editDraft.available_qty}
                          onChange={(e) =>
                            setEditDraft((d) => ({ ...d, available_qty: e.target.value }))
                          }
                        />
                      </label>
                      <label>
                        {t("capacity.costPerUnit")}
                        <input
                          type="number"
                          min={0}
                          step="0.01"
                          value={editDraft.cost_per_unit}
                          onChange={(e) =>
                            setEditDraft((d) => ({ ...d, cost_per_unit: e.target.value }))
                          }
                        />
                      </label>
                      <label>
                        {t("capacity.leadTimeDays")}
                        <input
                          type="number"
                          min={1}
                          value={editDraft.lead_time_days}
                          onChange={(e) =>
                            setEditDraft((d) => ({ ...d, lead_time_days: e.target.value }))
                          }
                        />
                      </label>
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={busy}
                        onClick={() => submitEdit(item.product_type)}
                      >
                        {t("common.save")}
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>
                        {t("common.cancel")}
                      </button>
                      {rowMessage?.product === item.product_type && (
                        <span className="inline-error">{rowMessage.text}</span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

        <div className="card">
          {!addOpen ? (
            <button className="btn btn-secondary" onClick={() => setAddOpen(true)}>
              {t("capacity.addProduct")}
            </button>
          ) : (
            <form className="inline-form" onSubmit={submitAdd}>
              <label>
                {t("capacity.productType")}
                <input
                  type="text"
                  required
                  value={addProductType}
                  onChange={(e) => setAddProductType(e.target.value)}
                />
              </label>
              <label>
                {t("capacity.availableInventoryLabel")}
                <input
                  type="number"
                  min={0}
                  required
                  value={addDraft.available_qty}
                  onChange={(e) => setAddDraft((d) => ({ ...d, available_qty: e.target.value }))}
                />
              </label>
              <label>
                {t("capacity.costPerUnit")}
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  required
                  value={addDraft.cost_per_unit}
                  onChange={(e) => setAddDraft((d) => ({ ...d, cost_per_unit: e.target.value }))}
                />
              </label>
              <label>
                {t("capacity.leadTimeDays")}
                <input
                  type="number"
                  min={1}
                  required
                  value={addDraft.lead_time_days}
                  onChange={(e) => setAddDraft((d) => ({ ...d, lead_time_days: e.target.value }))}
                />
              </label>
              <button className="btn btn-primary btn-sm" type="submit" disabled={busy}>
                {t("common.save")}
              </button>
              <button className="btn btn-ghost btn-sm" type="button" onClick={() => setAddOpen(false)}>
                {t("common.cancel")}
              </button>
              {addError && <span className="inline-error">{addError}</span>}
            </form>
          )}
        </div>
      </div>
    </Layout>
  );
}
