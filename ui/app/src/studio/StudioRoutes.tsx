import { Route, Routes } from "react-router-dom";
import ImageCompositionPage from "./composition/ImageCompositionPage";
import ExportsPage from "./pages/ExportsPage";
import HandoffPage from "./pages/HandoffPage";
import RecipeWorkflowPage from "./pages/RecipeWorkflowPage";
import StudioListPage from "./pages/StudioListPage";
import "./studio.css";

export default function StudioRoutes() {
  return (
    <Routes>
      <Route index element={<StudioListPage />} />
      <Route path=":workspaceId/compositions/:compositionId" element={<ImageCompositionPage />} />
      <Route path=":workspaceId/recipes/:recipeId/versions/:versionId" element={<RecipeWorkflowPage />} />
      <Route path=":workspaceId/exports/:versionId" element={<ExportsPage />} />
      <Route path=":workspaceId/handoffs/:handoffId" element={<HandoffPage />} />
    </Routes>
  );
}
