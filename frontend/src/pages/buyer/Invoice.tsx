import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { ApiError, SettlementSummaryResponse, buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import { SkeletonCard } from "../../components/Skeleton";

export default function Invoice() {
  const { t } = useTranslation();
  const { token } = useAuth();
  const { orderId } = useParams<{ orderId: string }>();
  const id = Number(orderId);

  const [invoice, setInvoice] = useState<SettlementSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token || !id) return;
    try {
      setInvoice(await buyerApi.getInvoice(token, id));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("invoice.loadError"));
    }
  }, [token, id, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Layout>
      <div className="page page-narrow">
        <Link to={`/buyer/orders/${id}`} className="back-link">
          {t("invoice.backToOrder", { id })}
        </Link>
        <h1>{t("invoice.title")}</h1>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={load}>
              {t("common.retry")}
            </button>
          </div>
        )}

        {!invoice && !error && <SkeletonCard lines={4} />}

        {invoice && (
          <div className="card invoice-card">
            <div className="invoice-row">
              <span>{t("invoice.order")}</span>
              <span>#{invoice.order_id}</span>
            </div>
            <div className="invoice-row">
              <span>{t("invoice.goodsDelivered")}</span>
              <span>₹{invoice.buyer_base}</span>
            </div>
            <div className="invoice-row">
              <span>{t("invoice.platformFee")}</span>
              <span>₹{invoice.platform_fee}</span>
            </div>
            <div className="invoice-row invoice-total">
              <span>{t("invoice.total")}</span>
              <span>₹{invoice.buyer_total}</span>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
