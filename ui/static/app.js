const jobSelect = document.getElementById("job-select");
const btnCompile = document.getElementById("btn-compile");
const btnDryRun = document.getElementById("btn-dry-run");
const btnGenerate = document.getElementById("btn-generate");
const btnRefresh = document.getElementById("btn-refresh-outputs");
const statusEl = document.getElementById("status");
const promptEl = document.getElementById("prompt");
const manifestEl = document.getElementById("manifest");
const galleryEl = document.getElementById("gallery");

let state = {
  jobs: [],
  selectedJob: null,
  compileDir: null,
  jobDir: null,
};

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    throw new Error(detail || res.statusText);
  }
  return data;
}

function renderGallery(outputs) {
  galleryEl.replaceChildren();
  if (!outputs || !outputs.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No output images yet. Run generate or refresh after a successful job.";
    galleryEl.appendChild(p);
    return;
  }
  for (const item of outputs) {
    const card = document.createElement("figure");
    card.className = "card";

    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener";

    const img = document.createElement("img");
    img.src = item.url;
    img.alt = item.name;
    img.loading = "lazy";

    const cap = document.createElement("figcaption");
    cap.textContent = item.name;

    link.appendChild(img);
    card.appendChild(link);
    card.appendChild(cap);
    galleryEl.appendChild(card);
  }
}

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs || [];
  jobSelect.innerHTML = "";
  for (const job of state.jobs) {
    const opt = document.createElement("option");
    opt.value = job.path;
    opt.textContent = job.path;
    opt.dataset.dir = job.dir;
    jobSelect.appendChild(opt);
  }
  if (state.jobs.length) {
    jobSelect.selectedIndex = 0;
    onJobSelect();
  }
}

function onJobSelect() {
  const opt = jobSelect.selectedOptions[0];
  if (!opt) return;
  state.selectedJob = opt.value;
  state.jobDir = opt.dataset.dir;
  state.compileDir = null;
  btnDryRun.disabled = true;
  btnGenerate.disabled = true;
  btnRefresh.disabled = !state.jobDir;
  promptEl.textContent = "";
  manifestEl.textContent = "";
  setStatus(`Selected ${state.selectedJob}`);
  refreshOutputs();
}

async function compileJob() {
  if (!state.selectedJob) return;
  setStatus("Compiling…");
  try {
    const data = await api("/api/compile", {
      method: "POST",
      body: JSON.stringify({ job: state.selectedJob }),
    });
    state.compileDir = data.compile_dir;
    promptEl.textContent = data.prompt || "(no prompt)";
    manifestEl.textContent = JSON.stringify(data.manifest || {}, null, 2);
    btnDryRun.disabled = false;
    btnGenerate.disabled = false;
    setStatus(`Compiled → ${state.compileDir}`);
    await refreshOutputs();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function execute(dryRun) {
  if (!state.compileDir) return;
  setStatus(dryRun ? "Dry run…" : "Generating (uses provider credits)…");
  try {
    const data = await api("/api/execute", {
      method: "POST",
      body: JSON.stringify({
        job: state.selectedJob,
        compile_dir: state.compileDir,
        dry_run: dryRun,
      }),
    });
    const result = data.result || {};
    if (result.prompt) promptEl.textContent = result.prompt;
    if (result.cost) {
      setStatus(`Done. Cost estimate: ${JSON.stringify(result.cost)}`);
    } else if (result.saved) {
      setStatus(`Saved ${result.saved.length} file(s)`);
    } else {
      setStatus("Execute finished");
    }
    renderGallery(data.outputs || []);
    if (!dryRun) await refreshOutputs();
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function refreshOutputs() {
  if (!state.jobDir) return;
  try {
    const data = await api(`/api/outputs?job_dir=${encodeURIComponent(state.jobDir)}`);
    renderGallery(data.outputs || []);
  } catch (err) {
    setStatus(err.message, true);
  }
}

jobSelect.addEventListener("change", onJobSelect);
btnCompile.addEventListener("click", compileJob);
btnDryRun.addEventListener("click", () => execute(true));
btnGenerate.addEventListener("click", () => {
  if (!confirm("Generate will call the configured engine and may spend credits. Continue?")) return;
  execute(false);
});
btnRefresh.addEventListener("click", refreshOutputs);

loadJobs().catch((err) => setStatus(err.message, true));
