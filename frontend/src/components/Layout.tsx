import { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useWorkshopBadges } from "../hooks/useWorkshopBadges";
import { useAdminReviewCount } from "../hooks/useAdminReviewCount";
import { UserIcon, HelpCircleIcon } from "./icons";
import { SUPPORT_WHATSAPP_LINK } from "../data/support";

interface LayoutProps {
  children: ReactNode;
  search?: ReactNode;
}

const BUYER_NAV = [
  { to: "/buyer/new-order", label: "Shop" },
  { to: "/buyer/orders", label: "Order status" },
];

const WORKSHOP_NAV = [
  { to: "/my-workshop/sublots", label: "My sub-lots" },
  { to: "/my-workshop/capacity", label: "My capacity" },
  { to: "/my-workshop/notifications", label: "Notifications" },
  { to: "/my-workshop/trust", label: "My trust score" },
];

const ADMIN_NAV = [{ to: "/admin/review", label: "Needs review" }];

export default function Layout({ children, search }: LayoutProps) {
  const { role, logout } = useAuth();
  const { urgentSublots, newNotifications } = useWorkshopBadges();
  const reviewCount = useAdminReviewCount();
  const nav = role === "buyer" ? BUYER_NAV : role === "workshop" ? WORKSHOP_NAV : ADMIN_NAV;

  function badgeFor(to: string): { count: number; urgent: boolean } | null {
    if (to === "/my-workshop/sublots" && urgentSublots > 0) {
      return { count: urgentSublots, urgent: true };
    }
    if (to === "/my-workshop/notifications" && newNotifications > 0) {
      return { count: newNotifications, urgent: false };
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
          <a
            className="btn btn-ghost help-link"
            href={SUPPORT_WHATSAPP_LINK}
            target="_blank"
            rel="noopener noreferrer"
          >
            <HelpCircleIcon />
            <span>Need help?</span>
          </a>
          <span className="role-badge">
            <UserIcon />
            {role}
          </span>
          <button className="btn btn-ghost" onClick={logout}>
            Sign out
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
                  title={badge.urgent ? "Needs your attention soon" : "New since the last hour"}
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
