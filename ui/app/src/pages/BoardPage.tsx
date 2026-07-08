import { useEffect, useState } from "react";
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

function ProductNode({ data }: NodeProps) {
  const d = data as { product?: Product; onOpen: () => void };
  const p = d.product;
  return (
    <div className="board-node board-node--product" onClick={d.onOpen}>
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">PRODUCT</div>
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
  const d = data as { label: string; onRemove: () => void };
  return (
    <div className="board-node board-node--op">
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <div className="board-node__kicker">OPERATION</div>
      <div className="board-node__title">{d.label}</div>
      <button type="button" className="board-node__remove nodrag" onClick={d.onRemove}>
        ✕ remove
      </button>
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

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

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

  const openProductInspector = async () => {
    if (!productSlug) return;
    setInspectorLoading(true);
    setInspector({ kind: "product", data: null });
    try {
      const params = withTenant(new URLSearchParams(), tenant);
      const d = await api<{ product: ProductSpecV2 }>(`/api/products/${productSlug}?${params}`);
      setInspector({ kind: "product", data: d.product });
    } finally {
      setInspectorLoading(false);
    }
  };

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
        data: { label: opLabel(opId), onRemove: () => removeOp(opId) },
      }));
      const outputX = activeOps.length ? 660 : 340;

      return [
        {
          id: "product",
          type: "productNode",
          position: posOf("product", { x: 0, y: 0 }),
          data: { product, onOpen: openProductInspector },
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
  }, [product, campaign, activeOps, customOpLabels, operations, outputs, credits, status, loading, canRun]);

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
