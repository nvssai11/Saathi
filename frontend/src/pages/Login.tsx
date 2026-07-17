import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Role, useAuth } from "../context/AuthContext";
import { ApiError, adminApi, buyerApi, workshopApi } from "../api/client";
import { EyeIcon, EyeOffIcon } from "../components/icons";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [role, setRole] = useState<Role>("buyer");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleRoleChange(next: Role) {
    if (submitting) return;
    setRole(next);
    setError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (role === "buyer") {
        await buyerApi.listOrders(trimmed, 1, 1);
      } else if (role === "workshop") {
        await workshopApi.listSublots(trimmed);
      } else {
        await adminApi.listNeedsReview(trimmed);
      }
      login(role, trimmed);
      navigate(
        role === "buyer" ? "/buyer/new-order" : role === "workshop" ? "/my-workshop/sublots" : "/admin/review",
        { replace: true }
      );
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setError(`That token isn't valid for a ${role} account.`);
      } else {
        setError("Couldn't reach Saathi right now. Check your connection and try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="page-center">
      <div className="card auth-card">
        <div className="auth-logo">S</div>
        <h1 style={{ textAlign: "center" }}>Saathi</h1>
        <p className="muted">One supplier to work with. A whole consortium behind it.</p>

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label>I am a</label>
            <div className="segmented" role="group" aria-label="I am a">
              <button
                type="button"
                className={role === "buyer" ? "active" : ""}
                aria-pressed={role === "buyer"}
                disabled={submitting}
                onClick={() => handleRoleChange("buyer")}
              >
                Buyer
              </button>
              <button
                type="button"
                className={role === "workshop" ? "active" : ""}
                aria-pressed={role === "workshop"}
                disabled={submitting}
                onClick={() => handleRoleChange("workshop")}
              >
                Workshop
              </button>
              <button
                type="button"
                className={role === "admin" ? "active" : ""}
                aria-pressed={role === "admin"}
                disabled={submitting}
                onClick={() => handleRoleChange("admin")}
              >
                Admin
              </button>
            </div>
          </div>

          <div className="field">
            <label htmlFor="token">Access token</label>
            <div className="input-with-action">
              <input
                id="token"
                type={showToken ? "text" : "password"}
                placeholder={role === "buyer" ? "buyer-demo-token" : role === "workshop" ? "token-ws-1" : "admin-demo-token"}
                value={token}
                onChange={(e) => {
                  setToken(e.target.value);
                  if (error) setError(null);
                }}
                aria-invalid={!!error}
                autoComplete="off"
                autoFocus
              />
              <button
                type="button"
                className="input-action-btn"
                onClick={() => setShowToken((s) => !s)}
                aria-label={showToken ? "Hide access token" : "Show access token"}
              >
                {showToken ? <EyeOffIcon /> : <EyeIcon />}
              </button>
            </div>
            {error && <span className="inline-error">{error}</span>}
          </div>

          <button type="submit" className="btn btn-primary btn-block" disabled={submitting || !token.trim()}>
            {submitting ? (
              <>
                <span className="spinner" /> Signing in…
              </>
            ) : (
              "Continue"
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
