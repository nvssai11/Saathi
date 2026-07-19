import { useTranslation } from "react-i18next";
import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import ScoreRing from "../../components/ScoreRing";
import { SkeletonCard } from "../../components/Skeleton";
import { localizedExplanation } from "../../utils/format";

export default function TrustScore() {
  const { t, i18n } = useTranslation();
  const { trust, trustError: error, refresh } = useWorkshopData();

  return (
    <Layout>
      <div className="page page-narrow">
        <h1>{t("trust.title")}</h1>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
              {t("common.retry")}
            </button>
          </div>
        )}
        {!trust && !error && <SkeletonCard lines={3} />}

        {trust && (
          <>
            <div className="card trust-hero">
              <ScoreRing score={trust.score} grade={trust.grade} />
            </div>

            <div className="card">
              <h2>{t("trust.whyThisScore")}</h2>
              <ul className="explanation-list">
                {trust.window_count === 0 ? (
                  <li>{t("trust.explanationColdStart", { score: trust.score.toFixed(3) })}</li>
                ) : (
                  <>
                    <li>
                      {t("trust.explanationScore", {
                        score: trust.score.toFixed(3),
                        grade: trust.grade,
                        count: trust.window_count,
                      })}
                    </li>
                    <li>
                      {t("trust.explanationOnTime", { pct: (trust.on_time_rate * 100).toFixed(1) })}
                    </li>
                    <li>
                      {t("trust.explanationDefect", { pct: (trust.defect_rate * 100).toFixed(1) })}
                    </li>
                  </>
                )}
              </ul>
            </div>

            <div className="wp-section">
              <div className="wp-section-head">
                <h2 className="wp-section-title">{t("trust.lastEvents")}</h2>
              </div>
              {trust.history.length === 0 ? (
                <div className="card">
                  <p className="muted">{t("trust.noHistory")}</p>
                </div>
              ) : (
                trust.history.map((h, i) => (
                  <div className="trust-event-card" key={i}>
                    <div className="trust-event-head">
                      <span className="trust-event-sublot">{t("trust.eventSublot", { sublotId: h.sublot_id })}</span>
                      <span className="trust-event-date">{new Date(h.date).toLocaleDateString()}</span>
                    </div>
                    <div className="trust-event-tags">
                      <span className={`status-pill status-${h.on_time ? "good" : "critical"}`}>
                        {h.on_time ? t("trust.eventOnTime") : t("trust.eventLate")}
                      </span>
                      <span
                        className={`status-pill status-${
                          !h.defect_found ? "good" : h.fault_party === "workshop" ? "critical" : "neutral"
                        }`}
                      >
                        {!h.defect_found
                          ? t("trust.eventNoDefect")
                          : h.fault_party === "workshop"
                          ? t("trust.eventDefectYourFault")
                          : t("trust.eventDefectNotYourFault")}
                      </span>
                    </div>
                    <p className="trust-event-explanation">
                      {localizedExplanation(h.explanation, h.explanations, i18n.language) ??
                        t("trust.noExplanation")}
                    </p>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </div>
    </Layout>
  );
}
