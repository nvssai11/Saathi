import { FormEvent, useEffect, useRef, useState } from "react";
import {
  CatalogItem,
  QUALITY_TIERS,
  discountPct,
  factoryPriceForTier,
  priceForTier,
} from "../data/catalog";

interface BulkOrderSheetProps {
  item: CatalogItem;
  onClose: () => void;
  onSubmit: (args: { totalQty: number; qualityMin: number; deadline: string; buyerRef: string }) => Promise<void>;
}

function defaultDeadline(): string {
  const d = new Date();
  d.setDate(d.getDate() + 21);
  return d.toISOString().slice(0, 10);
}

function minDeadline(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

export default function BulkOrderSheet({ item, onClose, onSubmit }: BulkOrderSheetProps) {
  const [qtyText, setQtyText] = useState(String(item.moq));
  const parsedQty = Number(qtyText);
  const validQty = qtyText.trim() !== "" && Number.isFinite(parsedQty) && parsedQty >= item.moq ? parsedQty : null;

  const [tier, setTier] = useState(QUALITY_TIERS[0].value);
  const tierPrice = priceForTier(item, tier);
  const tierFactoryPrice = factoryPriceForTier(item, tier);
  const [deadline, setDeadline] = useState(defaultDeadline());
  const [buyerRef, setBuyerRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sheetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    sheetRef.current?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  function commitQty(next: number) {
    setQtyText(String(Math.max(item.moq, next)));
  }

  function step(delta: number) {
    commitQty((validQty ?? item.moq) + delta);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (validQty === null) {
      commitQty(item.moq);
      return;
    }
    if (deadline < minDeadline()) {
      setError("Deadline must be at least a day out.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        totalQty: validQty,
        qualityMin: tier,
        deadline,
        buyerRef: buyerRef.trim() || `SAATHI-${Date.now().toString().slice(-8)}`,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not place order.");
      setSubmitting(false);
    }
  }

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sheet-product-title"
        tabIndex={-1}
        ref={sheetRef}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sheet-handle" />
        <div className="sheet-product">
          <div className="sheet-swatch" style={{ background: item.swatch }}>
            <span>{item.emoji}</span>
          </div>
          <div>
            <div className="sheet-category">{item.category}</div>
            <h2 className="sheet-title" id="sheet-product-title">{item.name}</h2>
            <div className="sheet-price">
              ₹{tierPrice}/unit <span className="sheet-mrp">₹{tierFactoryPrice}</span>{" "}
              <span className="sheet-off">{discountPct(item)}% off</span> · MOQ {item.moq}
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          {error && <div className="banner banner-error">{error}</div>}

          <div className="field">
            <label htmlFor="bulk-qty">Bulk quantity</label>
            <div className="qty-stepper">
              <button type="button" onClick={() => step(-item.moq)} aria-label="Decrease quantity">
                −
              </button>
              <input
                id="bulk-qty"
                type="number"
                inputMode="numeric"
                min={item.moq}
                step={item.moq}
                value={qtyText}
                onChange={(e) => setQtyText(e.target.value)}
                onBlur={() => commitQty(validQty ?? item.moq)}
                aria-invalid={validQty === null}
              />
              <button type="button" onClick={() => step(item.moq)} aria-label="Increase quantity">
                +
              </button>
            </div>
            <span className="muted">
              {validQty === null
                ? `Enter at least ${item.moq} units.`
                : `Minimum order quantity is ${item.moq} units.`}
            </span>
          </div>

          <div className="field">
            <label>Quality tier</label>
            <div className="tier-options">
              {QUALITY_TIERS.map((t) => (
                <button
                  type="button"
                  key={t.value}
                  className={`tier-option ${tier === t.value ? "active" : ""}`}
                  aria-pressed={tier === t.value}
                  onClick={() => setTier(t.value)}
                >
                  <span className="tier-label">{t.label}</span>
                  <span className="tier-blurb">{t.blurb}</span>
                  <span className="tier-price">₹{priceForTier(item, t.value)}/unit</span>
                </button>
              ))}
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="deadline">Need it by</label>
              <input
                id="deadline"
                type="date"
                required
                min={minDeadline()}
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="buyerRef">Order reference (optional)</label>
              <input
                id="buyerRef"
                placeholder="e.g. PO-2026-0417"
                value={buyerRef}
                onChange={(e) => setBuyerRef(e.target.value)}
              />
            </div>
          </div>

          <button type="submit" className="btn btn-accent btn-block btn-lg" disabled={submitting || validQty === null}>
            {submitting
              ? "Placing order…"
              : validQty === null
              ? "Enter a valid quantity"
              : `Place bulk order — ${validQty} units · ~₹${(validQty * tierPrice).toLocaleString("en-IN")}`}
          </button>
          <button type="button" className="btn btn-ghost btn-block" onClick={onClose}>
            Cancel
          </button>
        </form>
      </div>
    </div>
  );
}
