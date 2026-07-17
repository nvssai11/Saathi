import { useWorkshopData } from "../../context/WorkshopDataContext";
import Layout from "../../components/Layout";
import ScoreRing from "../../components/ScoreRing";
import { SkeletonCard } from "../../components/Skeleton";

export default function TrustScore() {
  const { trust, trustError: error, refresh } = useWorkshopData();

  return (
    <Layout>
      <div className="page page-narrow">
        <h1>My trust score</h1>

        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn-retry" onClick={refresh}>
              Retry
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
              <h2>Why this score</h2>
              <ul className="explanation-list">
                {trust.explanation.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </div>

            <div className="card table-card">
              <h2>Last 5 events</h2>
              {trust.history.length === 0 ? (
                <p className="muted">No delivery history yet.</p>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Sub-lot</th>
                        <th>Date</th>
                        <th>On time</th>
                        <th>Defect</th>
                        <th>Fault</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trust.history.map((h, i) => (
                        <tr key={i}>
                          <td>#{h.sublot_id}</td>
                          <td>{new Date(h.date).toLocaleDateString()}</td>
                          <td className={h.on_time ? "tone-good" : "tone-critical"}>
                            {h.on_time ? "Yes" : "No"}
                          </td>
                          <td className={h.defect_found ? "tone-critical" : "tone-good"}>
                            {h.defect_found ? "Yes" : "No"}
                          </td>
                          <td className={h.fault_party === "none" ? "tone-neutral" : ""}>
                            {h.fault_party === "none"
                              ? "—"
                              : h.fault_party.charAt(0).toUpperCase() + h.fault_party.slice(1)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </Layout>
  );
}
