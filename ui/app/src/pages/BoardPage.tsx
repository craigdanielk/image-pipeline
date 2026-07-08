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
import { api } from "../api";
import { useTenant, withTenant } from "../TenantContext";
import type { Campaign, Operation, Product, ProductSpecV2 } from "../types";

const OUTPUT_TYPES = [
  { id: "pdp", label: "PDP" },
  { id: "lifestyle", label: "Lifestyle" },
  { id: "hero", label: "Hero" },
  { id: "social", label: "Social" },
];

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
  const d = data as { product?: Product; productDetail?: ProductSpecV2 | null; onOpen: () => void };
  const p = d.product;
  const cadRef = cadReferenceOf(d.productDetail);
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => setImgFailed(false), [cadRef?.asset]);
  return (
    <div className="board-node board-node--product" onClick={d.onOpen}>
      <Handle type="source" position={Position.Right} />
      <div className="board-node__image">
        {cadRef && !imgFailed ? (
          <img src={mediaUrl(cadRef.asset)} alt={`${p?.name || "product"} — CAD source`} onError={() => setImgFailed(true)} />
        ) : (
          <span className="board-node__image-empty">
            {!p ? "" : cadRef ? "source file missing on disk" : "no source image on file"}
          </span>
        )}
      </div>
      <div className="board-node__kicker">PRODUCT · CAD SOURCE</div>
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
  };
  return (
    <div className="board-node board-node--op">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">OPERATION</div>
      <div className="board-node__title">{d.label}</div>
      {d.runStatus && <div className="board-node__status">{d.runStatus}</div>}
      <div className="board-node__op-actions">
        <button
          type="button"
          className="board-node__rerun nodrag"
          disabled={d.running || !d.canRerun}
          onClick={d.onRerun}
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
    <div className="board-node board-node--output nodrag">
      <Handle type="target" position={Position.Left} />
      <div className="board-node__kicker">OUTPUT</div>
      <div className="board-node__chip-row">
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
      <div className="board-node__actions">
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

const nodeTypes = {
  productNode: ProductNode,
  campaignNode: CampaignNode,
  operationNode: OperationNode,
  outputNode: OutputNode,
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

  useEffect(() => {
    Promise.all([
      api<{ products: Product[] }>("/api/products"),
      api<{ campaigns: Campaign[] }>("/api/campaigns"),
      api<{ operations: Operation[] }>("/api/operations"),
    ]).then(([p, c, o]) => {
      setProducts(p.products || []);
      setCampaigns(c.campaigns || []);
      setOperations(o.operations || []);
      if (p.products?.[0]) setProductSlug(p.products[0].slug);
      if (c.campaigns?.[0]) setCampaignId(c.campaigns[0].id);
    });
  }, []);

  useEffect(() => {
    setProductDetail(null);
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
    return () => {
      cancelled = true;
    };
  }, [productSlug, tenant]);

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
          operations: activeOps.map((id) => ({ op: id })),
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

      const opNodes: Node[] = activeOps.map((opId, i) => ({
        id: `op-${opId}`,
        type: "operationNode",
        position: posOf(`op-${opId}`, { x: 340, y: 40 + i * 110 }),
        data: {
          label: opLabel(opId),
          onRemove: () => removeOp(opId),
          onRerun: () => runNode(opId, `generate-${i}-${opId}`),
          running: !!opRunning[opId],
          runStatus: opRunStatus[opId] || "",
          canRerun: !!compileDir,
        },
      }));
      const outputX = activeOps.length ? 660 : 340;

      return [
        {
          id: "product",
          type: "productNode",
          position: posOf("product", { x: 0, y: 0 }),
          data: { product, productDetail, onOpen: openProductInspector },
        },
        {
          id: "campaign",
          type: "campaignNode",
          position: posOf("campaign", { x: 0, y: 220 }),
          data: { campaign, onOpen: openCampaignInspector },
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
      ];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    product,
    productDetail,
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
  ]);

  useEffect(() => {
    const chain = ["product", ...activeOps.map((o) => `op-${o}`), "output"];
    const es: Edge[] = [];
    for (let i = 0; i < chain.length - 1; i++) {
      es.push({ id: `e-${chain[i]}-${chain[i + 1]}`, source: chain[i], target: chain[i + 1], animated: true });
    }
    es.push({ id: "e-campaign-output", source: "campaign", target: "output", animated: true, label: "style" });
    setEdges(es);
  }, [activeOps, setEdges]);

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

      {inspector && (
        <div className="board-inspector">
          <button type="button" className="btn btn--sm board-inspector__close" onClick={() => setInspector(null)}>
            ✕ close
          </button>
          {inspectorLoading || !inspector.data ? (
            <p className="page-subtitle">Loading…</p>
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
