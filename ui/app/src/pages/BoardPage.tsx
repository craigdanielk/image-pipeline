import { useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, uploadFile } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { Campaign, Operation, Product, ProductSpecV2 } from "../types";

const OUTPUT_TYPES = [
  { id: "pdp", label: "PDP" },
  { id: "lifestyle", label: "Lifestyle" },
  { id: "hero", label: "Hero" },
  { id: "social", label: "Social" },
];

// The six runtime nodes are visible from job start — no hidden pipeline. Each
// unlocks only when its input ports are satisfied per node_registry
// (validate_node_ports server-side). "artifacts.load" is the output-as-input
// reuse node: an approved gallery artifact attaches here and feeds
// reference.resolve in a new chain.
const RUNTIME_CHAIN: { nodeType: string; label: string }[] = [
  { nodeType: "intake.load", label: "Load job" },
  { nodeType: "jobspec.validate", label: "Validate spec" },
  { nodeType: "prompt.build", label: "Build prompt" },
  { nodeType: "reference.resolve", label: "Resolve refs" },
  { nodeType: "engine.generate", label: "Generate" },
  { nodeType: "output.persist", label: "Persist" },
];

type RuntimeNodeSpec = {
  node_type: string;
  description: string;
  ports_in: Record<string, { type: string; required: boolean }>;
  ports_out: Record<string, { type: string; required: boolean }>;
};

type CampaignDetail = Campaign & {
  scene?: { description?: string; lighting?: string; camera?: string; composition?: string; background?: string };
  campaign?: { mood?: string; audience?: string; style?: string };
};

/** The reference photo the ProductSpec's geometry was actually derived from — prefer an
 * explicit product_ref/flatlay/cad-labeled role, else fall back to the first reference. */
function cadReferenceOf(spec: ProductSpecV2 | null | undefined): { asset: string; role: string } | null {
  const refs = spec?.references || [];
  if (refs.length === 0) return null;
  const preferred = refs.find((r) => /product_ref|flatlay|cad/i.test(r.role || ""));
  return preferred || refs[0];
}

function mediaUrl(assetPath: string): string {
  return `/media?path=${encodeURIComponent(assetPath)}`;
}

function ProductNode({ data }: NodeProps) {
  const d = data as {
    product?: Product;
    productDetail?: ProductSpecV2 | null;
    fallbackAsset?: string | null;
    hasSpec: boolean;
    onOpen: () => void;
    onExtract: () => void;
  };
  const p = d.product;
  const cadRef = cadReferenceOf(d.productDetail);
  // Knowledge specs store reference paths RELATIVE to the product's own dir
  // (refs/flatlay.png) — the browser can't resolve those against /media, so
  // an absolute path from the references API always wins. This was the
  // "Products tab renders, Generate tab doesn't" split-brain.
  const specAsset = cadRef?.asset && cadRef.asset.startsWith("/") ? cadRef.asset : null;
  const asset = specAsset || d.fallbackAsset || null;
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => setImgFailed(false), [asset]);
  return (
    <div className="board-node board-node--product" onClick={d.onOpen}>
      <Handle type="source" position={Position.Right} />
      <div className="board-node__image">
        {asset && !imgFailed ? (
          <img src={mediaUrl(asset)} alt={`${p?.name || "product"} — CAD source`} onError={() => setImgFailed(true)} />
        ) : (
          <span className="board-node__image-empty">
            {!p ? "" : asset ? "source file missing on disk" : "no source image on file"}
          </span>
        )}
      </div>
      <div className="board-node__kicker">
        PRODUCT · CAD SOURCE{" "}
        {p &&
          (d.hasSpec ? (
            <span className="cad-badge cad-badge--ok">SPEC ✓</span>
          ) : (
            <button
              type="button"
              className="cad-badge cad-badge--missing nodrag"
              onClick={(e) => {
                e.stopPropagation();
                d.onExtract();
              }}
              title="No CAD spec yet — extract one from photos"
            >
              NO SPEC — extract
            </button>
          ))}
      </div>
      <div className="board-node__title">{p?.name || "Select a product…"}</div>
      <div className="board-node__meta">
        {p?.geometry_description ? p.geometry_description.slice(0, 64) + (p.geometry_description.length > 64 ? "…" : "") : "no geometry set"}
      </div>
      <div className="board-node__foot">{p ? `${p.reference_count ?? 0} refs · click to inspect` : ""}</div>
    </div>
  );
}

function CampaignNode({ data }: NodeProps) {
  const d = data as { campaign?: CampaignDetail; onOpen: () => void };
  const c = d.campaign;
  return (
    <div className="board-node board-node--campaign" onClick={d.onOpen}>
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">CAMPAIGN</div>
      <div className="board-node__title">{c?.label || "Select a campaign…"}</div>
      <div className="board-node__meta">
        {c?.scene?.description ? c.scene.description.slice(0, 64) + (c.scene.description.length > 64 ? "…" : "") : c?.scene?.lighting || "no scene set"}
      </div>
      <div className="board-node__foot">{c ? "click to inspect" : ""}</div>
    </div>
  );
}

function OperationNode({ data }: NodeProps) {
  const d = data as {
    label: string;
    onRemove: () => void;
    onRerun: () => void;
    running: boolean;
    runStatus: string;
    canRerun: boolean;
    isCustom?: boolean;
  };
  const rerunTitle = d.isCustom
    ? "Custom operations aren't recognized graph ops, so they're skipped when compiling the board and can't be re-run individually."
    : undefined;
  return (
    <div className="board-node board-node--op">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">OPERATION</div>
      <div className="board-node__title">{d.label}</div>
      {d.runStatus && <div className="board-node__status">{d.runStatus}</div>}
      {d.isCustom && (
        <div className="board-node__status">Custom op — excluded from compile, can't be re-run here.</div>
      )}
      <div className="board-node__op-actions">
        <button
          type="button"
          className="board-node__rerun nodrag"
          disabled={d.running || !d.canRerun}
          onClick={d.onRerun}
          title={rerunTitle}
        >
          {d.running ? "Running…" : "↻ Re-run"}
        </button>
        <button type="button" className="board-node__remove nodrag" onClick={d.onRemove}>
          ✕ remove
        </button>
      </div>
    </div>
  );
}

function OutputNode({ data }: NodeProps) {
  const d = data as {
    outputs: Set<string>;
    toggleOutput: (id: string) => void;
    credits: number | null;
    status: string;
    loading: boolean;
    onEstimate: () => void;
    onGenerate: () => void;
    canRun: boolean;
  };
  return (
    <div className="board-node board-node--output">
      <Handle type="target" position={Position.Left} />
      <div className="board-node__kicker">OUTPUT</div>
      <div className="board-node__chip-row nodrag">
        {OUTPUT_TYPES.map((o) => (
          <button
            key={o.id}
            type="button"
            className={`chip chip--sm ${d.outputs.has(o.id) ? "chip--on" : ""}`}
            onClick={() => d.toggleOutput(o.id)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {d.credits != null && <div className="board-node__credits">~{d.credits} credits</div>}
      <div className="board-node__status">{d.status}</div>
      <div className="board-node__actions nodrag">
        <button type="button" className="btn btn--sm" disabled={d.loading || !d.canRun} onClick={d.onEstimate}>
          Estimate
        </button>
        <button
          type="button"
          className="btn btn--sm btn--primary-solid"
          disabled={d.loading || !d.canRun}
          onClick={d.onGenerate}
        >
          Generate
        </button>
      </div>
    </div>
  );
}

function RuntimeNode({ data }: NodeProps) {
  const d = data as {
    label: string;
    nodeType: string;
    index: number;
    unlocked: boolean;
    waitingOn: string[];
    runStatus?: string;
    promptPreview?: string;
    onOpen: () => void;
  };
  return (
    <div
      className={`board-node board-node--runtime ${d.unlocked ? "" : "board-node--runtime-locked"}`}
      onClick={d.onOpen}
    >
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">
        STEP {d.index + 1}/6 {d.unlocked ? "· READY" : "· LOCKED"}
      </div>
      <div className="board-node__title">{d.label}</div>
      <div className="board-node__meta">{d.nodeType}</div>
      {!d.unlocked && d.waitingOn.length > 0 && (
        <div className="board-node__status">waiting on: {d.waitingOn.join(", ")}</div>
      )}
      {d.promptPreview && (
        <div className="board-node__prompt" title="Compiled prompt — click node for full text">
          “{d.promptPreview}”
        </div>
      )}
      {d.runStatus && <div className="board-node__status">{d.runStatus}</div>}
      <div className="board-node__foot">click to inspect</div>
    </div>
  );
}

type Shot = { id: string; label: string; path: string };

function ShotNode({ data }: NodeProps) {
  const d = data as {
    shots: Shot[];
    selectedShotId: string | null;
    onSelect: (s: Shot) => void;
    compiled: boolean;
    busy: boolean;
    status: string;
    onRun: () => void;
  };
  return (
    <div className="board-node board-node--shot">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">SHOT · WHICH IMAGE TO MAKE</div>
      {d.shots.length === 0 ? (
        <div className="board-node__meta">no authored shots for this product yet</div>
      ) : (
        <div className="board-node__chip-row nodrag">
          {d.shots.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`chip chip--sm ${d.selectedShotId === s.id ? "chip--on" : ""}`}
              onClick={() => d.onSelect(s)}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      {d.status && <div className="board-node__status">{d.status}</div>}
      {d.compiled && (
        <div className="board-node__actions nodrag">
          <button type="button" className="btn btn--sm btn--primary-solid" disabled={d.busy} onClick={d.onRun}>
            {d.busy ? "Running…" : "Run this shot"}
          </button>
        </div>
      )}
    </div>
  );
}

function ArtifactNode({ data }: NodeProps) {
  const d = data as { id: string; name: string; path?: string; onRemove: () => void };
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <div className="board-node board-node--artifact">
      <Handle type="source" position={Position.Right} />
      <div className="board-node__image board-node__image--sm">
        {d.path && !imgFailed ? (
          <img src={mediaUrl(d.path)} alt={d.name} onError={() => setImgFailed(true)} />
        ) : (
          <span className="board-node__image-empty">{d.name}</span>
        )}
      </div>
      <div className="board-node__kicker">REUSED OUTPUT</div>
      <div className="board-node__meta" title={d.name}>
        {d.name}
      </div>
      <button type="button" className="board-node__remove nodrag" onClick={d.onRemove}>
        ✕ remove
      </button>
    </div>
  );
}

const nodeTypes = {
  productNode: ProductNode,
  campaignNode: CampaignNode,
  operationNode: OperationNode,
  outputNode: OutputNode,
  runtimeNode: RuntimeNode,
  artifactNode: ArtifactNode,
  shotNode: ShotNode,
};

export default function BoardPage() {
  const { tenant } = useTenant();
  const [products, setProducts] = useState<Product[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);

  const [productSlug, setProductSlug] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [activeOps, setActiveOps] = useState<string[]>([]);
  const [customOpLabels, setCustomOpLabels] = useState<Record<string, string>>({});
  const [customOpDraft, setCustomOpDraft] = useState("");
  const [outputs, setOutputs] = useState<Set<string>>(new Set(["hero"]));

  const [credits, setCredits] = useState<number | null>(null);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);

  const [inspector, setInspector] = useState<{ kind: "product" | "campaign"; data: unknown } | null>(null);
  const [inspectorLoading, setInspectorLoading] = useState(false);

  // Fetched eagerly (not just on inspector-open) so the Product node can always show
  // its CAD source image/reference the moment a product is selected on the board.
  const [productDetail, setProductDetail] = useState<ProductSpecV2 | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Real per-node compile + re-run wiring (Task 6). `compileDir` is refreshed
  // by the debounced effect below whenever the board's selection changes; each
  // Operation node re-runs itself against that compile via /api/graph-runs,
  // scoped with target_node_id so only that node (+ its real ancestors) execute.
  const [compileDir, setCompileDir] = useState<string | null>(null);
  const [compileError, setCompileError] = useState("");
  const [opRunning, setOpRunning] = useState<Record<string, boolean>>({});
  const [opRunStatus, setOpRunStatus] = useState<Record<string, string>>({});
  const activeStreamsRef = useRef<Set<EventSource>>(new Set());

  // Runtime chain (progressive unlock) + output-as-input reuse (artifacts.load).
  const [runtimeSpecs, setRuntimeSpecs] = useState<RuntimeNodeSpec[]>([]);
  const [loadedArtifacts, setLoadedArtifacts] = useState<{ id: string; name: string; path?: string }[]>([]);
  const [wiringError, setWiringError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerArtifacts, setPickerArtifacts] = useState<
    { id: string; name: string; path: string; url: string; product_slug?: string }[]
  >([]);
  const [runtimeInspect, setRuntimeInspect] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(() => localStorage.getItem("board-help-seen") !== "1");
  useEffect(() => {
    if (!showHelp) localStorage.setItem("board-help-seen", "1");
  }, [showHelp]);

  // SHOT — the authored composition to produce (the product's .job.json specs).
  // Selecting one compiles it (dry-run) so the real prompt lands on the rail;
  // Run executes the compiled graph.
  const [selectedShot, setSelectedShot] = useState<Shot | null>(null);
  const [shotCompile, setShotCompile] = useState<{ compile_dir: string; prompt: string } | null>(null);
  const [shotBusy, setShotBusy] = useState(false);
  const [shotStatus, setShotStatus] = useState("");

  const selectShot = async (s: Shot) => {
    setSelectedShot(s);
    setShotCompile(null);
    setShotStatus("Compiling…");
    try {
      const d = await api<{ compile_dir: string; prompt: string }>("/api/compile", {
        method: "POST",
        body: JSON.stringify({ job: s.path, dry_run: true }),
      });
      setShotCompile({ compile_dir: d.compile_dir, prompt: d.prompt || "" });
      setShotStatus(d.prompt ? "Compiled — prompt on the rail below" : "Compiled");
    } catch (e) {
      setShotStatus(String(e));
    }
  };

  // Greenfield: brand-new product from photos. Upload → vision CAD extraction
  // → confirm the drafted spec → save → it becomes the PRODUCT (CAD) node.
  const [newProductOpen, setNewProductOpen] = useState(false);
  const [npName, setNpName] = useState("");
  const [npNotes, setNpNotes] = useState("");
  const [npFiles, setNpFiles] = useState<File[]>([]);
  const [npDraft, setNpDraft] = useState<Record<string, unknown> | null>(null);
  const [npBusy, setNpBusy] = useState(false);
  const [npStatus, setNpStatus] = useState("");
  // When extracting for an EXISTING spec-less product, save under its slug so
  // the CAD node flips to SPEC ✓ instead of minting a duplicate product.
  const [npTargetSlug, setNpTargetSlug] = useState<string | null>(null);
  const [detailRefresh, setDetailRefresh] = useState(0);

  const openExtractForCurrent = () => {
    setNpTargetSlug(productSlug || null);
    setNpName(product?.name || productSlug || "");
    setNewProductOpen(true);
  };

  const npGeometry = (): string => {
    const d = npDraft as { geometry_description?: string; product?: { geometry?: { description?: string } } } | null;
    return d?.geometry_description || d?.product?.geometry?.description || "";
  };

  const extractCad = async () => {
    if (!npFiles.length) return;
    setNpBusy(true);
    setNpStatus("Extracting CAD spec from photos (vision model — ~30s)…");
    try {
      const form = new FormData();
      form.append("name", npName);
      form.append("notes", npNotes);
      for (const f of npFiles) form.append("photos", f);
      const res = await fetch("/api/knowledge/products/extract", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      setNpDraft(data.draft || null);
      setNpStatus("Draft ready — confirm the geometry below, then save.");
    } catch (e) {
      setNpStatus(String(e));
    } finally {
      setNpBusy(false);
    }
  };

  const saveNewProduct = async () => {
    const geometry = npGeometry();
    const name = npName || String((npDraft as { name?: string } | null)?.name || "");
    if (!name || !geometry) {
      setNpStatus("Need a name and extracted geometry before saving.");
      return;
    }
    setNpBusy(true);
    setNpStatus("Saving product…");
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const d = await api<{ product: { id: string } }>(`/api/knowledge/products?${params}`, {
        method: "POST",
        body: JSON.stringify({
          ...(npTargetSlug ? { id: npTargetSlug } : {}),
          name,
          geometry_description: geometry,
        }),
      });
      const slug = d.product.id;
      for (const f of npFiles) {
        try {
          await uploadFile(slug, f);
        } catch {
          /* refs upload is best-effort; spec already saved */
        }
      }
      const p = await api<{ products: Product[] }>(`/api/products?${params}`);
      setProducts(p.products || []);
      setProductSlug(slug);
      setDetailRefresh((n) => n + 1);
      setNewProductOpen(false);
      setNpTargetSlug(null);
      setNpName(""); setNpNotes(""); setNpFiles([]); setNpDraft(null); setNpStatus("");
    } catch (e) {
      setNpStatus(String(e));
    } finally {
      setNpBusy(false);
    }
  };

  // New campaign — the style/scene target, confirmed by the operator before use.
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const [ncLabel, setNcLabel] = useState("");
  const [ncScene, setNcScene] = useState("");
  const [ncMood, setNcMood] = useState("studio");
  const [ncStatus, setNcStatus] = useState("");

  const saveNewCampaign = async () => {
    if (!productSlug || !ncLabel || !ncScene) {
      setNcStatus("Pick a product first, then give the campaign a label and scene description.");
      return;
    }
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const d = await api<{ campaign: { id: string } }>(`/api/knowledge/campaigns?${params}`, {
        method: "POST",
        body: JSON.stringify({
          product_spec_id: productSlug,
          label: ncLabel,
          scene_description: ncScene,
          mood: ncMood,
        }),
      });
      const c = await api<{ campaigns: Campaign[] }>(`/api/campaigns?${params}`);
      setCampaigns(c.campaigns || []);
      setCampaignId(d.campaign.id);
      setNewCampaignOpen(false);
      setNcLabel(""); setNcScene(""); setNcStatus("");
    } catch (e) {
      setNcStatus(String(e));
    }
  };

  const runShot = async () => {
    if (!shotCompile) return;
    setShotBusy(true);
    setShotStatus("Generating…");
    try {
      const d = await api<{ outputs?: unknown[]; result?: unknown }>("/api/execute", {
        method: "POST",
        body: JSON.stringify({ compile_dir: shotCompile.compile_dir, dry_run: false }),
      });
      const n = (d.outputs || []).length;
      setShotStatus(n ? `Done — ${n} output${n === 1 ? "" : "s"} in Assets` : "Run finished — check Runs/Assets");
    } catch (e) {
      setShotStatus(String(e));
    } finally {
      setShotBusy(false);
    }
  };
  // Some products have reference photos on disk but no knowledge-store spec —
  // fall back to the references API so the Product node always shows an image
  // and the inspector always has something to render.
  const [fallbackAsset, setFallbackAsset] = useState<string | null>(null);
  const [productRefs, setProductRefs] = useState<{ path?: string; asset?: string; role?: string; name?: string }[]>([]);

  useEffect(() => {
    api<{ nodes: RuntimeNodeSpec[] }>("/api/board/runtime-chain")
      .then((d) => setRuntimeSpecs(d.nodes || []))
      .catch(() => setRuntimeSpecs([]));
  }, []);

  const specByType = (t: string) => runtimeSpecs.find((s) => s.node_type === t);

  // Progressive unlock: walk the linear chain accumulating produced output port
  // types. A node unlocks once every *required* input port type is available
  // upstream. reference.resolve additionally unlocks when an artifacts.load node
  // has been attached (output-as-input reuse feeds its compile_artifacts port).
  const produced = new Set<string>();
  if (loadedArtifacts.length > 0) {
    const al = specByType("artifacts.load");
    Object.values(al?.ports_out || {}).forEach((p) => produced.add(p.type));
  }
  const unlockState = RUNTIME_CHAIN.map(({ nodeType }) => {
    const spec = specByType(nodeType);
    const required = Object.values(spec?.ports_in || {}).filter((p) => p.required);
    const unlocked = required.every((p) => produced.has(p.type));
    Object.values(spec?.ports_out || {}).forEach((p) => produced.add(p.type));
    // Selecting a product+campaign satisfies the intake source for job start;
    // a compiled shot satisfies the whole chain (compile resolved every port).
    const gated = nodeType === "intake.load" ? !!productSlug && !!campaignId : unlocked;
    return { nodeType, unlocked: gated || !!shotCompile };
  });

  // Client-side wiring guard: validate an artifacts.load node's ports against
  // the compiler contract (node_registry.validate_node_ports) BEFORE attaching.
  // The server re-validates via POST /api/board/artifacts-load — invalid wiring
  // is rejected on both sides, never silently dropped.
  const attachArtifact = async (id: string, name: string) => {
    setWiringError("");
    try {
      await api("/api/board/validate-wiring", {
        method: "POST",
        body: JSON.stringify({
          node_type: "artifacts.load",
          ports: { out: { artifacts: { type: "compile_artifacts" } } },
        }),
      });
      const res = await api<{ node: { params?: { path?: string } }; artifact: { id: string; path?: string } }>(
        "/api/board/artifacts-load",
        { method: "POST", body: JSON.stringify({ artifact_id: id, ...(tenant ? { tenant } : {}) }) },
      );
      setLoadedArtifacts((prev) =>
        prev.some((a) => a.id === res.artifact.id)
          ? prev
          : [...prev, { id: res.artifact.id, name, path: res.artifact.path }],
      );
      setPickerOpen(false);
    } catch (e) {
      setWiringError(String(e));
    }
  };

  const openArtifactPicker = async () => {
    setPickerOpen(true);
    if (pickerArtifacts.length) return;
    try {
      const params = withTenant(new URLSearchParams({ status: "approved", limit: "60" }), tenant);
      const d = await api<{ artifacts: { id: string; name: string; path: string; url: string; product_slug?: string }[] }>(
        `/api/artifacts?${params}`,
      );
      setPickerArtifacts(d.artifacts || []);
    } catch (e) {
      setWiringError(String(e));
    }
  };

  useEffect(() => {
    // Tenant-scoped: without the tenant param the server falls back to the
    // service repo's own jobs/knowledge dirs and legacy products leak in.
    const params = withTenant(new URLSearchParams(), tenant);
    Promise.all([
      api<{ products: Product[] }>(`/api/products?${params}`),
      api<{ campaigns: Campaign[] }>(`/api/campaigns?${params}`),
      api<{ operations: Operation[] }>(`/api/operations?${params}`),
    ]).then(([p, c, o]) => {
      setProducts(p.products || []);
      setCampaigns(c.campaigns || []);
      setOperations(o.operations || []);
      if (p.products?.[0]) setProductSlug(p.products[0].slug);
      if (c.campaigns?.[0]) setCampaignId(c.campaigns[0].id);
    });
  }, [tenant]);

  useEffect(() => {
    setProductDetail(null);
    setFallbackAsset(null);
    setSelectedShot(null);
    setShotCompile(null);
    setShotStatus("");
    if (!productSlug) return;
    let cancelled = false;
    const params = withTenant(new URLSearchParams(), tenant);
    api<{ product: ProductSpecV2; source: string }>(`/api/products/${productSlug}?${params}`)
      .then((d) => {
        if (!cancelled && d.source === "knowledge") setProductDetail(d.product);
      })
      .catch(() => {
        if (!cancelled) setProductDetail(null);
      });
    // Fallback thumb: the refs directory is the ground truth even when no
    // knowledge-store spec exists for this product.
    api<{ references: { path?: string; asset?: string; role?: string }[] }>(
      `/api/products/${productSlug}/references?${params}`,
    )
      .then((d) => {
        if (cancelled) return;
        const refs = d.references || [];
        setProductRefs(refs);
        const preferred = refs.find((r) => /product|flatlay|cad|main/i.test(r.role || r.path || "")) || refs[0];
        setFallbackAsset(preferred?.path || preferred?.asset || null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [productSlug, tenant, detailRefresh]);

  const openProductInspector = () => {
    if (!productSlug) return;
    setInspector({ kind: "product", data: productDetail });
  };

  // Keep an already-open product inspector in sync once the eager fetch resolves
  // (covers the case where the node was clicked before productDetail arrived).
  useEffect(() => {
    setInspector((prev) => (prev?.kind === "product" ? { kind: "product", data: productDetail } : prev));
  }, [productDetail]);

  const openCampaignInspector = async () => {
    if (!campaignId) return;
    setInspectorLoading(true);
    setInspector({ kind: "campaign", data: null });
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const d = await api<{ campaign: CampaignDetail }>(`/api/knowledge/campaigns/${campaignId}?${params}`);
      setInspector({ kind: "campaign", data: d.campaign });
    } finally {
      setInspectorLoading(false);
    }
  };

  const toggleOutput = (id: string) => {
    setOutputs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const addOp = (opId: string) => {
    setActiveOps((prev) => (prev.includes(opId) ? prev : [...prev, opId]));
  };
  const removeOp = (opId: string) => {
    setActiveOps((prev) => prev.filter((o) => o !== opId));
    setCustomOpLabels((prev) => {
      if (!(opId in prev)) return prev;
      const next = { ...prev };
      delete next[opId];
      return next;
    });
    setOpRunning((prev) => {
      if (!(opId in prev)) return prev;
      const next = { ...prev };
      delete next[opId];
      return next;
    });
    setOpRunStatus((prev) => {
      if (!(opId in prev)) return prev;
      const next = { ...prev };
      delete next[opId];
      return next;
    });
  };
  const addCustomOp = () => {
    const label = customOpDraft.trim();
    if (!label) return;
    const id =
      "custom-" +
      label
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") +
      "-" +
      Date.now().toString(36).slice(-4);
    setCustomOpLabels((prev) => ({ ...prev, [id]: label }));
    addOp(id);
    setCustomOpDraft("");
  };
  const opLabel = (opId: string) => customOpLabels[opId] || operations.find((o) => o.id === opId)?.label || opId;

  const canRun = !!productSlug && !!campaignId && outputs.size > 0;

  const runIntent = async (dryRun: boolean) => {
    if (!canRun) return;
    setLoading(true);
    setStatus(dryRun ? "Estimating…" : "Compiling and running…");
    try {
      const data = await api<{ credits?: number; message?: string; job_path?: string }>("/api/generate/intent", {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          campaign_id: campaignId,
          output_types: [...outputs],
          changes: activeOps,
          dry_run: dryRun,
        }),
      });
      if (dryRun) setCredits(data.credits ?? null);
      setStatus(data.message || (dryRun ? "Estimate ready" : `Created ${data.job_path}`));
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  };

  // Debounced compile-on-change: whenever product/campaign/operations/outputs
  // settle for 500ms, recompile the board's real graph-v1 chain so per-node
  // re-run has a fresh compile_dir + real node ids (`generate-{i}-{opId}`) to
  // target. Cheap no-op guard: nothing to compile without product+campaign+
  // at least one output type selected.
  useEffect(() => {
    if (!productSlug || !campaignId || outputs.size === 0) return;
    const handle = setTimeout(() => {
      api<{ compile_dir: string; graph: { nodes: unknown[]; edges: unknown[] } }>("/api/board/compile", {
        method: "POST",
        body: JSON.stringify({
          product_slug: productSlug,
          campaign_id: campaignId,
          output_types: [...outputs],
          operations: activeOps.filter((id) => !id.startsWith("custom-")).map((id) => ({ op: id })),
        }),
      })
        .then((d) => {
          setCompileDir(d.compile_dir);
          setCompileError("");
        })
        .catch((e) => setCompileError(String(e)));
    }, 500);
    return () => clearTimeout(handle);
  }, [productSlug, campaignId, activeOps, outputs]);

  // Close every open SSE connection on unmount so re-runs from a previous
  // mount/compile never leak a socket or keep firing state updates.
  useEffect(() => {
    return () => {
      activeStreamsRef.current.forEach((es) => es.close());
      activeStreamsRef.current.clear();
    };
  }, []);

  const runNode = (opId: string, graphNodeId: string) => {
    if (!compileDir) return;
    setOpRunning((prev) => ({ ...prev, [opId]: true }));
    setOpRunStatus((prev) => ({ ...prev, [opId]: "Running…" }));
    api<{ graph_run_id: string }>("/api/graph-runs", {
      method: "POST",
      body: JSON.stringify({ compile_dir: compileDir, target_node_id: graphNodeId, dry_run: false }),
    })
      .then(({ graph_run_id }) => {
        const es = new EventSource(`/api/graph-runs/${graph_run_id}/stream`);
        activeStreamsRef.current.add(es);
        const finish = (status: string) => {
          setOpRunning((prev) => ({ ...prev, [opId]: false }));
          setOpRunStatus((prev) => ({ ...prev, [opId]: status }));
          activeStreamsRef.current.delete(es);
          es.close();
        };
        es.onmessage = (evt) => {
          const payload = JSON.parse(evt.data);
          // Node-scoped runs (target_node_id set) still emit one status event
          // per real ancestor as they execute in order — only the overall-run
          // event (node_id === null) or an event for *this* node marks done;
          // an ancestor's own "completed" isn't this node finishing.
          if (!["completed", "failed", "partial"].includes(payload.status)) return;
          if (payload.node_id === null || payload.node_id === graphNodeId) finish(payload.status);
        };
        es.onerror = () => finish("failed");
      })
      .catch((e) => {
        setOpRunning((prev) => ({ ...prev, [opId]: false }));
        setOpRunStatus((prev) => ({ ...prev, [opId]: String(e) }));
      });
  };

  const product = products.find((p) => p.slug === productSlug);
  const campaign = campaigns.find((c) => c.id === campaignId);

  // Rebuild node `data` whenever the underlying selection changes, but reuse each
  // node's *existing* position from the previous render so a drag in progress
  // (or already committed via onNodesChange) is never overwritten — only brand-new
  // nodes get a computed default position.
  useEffect(() => {
    setNodes((prev) => {
      const posOf = (id: string, fallback: { x: number; y: number }) =>
        prev.find((n) => n.id === id)?.position ?? fallback;

      const opNodes: Node[] = activeOps.map((opId, i) => {
        const isCustom = opId.startsWith("custom-");
        return {
          id: `op-${opId}`,
          type: "operationNode",
          position: posOf(`op-${opId}`, { x: 340, y: 40 + i * 110 }),
          data: {
            label: opLabel(opId),
            onRemove: () => removeOp(opId),
            onRerun: () => runNode(opId, `generate-${i}-${opId}`),
            running: !!opRunning[opId],
            runStatus: opRunStatus[opId] || "",
            canRerun: !isCustom && !!compileDir,
            isCustom,
          },
        };
      });
      const outputX = activeOps.length ? 660 : 340;

      // Runtime rail: the six compiler nodes live ON the canvas, left to
      // right beneath the intent row, locked until their ports are satisfied.
      const runtimeNodes: Node[] = RUNTIME_CHAIN.map(({ nodeType, label }, i) => {
        const spec = specByType(nodeType);
        const state = unlockState[i];
        const waitingOn = state.unlocked
          ? []
          : Object.entries(spec?.ports_in || {})
              .filter(([, p]) => p.required)
              .map(([port]) => port);
        return {
          id: `rt-${nodeType}`,
          type: "runtimeNode",
          position: posOf(`rt-${nodeType}`, { x: i * 250, y: 430 }),
          data: {
            label,
            nodeType,
            index: i,
            unlocked: state.unlocked,
            waitingOn,
            promptPreview:
              nodeType === "prompt.build" && shotCompile?.prompt
                ? shotCompile.prompt.slice(0, 110) + (shotCompile.prompt.length > 110 ? "…" : "")
                : undefined,
            onOpen: () => setRuntimeInspect(nodeType),
          },
        };
      });

      const shots: Shot[] = ((product as unknown as { shots?: Shot[] })?.shots || []).slice(0, 12);

      const artifactNodes: Node[] = loadedArtifacts.map((a, i) => ({
        id: `art-${a.id}`,
        type: "artifactNode",
        position: posOf(`art-${a.id}`, { x: 250 * 2.5, y: 640 + i * 40 }),
        data: {
          id: a.id,
          name: a.name,
          path: a.path,
          onRemove: () => setLoadedArtifacts((prev) => prev.filter((x) => x.id !== a.id)),
        },
      }));

      return [
        {
          id: "product",
          type: "productNode",
          position: posOf("product", { x: 0, y: 0 }),
          data: {
            product,
            productDetail,
            fallbackAsset,
            hasSpec: !!productDetail,
            onOpen: openProductInspector,
            onExtract: openExtractForCurrent,
          },
        },
        {
          id: "campaign",
          type: "campaignNode",
          position: posOf("campaign", { x: 0, y: 220 }),
          data: { campaign, onOpen: openCampaignInspector },
        },
        {
          id: "shot",
          type: "shotNode",
          position: posOf("shot", { x: 340, y: -140 }),
          data: {
            shots,
            selectedShotId: selectedShot?.id || null,
            onSelect: selectShot,
            compiled: !!shotCompile,
            busy: shotBusy,
            status: shotStatus,
            onRun: runShot,
          },
        },
        ...opNodes,
        {
          id: "output",
          type: "outputNode",
          position: posOf("output", { x: outputX, y: 110 }),
          data: {
            outputs,
            toggleOutput,
            credits,
            status,
            loading,
            canRun,
            onEstimate: () => runIntent(true),
            onGenerate: () => runIntent(false),
          },
        },
        ...runtimeNodes,
        ...artifactNodes,
      ];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    product,
    productDetail,
    fallbackAsset,
    campaign,
    activeOps,
    customOpLabels,
    operations,
    outputs,
    credits,
    status,
    loading,
    canRun,
    compileDir,
    opRunning,
    opRunStatus,
    runtimeSpecs,
    loadedArtifacts,
    selectedShot,
    shotCompile,
    shotBusy,
    shotStatus,
  ]);

  useEffect(() => {
    const chain = ["product", ...activeOps.map((o) => `op-${o}`), "output"];
    const es: Edge[] = [];
    for (let i = 0; i < chain.length - 1; i++) {
      es.push({ id: `e-${chain[i]}-${chain[i + 1]}`, source: chain[i], target: chain[i + 1], animated: true });
    }
    es.push({ id: "e-campaign-output", source: "campaign", target: "output", animated: true, label: "style" });
    es.push({ id: "e-product-shot", source: "product", target: "shot", animated: true, label: "shot" });
    es.push({ id: "e-shot-output", source: "shot", target: "output", animated: true });
    // Intent row feeds the runtime rail; the rail chains left to right and
    // its persisted output loops back into the OUTPUT card for review.
    es.push({ id: "e-output-rt", source: "output", target: "rt-intake.load", animated: true, label: "compile" });
    for (let i = 0; i < RUNTIME_CHAIN.length - 1; i++) {
      es.push({
        id: `e-rt-${i}`,
        source: `rt-${RUNTIME_CHAIN[i].nodeType}`,
        target: `rt-${RUNTIME_CHAIN[i + 1].nodeType}`,
        animated: true,
      });
    }
    for (const a of loadedArtifacts) {
      es.push({
        id: `e-art-${a.id}`,
        source: `art-${a.id}`,
        target: "rt-reference.resolve",
        animated: true,
        label: "reuse",
      });
    }
    setEdges(es);
  }, [activeOps, loadedArtifacts, setEdges]);

  return (
    <div className="board-page">
      <div className="board-toolbar">
        <div>
          <label className="field-label">Product</label>
          <select className="field-select" value={productSlug} onChange={(e) => setProductSlug(e.target.value)}>
            <option value="">Select…</option>
            {products.map((p) => (
              <option key={p.slug} value={p.slug}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="field-label">Campaign</label>
          <select className="field-select" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            <option value="">Select…</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="board-toolbar__ops">
          <span className="field-label">+ operation</span>
          <div className="chip-grid">
            {operations
              .filter((o) => !activeOps.includes(o.id))
              .map((o) => (
                <button key={o.id} type="button" className="chip chip--sm" onClick={() => addOp(o.id)}>
                  + {o.label}
                </button>
              ))}
          </div>
          <div className="board-toolbar__custom-op">
            <input
              className="field-input"
              placeholder="custom operation…"
              value={customOpDraft}
              onChange={(e) => setCustomOpDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addCustomOp();
                }
              }}
            />
            <button type="button" className="btn btn--sm" disabled={!customOpDraft.trim()} onClick={addCustomOp}>
              + Add
            </button>
          </div>
        </div>
        {compileError && <div className="board-toolbar__compile-error">{compileError}</div>}
      </div>

      <div className="board-actions-row">
        <button type="button" className="btn btn--sm" onClick={() => setNewProductOpen(true)}>
          + New product from photos
        </button>
        <button type="button" className="btn btn--sm" onClick={() => setNewCampaignOpen(true)}>
          + New campaign
        </button>
        <button type="button" className="btn btn--sm" onClick={openArtifactPicker}>
          + Reuse approved output
        </button>
        <button type="button" className="btn btn--sm" onClick={() => setShowHelp((v) => !v)}>
          {showHelp ? "Hide guide" : "How this works"}
        </button>
        {wiringError && <div className="board-toolbar__compile-error">{wiringError}</div>}
      </div>

      {showHelp && (
        <div className="board-help">
          <ol>
            <li>
              <strong>Pick a product</strong> — the physical item being shot. Its node shows the source photo the
              compiler grounds every generation in.
            </li>
            <li>
              <strong>Pick a campaign</strong> — the style/scene target (who's in the shot, lighting, mood). Product
              says <em>what</em>; campaign says <em>how it should look</em>.
            </li>
            <li>
              <strong>Add operations</strong> (optional) — targeted edits like swap subject or replace logo, chained
              between product and output.
            </li>
            <li>
              <strong>Choose output types</strong> on the OUTPUT node (PDP / Lifestyle / Hero / Social), then{" "}
              <strong>Estimate</strong> for a credit cost or <strong>Generate</strong> to run.
            </li>
            <li>
              <strong>Watch the runtime rail</strong> — the six compiler steps below unlock left to right as their
              inputs are satisfied; click any step to see what it needs and produces.
            </li>
            <li>
              <strong>Review in Assets</strong> — approve, comment, or reject each render; rejections feed the next
              run. Reuse any approved image here via “+ Reuse approved output”.
            </li>
          </ol>
        </div>
      )}

      <div className="board-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background color="var(--color-border-subtle)" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {newProductOpen && (
        <div className="artifact-modal" role="dialog" aria-modal="true" aria-label="New product from photos" onClick={() => setNewProductOpen(false)}>
          <div className="artifact-modal__inner artifact-modal__inner--wide" onClick={(e) => e.stopPropagation()}>
            <strong>New product — extract CAD spec from photos</strong>
            <p className="page-subtitle" style={{ margin: "0.4rem 0 0.8rem" }}>
              Upload product photos (flatlay, packshot, screenshot). The vision extractor drafts the geometry spec; you
              confirm it before it becomes the CAD source node.
            </p>
            <label className="field-label">Product name</label>
            <input className="field-input" value={npName} onChange={(e) => setNpName(e.target.value)} placeholder="e.g. Starz Training Mat" />
            <label className="field-label" style={{ marginTop: "0.5rem" }}>Notes for the extractor (optional)</label>
            <textarea className="field-input" rows={2} value={npNotes} onChange={(e) => setNpNotes(e.target.value)} placeholder="anything the photos don't show — size, materials, branding rules…" />
            <label className="field-label" style={{ marginTop: "0.5rem" }}>Photos</label>
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={(e) => setNpFiles(Array.from(e.target.files || []))}
            />
            {npFiles.length > 0 && <p className="page-subtitle">{npFiles.length} photo{npFiles.length === 1 ? "" : "s"} selected</p>}
            {npDraft && (
              <>
                <h3 className="board-inspector__section">Drafted geometry — confirm or edit</h3>
                <textarea
                  className="field-input"
                  rows={5}
                  value={npGeometry()}
                  onChange={(e) =>
                    setNpDraft((prev) => ({ ...(prev || {}), geometry_description: e.target.value }))
                  }
                />
              </>
            )}
            {npStatus && <p className="page-subtitle" role="status">{npStatus}</p>}
            <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
              <button type="button" className="btn btn--sm" onClick={() => setNewProductOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn--sm" disabled={npBusy || !npFiles.length} onClick={extractCad}>
                {npBusy ? "Working…" : "Extract CAD spec"}
              </button>
              <button type="button" className="btn btn--sm btn--primary-solid" disabled={npBusy || !npDraft} onClick={saveNewProduct}>
                Save product
              </button>
            </div>
          </div>
        </div>
      )}

      {newCampaignOpen && (
        <div className="artifact-modal" role="dialog" aria-modal="true" aria-label="New campaign" onClick={() => setNewCampaignOpen(false)}>
          <div className="artifact-modal__inner" onClick={(e) => e.stopPropagation()}>
            <strong>New campaign — the style/scene target</strong>
            <p className="page-subtitle" style={{ margin: "0.4rem 0 0.8rem" }}>
              Describe what the final image should look like. This is the confirmation step: the generator only sees
              what you write here.
            </p>
            <label className="field-label">Label</label>
            <input className="field-input" value={ncLabel} onChange={(e) => setNcLabel(e.target.value)} placeholder="e.g. Starz Kid — turf action" />
            <label className="field-label" style={{ marginTop: "0.5rem" }}>Scene description</label>
            <textarea className="field-input" rows={4} value={ncScene} onChange={(e) => setNcScene(e.target.value)} placeholder="who's in the shot, setting, lighting, mood — e.g. young athlete wearing the product, studio lifestyle, product clearly visible" />
            <label className="field-label" style={{ marginTop: "0.5rem" }}>Mood</label>
            <input className="field-input" value={ncMood} onChange={(e) => setNcMood(e.target.value)} />
            {ncStatus && <p className="page-subtitle" role="status">{ncStatus}</p>}
            <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
              <button type="button" className="btn btn--sm" onClick={() => setNewCampaignOpen(false)}>
                Cancel
              </button>
              <button type="button" className="btn btn--sm btn--primary-solid" onClick={saveNewCampaign}>
                Save campaign
              </button>
            </div>
          </div>
        </div>
      )}

      {pickerOpen && (
        <div className="artifact-modal" role="dialog" aria-modal="true" aria-label="Reuse approved output" onClick={() => setPickerOpen(false)}>
          <div className="artifact-modal__inner artifact-modal__inner--wide" onClick={(e) => e.stopPropagation()}>
            <strong>Reuse an approved output</strong>
            <p className="page-subtitle" style={{ margin: "0.4rem 0 0.8rem" }}>
              Attaches as an artifacts.load node feeding Resolve refs — compiler-validated on both sides.
            </p>
            {pickerArtifacts.length === 0 ? (
              <p className="page-subtitle">Loading approved artifacts…</p>
            ) : (
              <div className="assets-grid board-picker-grid">
                {pickerArtifacts.map((a) => (
                  <button key={a.id} type="button" className="board-picker-card" onClick={() => attachArtifact(a.id, a.name)}>
                    <img src={a.url} alt={a.name} loading="lazy" />
                    <span className="board-picker-card__name" title={a.name}>{a.name}</span>
                    <span className="board-picker-card__slug">{a.product_slug || ""}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="asset-card__actions" style={{ marginTop: "0.75rem" }}>
              <button type="button" className="btn btn--sm" onClick={() => setPickerOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {runtimeInspect && (() => {
        const spec = specByType(runtimeInspect);
        const idx = RUNTIME_CHAIN.findIndex((r) => r.nodeType === runtimeInspect);
        const state = idx >= 0 ? unlockState[idx] : undefined;
        return (
          <div className="board-inspector">
            <button type="button" className="btn btn--sm board-inspector__close" onClick={() => setRuntimeInspect(null)}>
              ✕ close
            </button>
            <h2 className="dashboard-panel__title">{RUNTIME_CHAIN[idx]?.label || runtimeInspect}</h2>
            <p className="page-subtitle">
              {runtimeInspect} · {state?.unlocked ? "READY" : "LOCKED — inputs not yet satisfied"}
            </p>
            <h3 className="board-inspector__section">What this step does</h3>
            <p>{spec?.description || "—"}</p>
            {runtimeInspect === "prompt.build" && shotCompile?.prompt && (
              <>
                <h3 className="board-inspector__section">Compiled prompt ({selectedShot?.label})</h3>
                <p style={{ whiteSpace: "pre-wrap", fontSize: "0.8rem" }}>{shotCompile.prompt}</p>
              </>
            )}
            <h3 className="board-inspector__section">Input ports</h3>
            <ul className="explorer-list">
              {Object.entries(spec?.ports_in || {}).map(([port, p]) => (
                <li key={port}>
                  {port}: {p.type} {p.required ? "(required)" : "(optional)"}
                </li>
              ))}
              {Object.keys(spec?.ports_in || {}).length === 0 && <li>none — chain entry point</li>}
            </ul>
            <h3 className="board-inspector__section">Output ports</h3>
            <ul className="explorer-list">
              {Object.entries(spec?.ports_out || {}).map(([port, p]) => (
                <li key={port}>
                  {port}: {p.type}
                </li>
              ))}
            </ul>
          </div>
        );
      })()}

      {inspector && (
        <div className="board-inspector">
          <button type="button" className="btn btn--sm board-inspector__close" onClick={() => setInspector(null)}>
            ✕ close
          </button>
          {inspectorLoading || (inspector.kind === "campaign" && !inspector.data) ? (
            <p className="page-subtitle">Loading…</p>
          ) : inspector.kind === "product" && !inspector.data ? (
            // No knowledge-store spec for this product — the refs directory is
            // still ground truth, so show it instead of loading forever.
            <div>
              <h2 className="dashboard-panel__title">{product?.name || productSlug}</h2>
              <p className="page-subtitle">No compiled product spec yet — showing the refs directory.</p>
              {product?.geometry_description && (
                <>
                  <h3 className="board-inspector__section">Geometry</h3>
                  <p>{product.geometry_description}</p>
                </>
              )}
              <h3 className="board-inspector__section">Reference images ({productRefs.length})</h3>
              <div className="assets-grid board-picker-grid">
                {productRefs.map((r, i) => {
                  const asset = r.path || r.asset || "";
                  const name = r.name || asset.split("/").pop() || `ref-${i}`;
                  return (
                    <div key={asset || i} className="board-picker-card">
                      <img src={mediaUrl(asset)} alt={name} loading="lazy" />
                      <span className="board-picker-card__name" title={name}>{name}</span>
                      {r.role && <span className="board-picker-card__slug">{r.role}</span>}
                    </div>
                  );
                })}
                {productRefs.length === 0 && <p className="page-subtitle">none on disk</p>}
              </div>
            </div>
          ) : inspector.kind === "product" ? (
            (() => {
              const spec = inspector.data as ProductSpecV2;
              return (
                <div>
                  <h2 className="dashboard-panel__title">{spec.name}</h2>
                  <p className="page-subtitle">Source: knowledge/products/{spec.id}.json</p>
                  <h3 className="board-inspector__section">Geometry</h3>
                  <p>{spec.product?.geometry?.description}</p>
                  <p className="page-subtitle">
                    symmetry: {spec.product?.geometry?.symmetry || "—"} · envelope: {spec.product?.geometry?.envelope || "—"}
                  </p>
                  <h3 className="board-inspector__section">Materials</h3>
                  <ul className="explorer-list">
                    {(spec.product?.materials || []).map((m) => (
                      <li key={m.id}>
                        {m.id}: {m.type} · {m.color} · {m.finish}
                      </li>
                    ))}
                  </ul>
                  <h3 className="board-inspector__section">References</h3>
                  <ul className="explorer-list">
                    {(spec.references || []).map((r) => (
                      <li key={r.id}>
                        {r.id} ({r.role}) — {r.asset}
                      </li>
                    ))}
                    {(spec.references || []).length === 0 && <li>none</li>}
                  </ul>
                  <h3 className="board-inspector__section">Constraints</h3>
                  <p className="page-subtitle">
                    preserve geometry: {String(spec.constraints?.preserve_geometry)} · preserve material:{" "}
                    {String(spec.constraints?.preserve_material)}
                  </p>
                </div>
              );
            })()
          ) : (
            (() => {
              const camp = inspector.data as CampaignDetail;
              return (
                <div>
                  <h2 className="dashboard-panel__title">{camp.label}</h2>
                  <p className="page-subtitle">Source: knowledge/campaigns/{camp.id}.json</p>
                  <h3 className="board-inspector__section">Scene</h3>
                  <p>{camp.scene?.description}</p>
                  <p className="page-subtitle">
                    lighting: {camp.scene?.lighting || "—"} · camera: {camp.scene?.camera || "—"} · background:{" "}
                    {camp.scene?.background || "—"}
                  </p>
                  <h3 className="board-inspector__section">Mood</h3>
                  <p className="page-subtitle">
                    {camp.campaign?.mood || "—"} · {camp.campaign?.style || "—"}
                  </p>
                </div>
              );
            })()
          )}
        </div>
      )}
    </div>
  );
}
