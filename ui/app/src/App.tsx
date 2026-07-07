import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { CreatorModeProvider } from "./CreatorModeContext";
import GlassShell from "./design/GlassShell";
import AssetsPage from "./pages/AssetsPage";
import CampaignsPage from "./pages/CampaignsPage";
import Dashboard from "./pages/Dashboard";
import GeneratePage from "./pages/GeneratePage";
import LegacyWorkbench from "./pages/LegacyWorkbench";
import PipelinesPage from "./pages/PipelinesPage";
import ProductsPage from "./pages/ProductsPage";
import RunsPage from "./pages/RunsPage";
import StubPage from "./pages/StubPage";

export default function App() {
  return (
    <CreatorModeProvider>
      <BrowserRouter>
        <Routes>
          <Route
            path="/workbench"
            element={
              <div className="workbench-route">
                <LegacyWorkbench />
              </div>
            }
          />
          <Route
            path="/*"
            element={
              <GlassShell>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/products" element={<ProductsPage />} />
                  <Route path="/campaigns" element={<CampaignsPage />} />
                  <Route path="/assets" element={<AssetsPage />} />
                  <Route path="/generate" element={<GeneratePage />} />
                  <Route path="/runs" element={<RunsPage />} />
                  <Route path="/pipelines" element={<PipelinesPage />} />
                  <Route
                    path="/knowledge"
                    element={
                      <StubPage title="Knowledge" subtitle="Package manager for products, campaigns, operations." />
                    }
                  />
                  <Route
                    path="/providers"
                    element={
                      <StubPage title="Providers" subtitle="Capability registry — developers only." />
                    }
                  />
                  <Route
                    path="/settings"
                    element={<StubPage title="Settings" subtitle="Workspace and tenant configuration." />}
                  />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </GlassShell>
            }
          />
        </Routes>
      </BrowserRouter>
    </CreatorModeProvider>
  );
}
