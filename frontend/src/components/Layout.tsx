import { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../context/AuthContext";
import { useWorkshopBadges } from "../hooks/useWorkshopBadges";
import { useAdminReviewCount } from "../hooks/useAdminReviewCount";
import { UserIcon, HelpCircleIcon, BellIcon } from "./icons";
import { SUPPORT_WHATSAPP_LINK } from "../data/support";
import LanguageToggle from "./LanguageToggle";

interface LayoutProps {
  children: ReactNode;
  search?: ReactNode;
}

export default function Layout({ children, search }: LayoutProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { role, logout } = useAuth();
  const { urgentSublots, newNotifications } = useWorkshopBadges();
  const reviewCount = useAdminReviewCount();

  const BUYER_NAV = [
    { to: "/buyer/new-order", label: t("nav.shop") },
    { to: "/buyer/orders", label: t("nav.orderStatus") },
  ];
  const WORKSHOP_NAV = [
    { to: "/my-workshop/sublots", label: t("nav.sublots") },
    { to: "/my-workshop/capacity", label: t("nav.capacity") },
    { to: "/my-workshop/notifications", label: t("nav.notifications") },
    { to: "/my-workshop/trust", label: t("nav.trust") },
  ];
  const ADMIN_NAV = [{ to: "/admin/review", label: t("nav.review") }];

  const nav = role === "buyer" ? BUYER_NAV : role === "workshop" ? WORKSHOP_NAV : ADMIN_NAV;

  function badgeFor(to: string): { count: number; urgent: boolean } | null {
    if (to === "/my-workshop/sublots" && urgentSublots > 0) {
      return { count: urgentSublots, urgent: true };
    }
    if (to === "/admin/review" && reviewCount > 0) {
      return { count: reviewCount, urgent: true };
    }
    return null;
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="brand">
          <span className="brand-mark">S</span>
          Saathi
        </span>
        {search && <div className="header-search">{search}</div>}
        <div className="header-right">
          {role === "workshop" && (
            <button
              type="button"
              className="header-bell"
              onClick={() => navigate("/my-workshop/notifications")}
              aria-label={t("nav.notifications")}
            >
              <BellIcon />
              {newNotifications > 0 && (
                <span className="header-bell-badge">{newNotifications}</span>
              )}
            </button>
          )}
          <LanguageToggle />
          <a
            className="btn btn-ghost help-link"
            href={SUPPORT_WHATSAPP_LINK}
            target="_blank"
            rel="noopener noreferrer"
          >
            <HelpCircleIcon />
            <span>{t("common.needHelp")}</span>
          </a>
          <span className="role-badge">
            <UserIcon />
            {role ? t(`common.role.${role}`) : ""}
          </span>
          <button className="btn btn-ghost" onClick={logout}>
            {t("common.signOut")}
          </button>
        </div>
      </header>
      <nav className="app-subnav">
        {nav.map((item) => {
          const badge = badgeFor(item.to);
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "subnav-link active" : "subnav-link")}
            >
              {item.label}
              {badge && (
                <span
                  className={`subnav-badge ${badge.urgent ? "is-urgent" : ""}`}
                  title={badge.urgent ? t("nav.badgeUrgent") : t("nav.badgeNew")}
                >
                  {badge.count}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>
      <main className="app-main">{children}</main>
    </div>
  );
}
