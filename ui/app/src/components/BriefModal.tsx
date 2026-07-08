import { useEffect, useState } from "react";
import { api, uploadFile } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { BriefDraftResponse, ExecuteResult, ReferenceImage } from "../types";

type Props = {
  productSlug: string;
  onClose: () => void;
  onGenerated?: () => void;
  initialBriefText?: string;
  initialFeedback?: string;
  basedOnLabel?: string;
};

function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .split("-")
      .slice(0, 5)
      .join("-") || "shot"
  );
}

export default function BriefModal({
  productSlug,
  onClose,
  onGenerated,
  initialBriefText = "",
  initialFeedback,
  basedOnLabel,
}: Props) {
  const { tenant } = useTenant();

  const [briefText, setBriefText] = useState(initialBriefText);
  const [mood, setMood] = useState("");
  const [draft, setDraft] = useState<BriefDraftResponse | null>(null);
  const [promptText, setPromptText] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState("");

  const [shotId, setShotId] = useState("");
  const [shotIdTouched, setShotIdTouched] = useState(false);
  const [imgtype, setImgtype] = useState("hero");
  const [aspectRatio, setAspectRatio] = useState("4:3");
  const [resolution, setResolution] = useState("2k");

  const [references, setReferences] = useState<ReferenceImage[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [jobPath, setJobPath] = useState<string | null>(null);

  const [dryRunning, setDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<ExecuteResult | null>(null);
  const [dryRunError, setDryRunError] = useState("");

  const [generating, setGenerating] = useState(false);
  const [generateResult, setGenerateResult] = useState<ExecuteResult | null>(null);
  const [generateError, setGenerateError] = useState("");

  useEffect(() => {
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ references: ReferenceImage[] }>(`/api/products/${productSlug}/references?${params}`)
      .then((d) => setReferences(d.references || []))
      .catch(() => {});
  }, [productSlug, tenant]);

  useEffect(() => {
    if (!shotIdTouched && briefText) setShotId(slugify(briefText));
  }, [briefText, shotIdTouched]);

  const runDraft = async () => {
    setDrafting(true);
    setDraftError("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const d = await api<BriefDraftResponse>(`/api/briefs/draft?${params}`, {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          tenant,
          brief_text: briefText,
          mood: mood || undefined,
          feedback: initialFeedback,
        }),
      });
      setDraft(d);
      setPromptText(d.prompt_template);
      const out = d.suggested_output as Record<string, unknown> | null;
      if (out) {
        if (typeof out.imgtype === "string") setImgtype(out.imgtype);
        if (typeof out.aspect_ratio === "string") setAspectRatio(out.aspect_ratio);
        if (typeof out.resolution === "string") setResolution(out.resolution);
      }
    } catch (e) {
      setDraftError(String(e));
    } finally {
      setDrafting(false);
    }
  };

  const toggleRef = (path: string) => {
    setSelectedRefs((prev) => (prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path]));
  };

  const onUploadRef = async (file: File) => {
    setUploading(true);
    try {
      const uploaded = await uploadFile(productSlug, file);
      setReferences((prev) => [...prev, { path: uploaded.path, name: uploaded.filename, url: uploaded.url }]);
      setSelectedRefs((prev) => [...prev, uploaded.path]);
    } catch (e) {
      setDraftError(String(e));
    } finally {
      setUploading(false);
    }
  };

  const saveJob = async () => {
    setSaving(true);
    setSaveError("");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const res = await api<{ ok: boolean; job: { path: string } }>(`/api/jobs/create?${params}`, {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          shot_id: shotId,
          subject: { view: briefText },
          prompt_template: promptText,
          reference_paths: selectedRefs.length ? selectedRefs : undefined,
          output: { imgtype, aspect_ratio: aspectRatio, resolution, count: 1 },
          engine: draft?.suggested_engine || undefined,
          cad: draft?.cad || undefined,
        }),
      });
      setJobPath(res.job.path);
      // Cost-before-commit without the extra click: estimate immediately so
      // Generate lights up on its own with the credit price attached.
      void runExecute(true, res.job.path);
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const runExecute = async (dryRun: boolean, pathOverride?: string) => {
    const target = pathOverride ?? jobPath;
    if (!target) return;
    const setLoading = dryRun ? setDryRunning : setGenerating;
    const setError = dryRun ? setDryRunError : setGenerateError;
    const setResult = dryRun ? setDryRunResult : setGenerateResult;
    setLoading(true);
    setError("");
    try {
      const res = await api<ExecuteResult>("/api/execute", {
        method: "POST",
        body: JSON.stringify({ job: target, dry_run: dryRun }),
      });
      setResult(res);
      if (!dryRun) onGenerated?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="artifact-modal" role="dialog" aria-modal="true" aria-label="New shot brief" onClick={onClose}>
      <div className="brief-modal__inner" onClick={(e) => e.stopPropagation()}>
        <div className="brief-modal__header">
          <strong>New shot — {productSlug}</strong>
          <button type="button" className="btn btn--sm" onClick={onClose}>
            Close
          </button>
        </div>

        {basedOnLabel && <p className="page-subtitle brief-modal__context">{basedOnLabel}</p>}
        {initialFeedback && (
          <p className="page-subtitle brief-modal__context">
            <strong>Feedback to fix:</strong> {initialFeedback}
          </p>
        )}

        <section className="brief-modal__section">
          <label className="field-label" htmlFor="brief-text">
            What should this shot show?
          </label>
          <textarea
            id="brief-text"
            className="field-input"
            rows={3}
            value={briefText}
            onChange={(e) => setBriefText(e.target.value)}
            placeholder="e.g. kid hero shot on pitch, blue hairband visible, smiling, dark stadium bokeh"
          />
          <label className="field-label" htmlFor="brief-mood" style={{ marginTop: "0.6rem" }}>
            Mood / style (optional)
          </label>
          <input
            id="brief-mood"
            className="field-input"
            value={mood}
            onChange={(e) => setMood(e.target.value)}
            placeholder="e.g. energetic, dramatic dark studio"
          />
          <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
            <button type="button" className="btn btn--primary-solid" onClick={runDraft} disabled={!briefText.trim() || drafting}>
              {drafting ? "Drafting…" : "Draft prompt with AI"}
            </button>
          </div>
          {draftError && <p className="brief-modal__error">{draftError}</p>}
        </section>

        {draft && (
          <>
            <section className="brief-modal__section">
              <label className="field-label" htmlFor="brief-prompt">
                Drafted prompt — review and edit
              </label>
              <textarea
                id="brief-prompt"
                className="field-input artifact-comment"
                rows={6}
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
              />
              <p className="page-subtitle" style={{ margin: "0.35rem 0 0" }}>
                Used {draft.examples_used} example prompt(s) from this product for house style.
                {draft.cad && " CAD spec attached."}
              </p>
            </section>

            <section className="brief-modal__section">
              <label className="field-label">Reference images (optional)</label>
              <div className="brief-modal__refs">
                {references.map((r) => (
                  <button
                    key={r.path}
                    type="button"
                    className={`brief-modal__ref ${selectedRefs.includes(r.path) ? "brief-modal__ref--selected" : ""}`}
                    onClick={() => toggleRef(r.path)}
                  >
                    <img src={r.url} alt={r.name} loading="lazy" />
                  </button>
                ))}
                <label className="brief-modal__ref brief-modal__ref--upload">
                  {uploading ? "…" : "+ Upload"}
                  <input
                    type="file"
                    accept="image/*"
                    style={{ display: "none" }}
                    disabled={uploading}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) onUploadRef(f);
                    }}
                  />
                </label>
              </div>
            </section>

            <section className="brief-modal__section brief-modal__output-row">
              <div>
                <label className="field-label" htmlFor="brief-imgtype">
                  Image type
                </label>
                <input id="brief-imgtype" className="field-input" value={imgtype} onChange={(e) => setImgtype(e.target.value)} />
              </div>
              <div>
                <label className="field-label" htmlFor="brief-aspect">
                  Aspect ratio
                </label>
                <select id="brief-aspect" className="field-select" value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}>
                  <option value="1:1">1:1</option>
                  <option value="4:3">4:3</option>
                  <option value="3:4">3:4</option>
                  <option value="16:9">16:9</option>
                  <option value="9:16">9:16</option>
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="brief-res">
                  Resolution
                </label>
                <select id="brief-res" className="field-select" value={resolution} onChange={(e) => setResolution(e.target.value)}>
                  <option value="1k">1k</option>
                  <option value="2k">2k</option>
                  <option value="4k">4k</option>
                </select>
              </div>
            </section>

            <section className="brief-modal__section">
              <label className="field-label" htmlFor="brief-shot-id">
                Shot id (unique job file name)
              </label>
              <input
                id="brief-shot-id"
                className="field-input"
                value={shotId}
                onChange={(e) => {
                  setShotIdTouched(true);
                  setShotId(e.target.value);
                }}
              />
              <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
                <button type="button" className="btn btn--primary-solid" onClick={saveJob} disabled={!shotId.trim() || saving || !!jobPath}>
                  {saving ? "Saving…" : jobPath ? "Saved" : "Save job"}
                </button>
              </div>
              {saveError && <p className="brief-modal__error">{saveError}</p>}
            </section>
          </>
        )}

        {jobPath && (
          <section className="brief-modal__section">
            <p className="page-subtitle" style={{ margin: "0 0 0.5rem" }}>
              {jobPath}
            </p>
            <div className="asset-card__actions">
              <button type="button" className="btn" onClick={() => runExecute(true)} disabled={dryRunning}>
                {dryRunning ? "Estimating…" : "Re-estimate"}
              </button>
              <button
                type="button"
                className="btn btn--generate"
                onClick={() => runExecute(false)}
                disabled={!dryRunResult || generating || dryRunning}
                title={!dryRunResult && !dryRunning ? "Cost estimate failed — hit Re-estimate" : undefined}
              >
                {generating
                  ? "Generating…"
                  : dryRunning
                    ? "Generate (pricing…)"
                    : (() => {
                        const credits = (dryRunResult?.result as { credits?: number } | undefined)?.credits;
                        return credits != null ? `Generate · ${credits} credits` : "Generate";
                      })()}
              </button>
            </div>
            {dryRunError && <p className="brief-modal__error">{dryRunError}</p>}
            {dryRunResult && (
              <pre className="brief-modal__result">{JSON.stringify(dryRunResult.result ?? dryRunResult.raw_stdout, null, 2)}</pre>
            )}
            {generateError && <p className="brief-modal__error">{generateError}</p>}
            {generateResult?.outputs && generateResult.outputs.length > 0 && (
              <div className="brief-modal__refs">
                {generateResult.outputs.map((o) => (
                  <div key={o.path} className="brief-modal__ref">
                    <img src={o.url} alt={o.name} />
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
