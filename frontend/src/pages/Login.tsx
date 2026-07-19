import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Role, useAuth } from "../context/AuthContext";
import { ApiError, adminApi, authApi, buyerApi, workshopApi } from "../api/client";
import { EyeIcon, EyeOffIcon } from "../components/icons";
import LanguageToggle from "../components/LanguageToggle";

const RESEND_COOLDOWN_SECONDS = 30;

// Static demo bearer tokens — same values as config.py's default
// BUYER_TOKEN/ADMIN_TOKEN/WORKSHOP_TOKENS_JSON, already public knowledge
// within this project's v0 demo auth model (documented in .env.example and
// CLAUDE.md). Purely a hackathon-demo convenience so a presenter doesn't
// need to remember/type these live; not something a real deployment would
// ship with real credentials.
const DEMO_WORKSHOP_TOKENS: { label: string; token: string }[] = [
  { label: "WS-1", token: "token-ws-1" },
  { label: "WS-2", token: "token-ws-2" },
  { label: "WS-3", token: "token-ws-3" },
  { label: "WS-4", token: "token-ws-4" },
  { label: "WS-5", token: "token-ws-5" },
  { label: "WS-6", token: "token-ws-6" },
];
const DEMO_FACTORY_TOKEN = "token-factory";
const DEMO_BUYER_TOKEN = "buyer-demo-token";
const DEMO_ADMIN_TOKEN = "admin-demo-token";

type WorkshopAuthMode = "otp" | "token";
type OtpStep = "phone" | "code";

type LoginError = { key: string; params?: Record<string, unknown>; roleParam?: Role } | { text: string };

export default function Login() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const navigate = useNavigate();
  const [role, setRole] = useState<Role>("buyer");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<LoginError | null>(null);
  const errorText = error
    ? "key" in error
      ? t(error.key, {
          ...error.params,
          ...(error.roleParam ? { role: t(`common.role.${error.roleParam}`) } : {}),
        })
      : error.text
    : null;

  const [workshopAuthMode, setWorkshopAuthMode] = useState<WorkshopAuthMode>("otp");
  const [otpStep, setOtpStep] = useState<OtpStep>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [demoCode, setDemoCode] = useState<string | null>(null);
  const [resendIn, setResendIn] = useState(0);
  const codeInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendIn]);

  function handleRoleChange(next: Role) {
    if (submitting) return;
    setRole(next);
    setError(null);
    setOtpStep("phone");
    setDemoCode(null);
    setCode("");
  }

  function fullPhone(): string {
    return `+91${phone.replace(/\D/g, "")}`;
  }

  function fillDemoToken(demoToken: string) {
    if (submitting) return;
    setError(null);
    if (role === "workshop") {
      setWorkshopAuthMode("token");
    }
    setToken(demoToken);
    setShowToken(false);
  }

  function goToDestination(nextRole: Role) {
    navigate(
      nextRole === "buyer" ? "/buyer/new-order" : nextRole === "workshop" ? "/my-workshop/sublots" : "/admin/review",
      { replace: true }
    );
  }

  async function handleTokenSubmit(e: FormEvent) {
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
      goToDestination(role);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setError({ key: "login.tokenInvalid", roleParam: role });
      } else {
        setError({ key: "login.networkError" });
      }
      setSubmitting(false);
    }
  }

  async function handleSendCode(e: FormEvent) {
    e.preventDefault();
    const digits = phone.replace(/\D/g, "");
    if (digits.length !== 10 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await authApi.requestOtp(fullPhone());
      setDemoCode(res.demo_code);
      setOtpStep("code");
      setResendIn(RESEND_COOLDOWN_SECONDS);
      setTimeout(() => codeInputRef.current?.focus(), 0);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError({ key: "login.phoneNotRegistered" });
      } else {
        setError({ key: "login.networkError" });
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResend() {
    if (resendIn > 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await authApi.requestOtp(fullPhone());
      setDemoCode(res.demo_code);
      setResendIn(RESEND_COOLDOWN_SECONDS);
    } catch {
      setError({ key: "login.resendNetworkError" });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerifyCode(e: FormEvent) {
    e.preventDefault();
    if (code.trim().length < 4 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await authApi.verifyOtp(fullPhone(), code.trim());
      login("workshop", res.token);
      goToDestination("workshop");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError({ text: err.message });
      } else {
        setError({ key: "login.networkError" });
      }
      setSubmitting(false);
    }
  }

  const workshopOtpForm = (
    <>
      {otpStep === "phone" && (
        <form onSubmit={handleSendCode}>
          <div className="field">
            <label htmlFor="phone">{t("login.phoneLabel")}</label>
            <div className="input-with-action input-with-prefix">
              <span className="input-prefix">+91</span>
              <input
                id="phone"
                type="tel"
                inputMode="numeric"
                placeholder="98100 00001"
                value={phone}
                onChange={(e) => {
                  setPhone(e.target.value);
                  if (error) setError(null);
                }}
                aria-invalid={!!error}
                autoComplete="tel-national"
                autoFocus
                maxLength={10}
              />
            </div>
            {errorText && <span className="inline-error">{errorText}</span>}
          </div>

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={submitting || phone.replace(/\D/g, "").length !== 10}
          >
            {submitting ? (
              <>
                <span className="spinner" /> {t("login.sendingCode")}
              </>
            ) : (
              t("login.sendCode")
            )}
          </button>

          <button
            type="button"
            className="btn btn-ghost btn-block auth-alt-action"
            onClick={() => setWorkshopAuthMode("token")}
          >
            {t("login.useTokenInstead")}
          </button>
        </form>
      )}

      {otpStep === "code" && (
        <form onSubmit={handleVerifyCode}>
          <p className="muted">
            {t("login.codeSentTo")} <strong>+91 {phone}</strong>.{" "}
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setOtpStep("phone");
                setCode("");
                setError(null);
              }}
            >
              {t("login.changeNumber")}
            </button>
          </p>

          {demoCode && (
            <div className="banner banner-info">
              <span>
                {t("login.demoModeNotice")} <strong>{demoCode}</strong>
              </span>
            </div>
          )}

          <div className="field">
            <label htmlFor="code">{t("login.codeLabel")}</label>
            <input
              id="code"
              ref={codeInputRef}
              type="text"
              inputMode="numeric"
              placeholder="123456"
              value={code}
              onChange={(e) => {
                setCode(e.target.value);
                if (error) setError(null);
              }}
              aria-invalid={!!error}
              autoComplete="one-time-code"
              maxLength={8}
              className="otp-code-input"
            />
            {errorText && <span className="inline-error">{errorText}</span>}
          </div>

          <button type="submit" className="btn btn-primary btn-block" disabled={submitting || code.trim().length < 4}>
            {submitting ? (
              <>
                <span className="spinner" /> {t("login.verifying")}
              </>
            ) : (
              t("login.verifyAndSignIn")
            )}
          </button>

          <button
            type="button"
            className="btn btn-ghost btn-block auth-alt-action"
            onClick={handleResend}
            disabled={resendIn > 0 || submitting}
          >
            {resendIn > 0 ? t("login.resendIn", { seconds: resendIn }) : t("login.resendCode")}
          </button>
        </form>
      )}
    </>
  );

  const tokenForm = (
    <form onSubmit={handleTokenSubmit}>
      <div className="field">
        <label htmlFor="token">{t("login.accessToken")}</label>
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
            aria-label={showToken ? t("login.hideToken") : t("login.showToken")}
          >
            {showToken ? <EyeOffIcon /> : <EyeIcon />}
          </button>
        </div>
        {errorText && <span className="inline-error">{errorText}</span>}
      </div>

      <button type="submit" className="btn btn-primary btn-block" disabled={submitting || !token.trim()}>
        {submitting ? (
          <>
            <span className="spinner" /> {t("login.signingIn")}
          </>
        ) : (
          t("login.continueBtn")
        )}
      </button>

      {role === "workshop" && (
        <button
          type="button"
          className="btn btn-ghost btn-block auth-alt-action"
          onClick={() => {
            setWorkshopAuthMode("otp");
            setError(null);
          }}
        >
          {t("login.usePhoneInstead")}
        </button>
      )}
    </form>
  );

  return (
    <div className="page-center">
      <div className="card auth-card">
        <div className="auth-card-lang">
          <LanguageToggle />
        </div>
        <div className="auth-logo">S</div>
        <h1 style={{ textAlign: "center" }}>Saathi</h1>
        <p className="muted">{t("login.tagline")}</p>

        <div className="field">
          <label>{t("login.iAm")}</label>
          <div className="segmented" role="group" aria-label={t("login.iAm")}>
            <button
              type="button"
              className={role === "buyer" ? "active" : ""}
              aria-pressed={role === "buyer"}
              disabled={submitting}
              onClick={() => handleRoleChange("buyer")}
            >
              {t("login.roleBuyer")}
            </button>
            <button
              type="button"
              className={role === "workshop" ? "active" : ""}
              aria-pressed={role === "workshop"}
              disabled={submitting}
              onClick={() => handleRoleChange("workshop")}
            >
              {t("login.roleWorkshop")}
            </button>
            <button
              type="button"
              className={role === "admin" ? "active" : ""}
              aria-pressed={role === "admin"}
              disabled={submitting}
              onClick={() => handleRoleChange("admin")}
            >
              {t("login.roleAdmin")}
            </button>
          </div>
        </div>

        <div className="field">
          <label>{t("login.demoQuickLogin")}</label>
          <div className="action-row quick-login-row">
            {role === "buyer" && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => fillDemoToken(DEMO_BUYER_TOKEN)}
              >
                {t("login.demoBuyerBtn")}
              </button>
            )}
            {role === "admin" && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => fillDemoToken(DEMO_ADMIN_TOKEN)}
              >
                {t("login.demoAdminBtn")}
              </button>
            )}
            {role === "workshop" &&
              DEMO_WORKSHOP_TOKENS.map((w) => (
                <button
                  key={w.token}
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => fillDemoToken(w.token)}
                >
                  {w.label}
                </button>
              ))}
            {role === "workshop" && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => fillDemoToken(DEMO_FACTORY_TOKEN)}
              >
                {t("login.demoFactoryBtn")}
              </button>
            )}
          </div>
        </div>

        {role === "workshop" && workshopAuthMode === "otp" ? workshopOtpForm : tokenForm}
      </div>
    </div>
  );
}
