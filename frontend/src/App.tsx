import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import Login from "./pages/Login";
import PlaceOrder from "./pages/buyer/PlaceOrder";
import OrderList from "./pages/buyer/OrderList";
import OrderDetail from "./pages/buyer/OrderDetail";
import Invoice from "./pages/buyer/Invoice";
import MySublots from "./pages/workshop/MySublots";
import TrustScore from "./pages/workshop/TrustScore";
import MyCapacity from "./pages/workshop/MyCapacity";
import Notifications from "./pages/workshop/Notifications";
import NeedsReview from "./pages/admin/NeedsReview";

function RequireRole({ role, children }: { role: "buyer" | "workshop" | "admin"; children: JSX.Element }) {
  const auth = useAuth();
  if (!auth.token || auth.role !== role) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  const { role, token } = useAuth();
  const homeRedirect = token
    ? role === "buyer"
      ? "/buyer/new-order"
      : role === "workshop"
      ? "/my-workshop/sublots"
      : "/admin/review"
    : null;

  return (
    <Routes>
      <Route path="/" element={homeRedirect ? <Navigate to={homeRedirect} replace /> : <Login />} />

      <Route
        path="/buyer/new-order"
        element={
          <RequireRole role="buyer">
            <PlaceOrder />
          </RequireRole>
        }
      />
      <Route
        path="/buyer/orders"
        element={
          <RequireRole role="buyer">
            <OrderList />
          </RequireRole>
        }
      />
      <Route
        path="/buyer/orders/:orderId"
        element={
          <RequireRole role="buyer">
            <OrderDetail />
          </RequireRole>
        }
      />
      <Route
        path="/buyer/orders/:orderId/invoice"
        element={
          <RequireRole role="buyer">
            <Invoice />
          </RequireRole>
        }
      />

      <Route
        path="/my-workshop/sublots"
        element={
          <RequireRole role="workshop">
            <MySublots />
          </RequireRole>
        }
      />
      <Route
        path="/my-workshop/trust"
        element={
          <RequireRole role="workshop">
            <TrustScore />
          </RequireRole>
        }
      />
      <Route
        path="/my-workshop/capacity"
        element={
          <RequireRole role="workshop">
            <MyCapacity />
          </RequireRole>
        }
      />
      <Route
        path="/my-workshop/notifications"
        element={
          <RequireRole role="workshop">
            <Notifications />
          </RequireRole>
        }
      />

      <Route
        path="/admin/review"
        element={
          <RequireRole role="admin">
            <NeedsReview />
          </RequireRole>
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
