import { createBrowserRouter } from "react-router";
import { Layout } from "./pages/Layout";
import { LoginPage } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { FindingsPage } from "./pages/Findings";
import { FindingPage } from "./pages/Finding";
import { UploadsPage } from "./pages/Uploads";
import { SettingsPage } from "./pages/Settings";
import { NotFoundPage } from "./pages/NotFound";
import { LinksPage } from "./pages/Links";
import { SupplierPage } from "./pages/Supplier";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "findings", element: <FindingsPage /> },
      { path: "findings/:id", element: <FindingPage /> },
      { path: "links", element: <LinksPage /> },
      { path: "suppliers/:id", element: <SupplierPage /> },
      { path: "uploads", element: <UploadsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
