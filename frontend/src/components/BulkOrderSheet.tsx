import { FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { PaymentTerms } from "../api/client";
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
  onSubmit: (args: {
    totalQty: number;
    qualityMin: number;
    deadline: string;
    buyerRef: string;
    paymentTerms: PaymentTerms;
  }) => Promise<void>;
}

const TIER_KEY: Record<number, { label: string; blurb: string }> = {
  2: { label: "tierStandard", blurb: "tierStandardBlurb" },
  4: { label: "tierPremium", blurb: "tierPremiumBlurb" },
  5: { label: "tierExport", blurb: "tierExportBlurb" },
};

// Mirrors config.py's settings.settlement_advance_percentage default (0.30).
const ADVANCE_PERCENTAGE = 30;

const PAYMENT_TERMS_OPTIONS: { value: PaymentTerms; label: string; blurb: string }[] = [
  { value: "PAY_ON_DELIVERY", label: "paymentPayOnDelivery", blurb: "paymentPayOnDeliveryBlurb" },
  { value: "PAY_UPFRONT", label: "paymentPayUpfront", blurb: "paymentPayUpfrontBlurb" },
  { value: "ADVANCE_PLUS_BALANCE", label: "paymentAdvancePlusBalance", blurb: "paymentAdvancePlusBalanceBlurb" },
];

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
  const { t } = useTranslation();
  const [qtyText, setQtyText] = useState(String(item.moq));
  const parsedQty = Number(qtyText);
  const validQty = qtyText.trim() !== "" && Number.isFinite(parsedQty) && parsedQty >= item.moq ? parsedQty : null;

  const [tier, setTier] = useState(QUALITY_TIERS[0].value);
  const tierPrice = priceForTier(item, tier);
  const tierFactoryPrice = factoryPriceForTier(item, tier);
  const [deadline, setDeadline] = useState(defaultDeadline());
  const [buyerRef, setBuyerRef] = useState("");
  const [paymentTerms, setPaymentTerms] = useState<PaymentTerms>("PAY_ON_DELIVERY");
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
      setError(t("bulkOrder.deadlineTooSoon"));
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
        paymentTerms,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("bulkOrder.couldNotPlace"));
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
            <label htmlFor="bulk-qty">{t("bulkOrder.bulkQuantity")}</label>
            <div className="qty-stepper">
              <button type="button" onClick={() => step(-item.moq)} aria-label={t("bulkOrder.decreaseQty")}>
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
              <button type="button" onClick={() => step(item.moq)} aria-label={t("bulkOrder.increaseQty")}>
                +
              </button>
            </div>
            <span className="muted">
              {validQty === null
                ? t("bulkOrder.enterAtLeast", { moq: item.moq })
                : t("bulkOrder.minimumIs", { moq: item.moq })}
            </span>
          </div>

          <div className="field">
            <label>{t("bulkOrder.qualityTier")}</label>
            <div className="tier-options">
              {QUALITY_TIERS.map((qt) => (
                <button
                  type="button"
                  key={qt.value}
                  className={`tier-option ${tier === qt.value ? "active" : ""}`}
                  aria-pressed={tier === qt.value}
                  onClick={() => setTier(qt.value)}
                >
                  <span className="tier-label">{t(`bulkOrder.${TIER_KEY[qt.value]?.label}`, qt.label)}</span>
                  <span className="tier-blurb">{t(`bulkOrder.${TIER_KEY[qt.value]?.blurb}`, qt.blurb)}</span>
                  <span className="tier-price">₹{priceForTier(item, qt.value)}/unit</span>
                </button>
              ))}
            </div>
          </div>

          <div className="field">
            <label>{t("bulkOrder.paymentTerms")}</label>
            <div className="tier-options">
              {PAYMENT_TERMS_OPTIONS.map((opt) => (
                <button
                  type="button"
                  key={opt.value}
                  className={`tier-option ${paymentTerms === opt.value ? "active" : ""}`}
                  aria-pressed={paymentTerms === opt.value}
                  onClick={() => setPaymentTerms(opt.value)}
                >
                  <span className="tier-label">
                    {t(`bulkOrder.${opt.label}`, { pct: ADVANCE_PERCENTAGE })}
                  </span>
                  <span className="tier-blurb">{t(`bulkOrder.${opt.blurb}`)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="deadline">{t("bulkOrder.needItBy")}</label>
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
              <label htmlFor="buyerRef">{t("bulkOrder.orderReference")}</label>
              <input
                id="buyerRef"
                placeholder={t("bulkOrder.orderReferencePlaceholder")}
                value={buyerRef}
                onChange={(e) => setBuyerRef(e.target.value)}
              />
            </div>
          </div>

          <button type="submit" className="btn btn-accent btn-block btn-lg" disabled={submitting || validQty === null}>
            {submitting
              ? t("bulkOrder.placingOrder")
              : validQty === null
              ? t("bulkOrder.enterValidQty")
              : t("bulkOrder.placeBulkOrder", {
                  qty: validQty,
                  amount: (validQty * tierPrice).toLocaleString("en-IN"),
                })}
          </button>
          <button type="button" className="btn btn-ghost btn-block" onClick={onClose}>
            {t("bulkOrder.cancel")}
          </button>
        </form>
      </div>
    </div>
  );
}
