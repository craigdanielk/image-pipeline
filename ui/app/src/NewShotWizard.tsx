import { useCallback, useEffect, useState } from "react";
import { api, uploadFile } from "./api";
import type { Job, Template } from "./types";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated: (job: Job) => void;
  products: { slug: string; name: string }[];
  /** Show prompt field — workbench developer path only */
  devMode?: boolean;
};

type Step = "product" | "template" | "details";

const TEMPLATE_ICONS: Record<string, string> = {
  plate: "🖼️",
  scene: "✨",
  blank: "📝",
};

export default function NewShotWizard({ open, onClose, onCreated, products, devMode = false }: Props) {
  const [step, setStep] = useState<Step>("product");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [productSlug, setProductSlug] = useState("");
  const [newProductName, setNewProductName] = useState("");
  const [useNewProduct, setUseNewProduct] = useState(false);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [shotName, setShotName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [referencePath, setReferencePath] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const reset = useCallback(() => {
    setStep("product");
    setProductSlug("");
    setNewProductName("");
    setUseNewProduct(false);
    setTemplateId(null);
    setShotName("");
    setPrompt("");
    setReferencePreview(null);
    setReferencePath(null);
    setError("");
  }, []);

  useEffect(() => {
    if (open) {
      api<{ templates: Template[] }>("/api/templates")
        .then((d) => setTemplates(d.templates || []))
        .catch(() => setTemplates([]));
    } else {
      reset();
    }
  }, [open, reset]);

  const slugify = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");

  const effectiveSlug = useNewProduct ? slugify(newProductName) : productSlug;

  const onSelectTemplate = (tpl: Template) => {
    setTemplateId(tpl.id);
    const defaults = tpl.defaults as { spec?: { prompt_template?: string } } | undefined;
    if (defaults?.spec?.prompt_template) {
      setPrompt(defaults.spec.prompt_template);
    }
    setStep("details");
  };

  const onUpload = async (file: File) => {
    if (!effectiveSlug) {
      setError("Select a product first");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const result = await uploadFile(effectiveSlug, file);
      setReferencePath(result.path);
      setReferencePreview(result.url);
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
    }
  };

  const onCreate = async () => {
    if (!effectiveSlug || !shotName.trim()) {
      setError("Product and shot name are required");
      return;
    }
    if (devMode && !prompt.trim()) {
      setError("Prompt required in developer workbench mode");
      return;
    }
    const shotId = slugify(shotName);
    if (!shotId) {
      setError("Invalid shot name");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const data = await api<{ job: Job }>("/api/jobs/create", {
        method: "POST",
        body: JSON.stringify({
          product_slug: effectiveSlug,
          shot_id: shotId,
          template_id: templateId,
          subject: { product_name: useNewProduct ? newProductName : effectiveSlug.replace(/-/g, " ") },
          prompt_template: devMode ? prompt : (prompt || "Studio product render — compiler will resolve from campaign."),
          reference_paths: referencePath ? [referencePath] : undefined,
        }),
      });
      onCreated({ ...data.job, source: "repo" });
      onClose();
      reset();
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  };

  if (!open) return null;

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard" onClick={(e) => e.stopPropagation()}>
        <header className="wizard__header">
          <h2>New Shot</h2>
          <button type="button" className="wizard__close" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="wizard__steps">
          <span className={step === "product" ? "active" : ""}>1. Product</span>
          <span className={step === "template" ? "active" : ""}>2. Template</span>
          <span className={step === "details" ? "active" : ""}>3. Details</span>
        </div>

        {step === "product" && (
          <div className="wizard__body">
            <label className="wizard__label">
              <input
                type="radio"
                checked={!useNewProduct}
                onChange={() => setUseNewProduct(false)}
              />
              Existing product
            </label>
            {!useNewProduct && (
              <select
                className="wizard__input"
                value={productSlug}
                onChange={(e) => setProductSlug(e.target.value)}
              >
                <option value="">Select product…</option>
                {products.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
            <label className="wizard__label">
              <input
                type="radio"
                checked={useNewProduct}
                onChange={() => setUseNewProduct(true)}
              />
              New product
            </label>
            {useNewProduct && (
              <input
                className="wizard__input"
                placeholder="Product name (e.g. PrimiKey USB-A)"
                value={newProductName}
                onChange={(e) => setNewProductName(e.target.value)}
              />
            )}
            <button
              type="button"
              className="btn btn--primary"
              disabled={!effectiveSlug}
              onClick={() => setStep("template")}
            >
              Next
            </button>
          </div>
        )}

        {step === "template" && (
          <div className="wizard__body">
            <div className="template-grid">
              {templates.map((tpl) => (
                <button
                  key={tpl.id}
                  type="button"
                  className={`template-card ${templateId === tpl.id ? "active" : ""}`}
                  onClick={() => onSelectTemplate(tpl)}
                >
                  <span className="template-card__icon">
                    {TEMPLATE_ICONS[tpl.icon || ""] || "🎨"}
                  </span>
                  <span className="template-card__label">{tpl.label}</span>
                  <span className="template-card__desc">{tpl.description}</span>
                </button>
              ))}
            </div>
            <button type="button" className="btn" onClick={() => setStep("product")}>
              Back
            </button>
          </div>
        )}

        {step === "details" && (
          <div className="wizard__body">
            <label className="wizard__field">
              Shot name
              <input
                className="wizard__input"
                placeholder="e.g. Hero Float A"
                value={shotName}
                onChange={(e) => setShotName(e.target.value)}
              />
            </label>
            {devMode && (
            <label className="wizard__field">
              Prompt (developer only)
              <textarea
                className="wizard__textarea"
                rows={6}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </label>
            )}
            <label className="wizard__field">
              Reference image (optional)
              <div className="wizard__upload">
                {referencePreview ? (
                  <div className="wizard__preview">
                    <img src={referencePreview} alt="Reference" />
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => {
                        setReferencePreview(null);
                        setReferencePath(null);
                      }}
                    >
                      Remove
                    </button>
                  </div>
                ) : (
                  <label className="wizard__dropzone">
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) onUpload(f);
                      }}
                    />
                    {uploading ? "Uploading…" : "Drop image or click to upload"}
                  </label>
                )}
              </div>
            </label>
            {error && <p className="wizard__error">{error}</p>}
            <div className="wizard__actions">
              <button type="button" className="btn" onClick={() => setStep("template")}>
                Back
              </button>
              <button
                type="button"
                className="btn btn--primary"
                disabled={creating}
                onClick={onCreate}
              >
                {creating ? "Creating…" : "Create Shot"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
