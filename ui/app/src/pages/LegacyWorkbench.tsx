import { useCallback, useEffect, useMemo, useState } from "react";
import type { Edge, Node } from "@xyflow/react";

import { api } from "../api";
import { useCreatorMode } from "../CreatorModeContext";
import GraphCanvas from "../GraphCanvas";
import InspectorPanel from "../InspectorPanel";
import NewShotWizard from "../NewShotWizard";
import OutputStrip from "../OutputStrip";
import ProductSidebar from "../ProductSidebar";
import RunBar from "../RunBar";
import type { CompileFile, CostEstimate, Job, Output, Product } from "../types";

/** Legacy Weave workbench — developer path only (/workbench). */
export default function LegacyWorkbench() {
  const { creatorMode } = useCreatorMode();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [compileDir, setCompileDir] = useState<string | null>(null);
  const [manifest, setManifest] = useState<Record<string, unknown>>({});
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState("");
  const [statusError, setStatusError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [graphNodes, setGraphNodes] = useState<Node[]>([]);
  const [graphEdges, setGraphEdges] = useState<Edge[]>([]);
  const [files, setFiles] = useState<CompileFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [artifactText, setArtifactText] = useState("");
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [costEstimate, setCostEstimate] = useState<CostEstimate | null>(null);

  const setStatusMsg = (msg: string, isError = false) => {
    setStatus(msg);
    setStatusError(isError);
  };

  const loadJobs = useCallback(async () => {
    const [jobsData, productsData] = await Promise.all([
      api<{ jobs: Job[] }>("/api/jobs"),
      api<{ products: Product[] }>("/api/products"),
    ]);
    setJobs(jobsData.jobs || []);
    setProducts(productsData.products || []);
    if (jobsData.jobs?.length && !selectedJob) {
      setSelectedJob(jobsData.jobs[0]);
    }
  }, [selectedJob]);

  const refreshOutputs = useCallback(async (jobDir: string) => {
    const data = await api<{ outputs: Output[] }>(
      `/api/outputs?job_dir=${encodeURIComponent(jobDir)}`
    );
    setOutputs(data.outputs || []);
  }, []);

  const loadCompileTree = useCallback(async (dir: string) => {
    const data = await api<{ files: CompileFile[]; manifest: Record<string, unknown> }>(
      `/api/compile-tree?compile_dir=${encodeURIComponent(dir)}`
    );
    setFiles(data.files || []);
    setManifest(data.manifest || {});
    if (data.files?.length) {
      const first = data.files[0].path;
      setSelectedFile(first);
      const art = await api<{ raw: string; content: unknown }>(
        `/api/artifact?path=${encodeURIComponent(first)}`
      );
      setArtifactText(
        art.content ? JSON.stringify(art.content, null, 2) : art.raw || ""
      );
    }
  }, []);

  const loadGraph = useCallback(async (dir: string) => {
    try {
      const data = await api<{ nodes: Node[]; edges: Edge[] }>(
        `/api/graph-reactflow?compile_dir=${encodeURIComponent(dir)}`
      );
      setGraphNodes(data.nodes || []);
      setGraphEdges(data.edges || []);
    } catch {
      setGraphNodes([]);
      setGraphEdges([]);
    }
  }, []);

  useEffect(() => {
    loadJobs().catch((e) => setStatusMsg(String(e), true));
  }, [loadJobs]);

  useEffect(() => {
    if (selectedJob) {
      refreshOutputs(selectedJob.dir).catch(() => setOutputs([]));
      setStatusMsg(`Selected ${creatorMode ? selectedJob.label : selectedJob.path}`);
    }
  }, [selectedJob, refreshOutputs, creatorMode]);

  const onSelectJob = (job: Job) => {
    setSelectedJob(job);
    setCompileDir(null);
    setGraphNodes([]);
    setGraphEdges([]);
    setFiles([]);
    setArtifactText("");
    setManifest({});
    setPrompt("");
    setCostEstimate(null);
  };

  const onCompile = async () => {
    if (!selectedJob) return;
    setLoading(true);
    setStatusMsg("Compiling…");
    try {
      const data = await api<{
        compile_dir: string;
        manifest: Record<string, unknown>;
        prompt?: string;
        graph?: { nodes: Node[]; edges: Edge[] };
      }>("/api/compile", {
        method: "POST",
        body: JSON.stringify({ job: selectedJob.path, dry_run: true }),
      });
      setCompileDir(data.compile_dir);
      setManifest(data.manifest || {});
      setPrompt(data.prompt || "");
      if (data.graph?.nodes) {
        setGraphNodes(data.graph.nodes);
        setGraphEdges(data.graph.edges || []);
      } else {
        await loadGraph(data.compile_dir);
      }
      await loadCompileTree(data.compile_dir);
      setStatusMsg("Ready");
      await refreshOutputs(selectedJob.dir);
    } catch (e) {
      setStatusMsg(String(e), true);
    } finally {
      setLoading(false);
    }
  };

  const onEstimate = async () => {
    if (!selectedJob || !compileDir) return;
    setLoading(true);
    try {
      const data = await api<{ result?: { cost?: unknown; credits?: number } }>("/api/execute", {
        method: "POST",
        body: JSON.stringify({
          job: selectedJob.path,
          compile_dir: compileDir,
          dry_run: true,
        }),
      });
      const result = data.result || {};
      setCostEstimate({ cost: result.cost, credits: result.credits });
      setStatusMsg("Estimate ready");
    } catch (e) {
      setStatusMsg(String(e), true);
    } finally {
      setLoading(false);
    }
  };

  const onGenerate = async () => {
    if (!selectedJob || !compileDir) return;
    if (!confirm("Generate may spend credits. Continue?")) return;
    setLoading(true);
    try {
      const data = await api<{ outputs?: Output[] }>("/api/execute", {
        method: "POST",
        body: JSON.stringify({
          job: selectedJob.path,
          compile_dir: compileDir,
          dry_run: false,
        }),
      });
      setOutputs(data.outputs || []);
      await refreshOutputs(selectedJob.dir);
      setStatusMsg("Done");
    } catch (e) {
      setStatusMsg(String(e), true);
    } finally {
      setLoading(false);
    }
  };

  const onSelectFile = async (path: string) => {
    setSelectedFile(path);
    try {
      const art = await api<{ raw: string; content: unknown }>(
        `/api/artifact?path=${encodeURIComponent(path)}`
      );
      setArtifactText(
        art.content ? JSON.stringify(art.content, null, 2) : art.raw || ""
      );
    } catch (e) {
      setArtifactText(String(e));
    }
  };

  const manifestHash = useMemo(() => {
    const m = manifest as { manifest_hash?: string; compile_hash?: string };
    return [m.manifest_hash, m.compile_hash].filter(Boolean).join(" · ");
  }, [manifest]);

  return (
    <div className="legacy-workbench">
      <div className="legacy-workbench__workspace">
        <ProductSidebar
          products={products}
          jobs={jobs}
          selectedJob={selectedJob}
          onSelectJob={onSelectJob}
          onNewShot={() => setWizardOpen(true)}
        />
        <div className="legacy-workbench__center">
          <GraphCanvas nodes={graphNodes} edges={graphEdges} />
          <OutputStrip
            outputs={outputs}
            onRefresh={selectedJob ? () => refreshOutputs(selectedJob.dir) : undefined}
          />
        </div>
        <InspectorPanel
          prompt={prompt}
          files={files}
          selectedFile={selectedFile}
          artifactText={artifactText}
          manifestHash={manifestHash}
          onSelectFile={onSelectFile}
        />
      </div>
      <RunBar
        loading={loading}
        hasJob={!!selectedJob}
        hasCompile={!!compileDir}
        status={status}
        statusError={statusError}
        costEstimate={costEstimate}
        onCompile={onCompile}
        onEstimate={onEstimate}
        onGenerate={onGenerate}
      />
      <NewShotWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={(job: Job) => {
          setJobs((prev) => [...prev, job]);
          setSelectedJob(job);
        }}
        products={products.map((p) => ({ slug: p.slug, name: p.name }))}
        devMode
      />
    </div>
  );
}
