import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import { CreatorModeProvider } from "./CreatorModeContext";
import { TenantProvider } from "./TenantContext";
import { WorkspaceProvider } from "./WorkspaceContext";
import GlassShell from "./design/GlassShell";
import AssetsPage from "./pages/AssetsPage";
import AuthPage from "./pages/AuthPage";
import BoardPage from "./pages/BoardPage";
import CampaignNewPage from "./pages/CampaignNewPage";
import CampaignsPage from "./pages/CampaignsPage";
import Dashboard from "./pages/Dashboard";
import GeneratePage from "./pages/GeneratePage";
import LegacyWorkbench from "./pages/LegacyWorkbench";
import PipelinesPage from "./pages/PipelinesPage";
import ProductDetailPage from "./pages/ProductDetailPage";
import ProductNewPage from "./pages/ProductNewPage";
import ProductsPage from "./pages/ProductsPage";
import RunsPage from "./pages/RunsPage";
import StubPage from "./pages/StubPage";
import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Route guard — redirects to /auth if not authenticated
// ---------------------------------------------------------------------------
function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="auth-loading">
        <span className="spinner" aria-label="Loading session…" />
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/auth" replace />;
  }

  return <>{children}</>;
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  return (
    <AuthProvider>
      <CreatorModeProvider>
        <TenantProvider>
          <WorkspaceProvider>
            <BrowserRouter>
              <Routes>
                {/* Unauthenticated route — no shell */}
                <Route path="/auth" element={<AuthPage />} />

                {/* All other routes require auth */}
                <Route
                  path="/*"
                  element={
                    <ProtectedRoute>
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
                                <Route path="/products/new" element={<ProductNewPage />} />
                                <Route path="/products/:slug" element={<ProductDetailPage />} />
                                <Route path="/campaigns" element={<CampaignsPage />} />
                                <Route path="/campaigns/new" element={<CampaignNewPage />} />
                                <Route path="/assets" element={<AssetsPage />} />
                                <Route path="/generate" element={<BoardPage />} />
                                <Route path="/generate-form" element={<GeneratePage />} />
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
                                  element={
                                    <StubPage title="Settings" subtitle="Workspace and tenant configuration." />
                                  }
                                />
                                <Route path="*" element={<Navigate to="/" replace />} />
                              </Routes>
                            </GlassShell>
                          }
                        />
                      </Routes>
                    </ProtectedRoute>
                  }
                />
              </Routes>
            </BrowserRouter>
          </WorkspaceProvider>
        </TenantProvider>
      </CreatorModeProvider>
    </AuthProvider>
  );
}
