import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./context/AuthContext";
import { WorkshopDataProvider } from "./context/WorkshopDataContext";
import "./i18n";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <WorkshopDataProvider>
          <App />
        </WorkshopDataProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
