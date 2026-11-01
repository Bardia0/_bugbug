const $ = (s) => document.querySelector(s);
const PID = document.querySelector("#project-root").dataset.projectId;

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

/* ════════════════════════════════════════════════════════════════════
   CRON CONFIGURATION
   ════════════════════════════════════════════════════════════════════ */

const cronModal = $("#cron-modal");
const cronForm = $("#cron-form");
const cronEnabled = $("#cron-enabled");
const cronOptions = $("#cron-options");
const cronInterval = $("#cron-interval");
const cronToolsBox = $("#cron-tools-box");
const cronStatusBadge = $("#cron-status-badge");
const cronDeleteBtn = $("#cron-delete-btn");

const PROJECT_CRON = JSON.parse(document.getElementById("cron-data").textContent || "{}");

function updateCronBadge(enabled, interval) {
  if (enabled) {
    const labels = {"6h": "Every 6h", "12h": "Every 12h", "daily": "Daily", "weekly": "Weekly"};
    cronStatusBadge.querySelector(".chip").textContent = `⏰ Cron Active (${labels[interval] || interval})`;
    cronStatusBadge.classList.remove("hidden");
    cronDeleteBtn.classList.remove("hidden");
  } else {
    cronStatusBadge.classList.add("hidden");
    cronDeleteBtn.classList.add("hidden");
  }
}

if (PROJECT_CRON) {
  cronEnabled.checked = PROJECT_CRON.enabled;
  cronInterval.value = PROJECT_CRON.interval || "daily";
  if (PROJECT_CRON.enabled) {
    cronOptions.style.opacity = "1";
    cronOptions.style.pointerEvents = "auto";
    updateCronBadge(true, PROJECT_CRON.interval);
  }
}

cronEnabled.addEventListener("change", (e) => {
  if (e.target.checked) {
    cronOptions.style.opacity = "1";
    cronOptions.style.pointerEvents = "auto";
  } else {
    cronOptions.style.opacity = "0.5";
    cronOptions.style.pointerEvents = "none";
  }
});

$("#cron-config-btn").addEventListener("click", () => {
  cronModal.classList.remove("hidden");
});

$("#cron-cancel-btn").addEventListener("click", () => {
  cronModal.classList.add("hidden");
});

cronDeleteBtn.addEventListener("click", async () => {
  if (!(await showConfirm("Are you sure you want to delete this cron schedule?\nThis will stop any active automated pipeline.", "Delete Cron Schedule"))) return;

  cronDeleteBtn.disabled = true;
  cronDeleteBtn.textContent = "Deleting...";

  try {
    const res = await fetch(`/api/projects/${PID}/cron`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete");
    
    updateCronBadge(false);
    cronEnabled.checked = false;
    cronOptions.style.opacity = "0.5";
    cronOptions.style.pointerEvents = "none";
    showToast("Cron schedule deleted successfully.");
  } catch (err) {
    showToast("Error deleting cron schedule.");
  } finally {
    cronDeleteBtn.disabled = false;
    cronDeleteBtn.textContent = "🛑 Delete Schedule";
  }
});

cronForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const saveBtn = $("#cron-save-btn");
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving...";

  const selectedTools = [...cronToolsBox.querySelectorAll('input[type="checkbox"]:checked')].map(el => el.value);
  const payload = {
    enabled: cronEnabled.checked,
    interval: cronInterval.value,
    tools: selectedTools
  };

  try {
    const res = await fetch(`/api/projects/${PID}/cron`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    
    if (!res.ok) throw new Error("Failed to save");
    
    updateCronBadge(payload.enabled, payload.interval);
    cronModal.classList.add("hidden");
    showToast("Cron configuration saved successfully.");
  } catch (err) {
    showToast("Error saving cron configuration.");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save Configuration";
  }
});

/* ════════════════════════════════════════════════════════════════════
   SUBDOMAIN DISCOVERY
   ════════════════════════════════════════════════════════════════════ */

const btn              = $("#discover-btn");
const statusEl         = $("#discovery-status");
const listEl           = $("#subdomain-list");
const emptyEl          = $("#subdomain-empty");
const filteredEmptyEl  = $("#subdomain-filtered-empty");
const countEl          = $("#sub-count");
const toolBox          = $("#tool-checkboxes");
const filterBar        = $("#filter-bar");
const filterChips      = $("#filter-chips");
const toolbar          = $("#subdomain-toolbar");
const selectAllCb      = $("#select-all-cb");
const selCountEl       = $("#sel-count");
const deleteSelBtn     = $("#delete-sel-btn");
const deleteAllBtn     = $("#delete-all-btn");

let polling = false;
let allSubs = [];
const activeSources = new Set();
const selectedIds   = new Set();

async function loadTools() {
  try {
    const res  = await fetch("/api/tools");
    const data = await res.json();
    toolBox.innerHTML = "";
    cronToolsBox.innerHTML = "";
    
    for (const t of data.tools || []) {
      const id = `tool-cb-${t.name}`;
      const label = document.createElement("label");
      label.className = "tool-cb" + (t.available ? "" : " disabled");
      label.htmlFor = id;
      label.innerHTML = `
        <input type="checkbox" id="${id}" value="${escapeHtml(t.name)}"
               ${t.available ? "checked" : "disabled"}>
        <span>${escapeHtml(t.name)}</span>
        ${t.available ? "" : '<span class="small muted">(unavailable)</span>'}
      `;
      label.querySelector("input").addEventListener("change", updateDiscoverBtn);
      toolBox.appendChild(label);
      
      if (t.available) {
        const cId = `cron-tool-${t.name}`;
        const cLabel = document.createElement("label");
        cLabel.className = "tool-cb";
        cLabel.htmlFor = cId;
        cLabel.innerHTML = `
          <input type="checkbox" id="${cId}" value="${escapeHtml(t.name)}"
                 ${PROJECT_CRON?.tools?.includes(t.name) ? "checked" : ""}>
          <span>${escapeHtml(t.name)}</span>
        `;
        cronToolsBox.appendChild(cLabel);
      }
    }
    updateDiscoverBtn();
  } catch {
    toolBox.innerHTML = '<span class="small" style="color:var(--danger)">⚠ Could not load tools.</span>';
    btn.disabled = true;
  }
}

function getSelectedTools() {
  return [...toolBox.querySelectorAll('input[type="checkbox"]:checked')]
    .map((el) => el.value);
}

function updateDiscoverBtn() {
  btn.disabled = polling || getSelectedTools().length === 0;
}

$("#tools-all-btn").addEventListener("click", () => {
  toolBox.querySelectorAll('input[type="checkbox"]:not(:disabled)')
    .forEach((el) => { el.checked = true; });
  updateDiscoverBtn();
});
$("#tools-none-btn").addEventListener("click", () => {
  toolBox.querySelectorAll('input[type="checkbox"]')
    .forEach((el) => { el.checked = false; });
  updateDiscoverBtn();
});

function getVisibleSubs() {
  if (activeSources.size === 0) return allSubs;
  return allSubs.filter((s) =>
    (s.sources || []).some((src) => activeSources.has(src))
  );
}

function renderFilterChips() {
  const counts = {};
  for (const s of allSubs) {
    for (const src of s.sources || []) counts[src] = (counts[src] || 0) + 1;
  }
  const sources = Object.keys(counts).sort();
  for (const src of [...activeSources]) if (!(src in counts)) activeSources.delete(src);

  filterChips.innerHTML = "";
  if (!sources.length) { filterBar.classList.add("hidden"); return; }
  filterBar.classList.remove("hidden");

  if (activeSources.size > 0) {
    const allChip = document.createElement("button");
    allChip.type = "button"; allChip.className = "chip"; allChip.textContent = "All";
    allChip.addEventListener("click", () => { activeSources.clear(); renderSubdomains(); });
    filterChips.appendChild(allChip);
  }
  for (const src of sources) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (activeSources.has(src) ? " active" : "");
    chip.innerHTML = `${escapeHtml(src)} <span class="muted">(${counts[src]})</span>`;
    chip.addEventListener("click", () => {
      if (activeSources.has(src)) activeSources.delete(src);
      else activeSources.add(src);
      renderSubdomains();
    });
    filterChips.appendChild(chip);
  }
}

function updateSelectionUI() {
  const visible = getVisibleSubs();
  const visibleSel = visible.filter((s) => selectedIds.has(s.id)).length;

  if (!visible.length || visibleSel === 0) {
    selectAllCb.checked = false; selectAllCb.indeterminate = false;
  } else if (visibleSel === visible.length) {
    selectAllCb.checked = true; selectAllCb.indeterminate = false;
  } else {
    selectAllCb.checked = false; selectAllCb.indeterminate = true;
  }
  const n = selectedIds.size;
  selCountEl.textContent = n ? `${n} selected` : "";
  deleteSelBtn.classList.toggle("hidden", n === 0);
  selectAllCb.disabled = visible.length === 0;
}

selectAllCb.addEventListener("change", () => {
  const visible = getVisibleSubs();
  if (selectAllCb.checked) for (const s of visible) selectedIds.add(s.id);
  else for (const s of visible) selectedIds.delete(s.id);
  renderSubdomains();
});

listEl.addEventListener("change", (e) => {
  const cb = e.target;
  if (!cb.classList.contains("row-cb")) return;
  const li = cb.closest("li[data-id]");
  if (!li) return;
  if (cb.checked) selectedIds.add(li.dataset.id);
  else selectedIds.delete(li.dataset.id);
  updateSelectionUI();
});

function renderSubdomains() {
  renderFilterChips();
  const visible = getVisibleSubs();

  if (!allSubs.length) countEl.textContent = "";
  else if (activeSources.size > 0) countEl.textContent = `(${visible.length}/${allSubs.length})`;
  else countEl.textContent = `(${allSubs.length})`;

  listEl.innerHTML = "";
  toolbar.classList.toggle("hidden", allSubs.length === 0);

  if (!allSubs.length) {
    emptyEl.classList.remove("hidden"); filteredEmptyEl.classList.add("hidden");
    updateSelectionUI(); return;
  }
  emptyEl.classList.add("hidden");

  if (!visible.length) {
    filteredEmptyEl.classList.remove("hidden");
    updateSelectionUI(); return;
  }
  filteredEmptyEl.classList.add("hidden");

  for (const s of visible) {
    const li = document.createElement("li");
    li.dataset.id = s.id;
    const checked = selectedIds.has(s.id) ? "checked" : "";
    const chips = (s.sources || [])
      .map((x) => `<span class="chip chip-sm">${escapeHtml(x)}</span>`).join("");
    li.innerHTML = `
      <input type="checkbox" class="row-cb" ${checked} aria-label="Select ${escapeHtml(s.hostname)}">
      <code>${escapeHtml(s.hostname)}</code>
      ${chips}
    `;
    listEl.appendChild(li);
  }
  updateSelectionUI();
}

async function loadSubdomains() {
  const res = await fetch(`/api/projects/${PID}/subdomains`);
  if (!res.ok) return;
  const data = await res.json();
  allSubs = data.subdomains || [];

  const liveIds = new Set(allSubs.map((s) => s.id));
  for (const id of [...selectedIds]) if (!liveIds.has(id)) selectedIds.delete(id);

  renderSubdomains();
  if (data.running_job_id) pollJob(data.running_job_id);
}

async function deleteSelected() {
  const ids = [...selectedIds];
  if (!ids.length) return;
  if (!(await showConfirm(`Delete ${ids.length} subdomain${ids.length === 1 ? "" : "s"}?\nTheir DNS and HTTP records will also be removed.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/subdomains`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || "Failed to delete."); return; }
    const del = new Set(ids);
    allSubs = allSubs.filter((s) => !del.has(s.id));
    selectedIds.clear();
    renderSubdomains();
    loadResolved();
    loadHttpxResults();
    loadHttpxChanges();
  } catch { showToast("Network error while deleting."); }
}

async function deleteAll() {
  if (!allSubs.length) return;
  if (!(await showConfirm(`Delete ALL ${allSubs.length} subdomains?\nTheir DNS and HTTP records will also be removed. This cannot be undone.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/subdomains`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || "Failed to delete."); return; }
    allSubs = []; selectedIds.clear();
    renderSubdomains();
    loadResolved();
    loadHttpxResults();
    loadHttpxChanges();
  } catch { showToast("Network error while deleting."); }
}

deleteSelBtn.addEventListener("click", deleteSelected);
deleteAllBtn.addEventListener("click", deleteAll);

async function startDiscovery() {
  const tools = getSelectedTools();
  if (!tools.length) { statusEl.textContent = "❌ Select at least one module."; return; }
  polling = true; updateDiscoverBtn();
  statusEl.textContent = `⏳ Starting with: ${tools.join(", ")}…`;

  try {
    const res = await fetch(`/api/projects/${PID}/discovery`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tools }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.job_id) { pollJob(data.job_id); return; }
    if (!res.ok) {
      polling = false; updateDiscoverBtn();
      statusEl.textContent = `❌ ${data.error || "Failed to start."}`;
      return;
    }
    pollJob(data.job_id);
  } catch {
    polling = false; updateDiscoverBtn();
    statusEl.textContent = "❌ Network error.";
  }
}

function updateStatusUI(job) {
  if (job.status === "running") {
    const cur = job.current_domain ? ` — scanning ${job.current_domain}` : "";
    statusEl.textContent =
      `⏳ ${job.processed_domains}/${job.total_domains} domains${cur} · ` +
      `${job.found_count} hosts so far · modules: ${(job.tools || []).join(", ")}`;
  } else if (job.status === "done") {
    let msg = `✅ Done — ${job.found_count} hosts total, ${job.new_count} new.`;
    if (job.errors?.length) msg += ` (${job.errors.length} tool error${job.errors.length > 1 ? "s" : ""})`;
    statusEl.textContent = msg;
  } else if (job.status === "failed") {
    statusEl.textContent = `❌ Failed: ${job.error || "unknown error"}`;
  }
}

async function pollJob(jobId) {
  if (polling && jobId === pollJob._currentId) return;
  pollJob._currentId = jobId;
  polling = true; updateDiscoverBtn();

  try {
    while (true) {
      const res = await fetch(`/api/projects/${PID}/discovery/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      updateStatusUI(job);
      if (job.status === "done" || job.status === "failed") {
        if (job.status === "done") await loadSubdomains();
        break;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  } finally {
    polling = false; pollJob._currentId = null; updateDiscoverBtn();
  }
}

/* ════════════════════════════════════════════════════════════════════
   DNS RESOLUTION
   ════════════════════════════════════════════════════════════════════ */

const dnsBtn          = $("#dns-resolve-btn");
const dnsStatus       = $("#dns-status");
const resolverInfo    = $("#resolver-info");
const resolvedCount   = $("#resolved-count");
const resolvedList    = $("#resolved-list");
const resolvedEmpty   = $("#resolved-empty");
const resolvedToolbar = $("#resolved-toolbar");
const dnsSelectAllCb  = $("#dns-select-all-cb");
const dnsSelCount     = $("#dns-sel-count");
const dnsDeleteSel    = $("#dns-delete-sel-btn");
const dnsDeleteAll    = $("#dns-delete-all-btn");

let dnsPolling = false;
let allResolved = [];
const dnsSelectedIds = new Set();

async function loadResolverInfo() {
  try {
    const res = await fetch("/api/resolvers");
    const data = await res.json();
    const dnsx = (data.resolvers || []).find((r) => r.name === "dnsx");
    if (!dnsx || !dnsx.available) {
      dnsBtn.disabled = true;
      resolverInfo.textContent = "⚠ dnsx not found — install: go install github.com/projectdiscovery/dnsx/cmd/dnsx@latest";
    } else {
      resolverInfo.textContent = `Resolver: ${dnsx.name}`;
    }
  } catch {
    resolverInfo.textContent = "⚠ Could not load resolver status.";
  }
}

function updateDnsSelectionUI() {
  const visible = allResolved;
  const visibleSel = visible.filter((r) => dnsSelectedIds.has(r.id)).length;

  if (!visible.length || visibleSel === 0) {
    dnsSelectAllCb.checked = false; dnsSelectAllCb.indeterminate = false;
  } else if (visibleSel === visible.length) {
    dnsSelectAllCb.checked = true; dnsSelectAllCb.indeterminate = false;
  } else {
    dnsSelectAllCb.checked = false; dnsSelectAllCb.indeterminate = true;
  }
  const n = dnsSelectedIds.size;
  dnsSelCount.textContent = n ? `${n} selected` : "";
  dnsDeleteSel.classList.toggle("hidden", n === 0);
  dnsSelectAllCb.disabled = visible.length === 0;
}

dnsSelectAllCb.addEventListener("change", () => {
  if (dnsSelectAllCb.checked) for (const r of allResolved) dnsSelectedIds.add(r.id);
  else for (const r of allResolved) dnsSelectedIds.delete(r.id);
  renderResolved();
});

resolvedList.addEventListener("change", (e) => {
  const cb = e.target;
  if (!cb.classList.contains("row-cb")) return;
  const li = cb.closest("li[data-id]");
  if (!li) return;
  if (cb.checked) dnsSelectedIds.add(li.dataset.id);
  else dnsSelectedIds.delete(li.dataset.id);
  updateDnsSelectionUI();
});

function renderResolved() {
  resolvedCount.textContent = allResolved.length ? `(${allResolved.length})` : "";

  resolvedList.innerHTML = "";
  resolvedToolbar.classList.toggle("hidden", allResolved.length === 0);

  if (!allResolved.length) {
    resolvedEmpty.classList.remove("hidden");
    updateDnsSelectionUI(); return;
  }
  resolvedEmpty.classList.add("hidden");

  for (const r of allResolved) {
    const li = document.createElement("li");
    li.dataset.id = r.id;
    const checked = dnsSelectedIds.has(r.id) ? "checked" : "";
    li.innerHTML = `
      <input type="checkbox" class="row-cb" ${checked} aria-label="Select ${escapeHtml(r.hostname)}">
      <code>${escapeHtml(r.hostname)}</code>
    `;
    resolvedList.appendChild(li);
  }
  updateDnsSelectionUI();
}

async function loadResolved() {
  const res = await fetch(`/api/projects/${PID}/resolved`);
  if (!res.ok) return;
  const data = await res.json();
  allResolved = data.resolved || [];

  const liveIds = new Set(allResolved.map((r) => r.id));
  for (const id of [...dnsSelectedIds]) if (!liveIds.has(id)) dnsSelectedIds.delete(id);

  renderResolved();
  if (data.running_job_id) pollDnsJob(data.running_job_id);
}

async function startDnsResolve() {
  dnsPolling = true; dnsBtn.disabled = true;
  dnsStatus.textContent = "⏳ Starting DNS resolution…";
  try {
    const res = await fetch(`/api/projects/${PID}/dns-resolve`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.job_id) { pollDnsJob(data.job_id); return; }
    if (!res.ok) {
      dnsPolling = false; dnsBtn.disabled = false;
      dnsStatus.textContent = `❌ ${data.error || "Failed to start."}`;
      return;
    }
    pollDnsJob(data.job_id);
  } catch {
    dnsPolling = false; dnsBtn.disabled = false;
    dnsStatus.textContent = "❌ Network error.";
  }
}

function updateDnsStatusUI(job) {
  if (job.status === "running") {
    dnsStatus.textContent = `⏳ Resolving ${job.total_hosts} hostnames…`;
  } else if (job.status === "done") {
    dnsStatus.textContent =
      `✅ Done — ${job.resolved}/${job.total_hosts} resolved ` +
      `(${job.new_count} new, ${job.updated_count} updated, ${job.failed} failed).`;
  } else if (job.status === "failed") {
    dnsStatus.textContent = `❌ Failed: ${job.error || "unknown error"}`;
  }
}

async function pollDnsJob(jobId) {
  if (dnsPolling && jobId === pollDnsJob._currentId) return;
  pollDnsJob._currentId = jobId;
  dnsPolling = true; dnsBtn.disabled = true;

  try {
    while (true) {
      const res = await fetch(`/api/projects/${PID}/dns-resolve/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      updateDnsStatusUI(job);
      if (job.status === "done" || job.status === "failed") {
        if (job.status === "done") await loadResolved();
        break;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  } finally {
    dnsPolling = false; pollDnsJob._currentId = null; dnsBtn.disabled = false;
    loadResolverInfo();
  }
}

async function deleteResolvedSelected() {
  const ids = [...dnsSelectedIds];
  if (!ids.length) return;
  if (!(await showConfirm(`Delete ${ids.length} DNS record${ids.length === 1 ? "" : "s"}?\nThe underlying subdomains stay in the list.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/resolved`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || "Failed to delete."); return; }
    const del = new Set(ids);
    allResolved = allResolved.filter((r) => !del.has(r.id));
    dnsSelectedIds.clear();
    renderResolved();
  } catch { showToast("Network error while deleting."); }
}

async function deleteResolvedAll() {
  if (!allResolved.length) return;
  if (!(await showConfirm(`Delete ALL ${allResolved.length} DNS records?\nThe underlying subdomains stay in the list. This cannot be undone.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/resolved`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || "Failed to delete."); return; }
    allResolved = []; dnsSelectedIds.clear();
    renderResolved();
  } catch { showToast("Network error while deleting."); }
}

dnsDeleteSel.addEventListener("click", deleteResolvedSelected);
dnsDeleteAll.addEventListener("click", deleteResolvedAll);
dnsBtn.addEventListener("click", startDnsResolve);

/* ════════════════════════════════════════════════════════════════════
   HTTP DISCOVERY
   ════════════════════════════════════════════════════════════════════ */

const httpxBtn         = $("#httpx-btn");
const httpxStatus      = $("#httpx-status");
const proberInfo       = $("#prober-info");
const httpxCount       = $("#httpx-count");
const httpxEmpty       = $("#httpx-empty");
const httpxTable       = $("#httpx-table");
const httpxTbody       = $("#httpx-tbody");
const httpxDeleteAllBtn= $("#httpx-delete-all-btn");
const httpxSelectAllCb = $("#httpx-select-all-cb");

const httpxDateSelect  = $("#httpx-date-select");
const httpxDomainSelect= $("#httpx-domain-select");
const httpxStatusSelect= $("#httpx-status-select");
const httpxTitleFilter = $("#httpx-title-filter");
const httpxTechFilter  = $("#httpx-tech-filter");
const httpxSearch      = $("#httpx-search");
const httpxTitleList   = $("#httpx-title-list");
const httpxTechList    = $("#httpx-tech-list");
const httpxClearFiltersBtn = $("#httpx-clear-filters-btn");
const httpxCrawlBtn    = $("#httpx-crawl-btn");

const httpxChangesList = $("#httpx-changes-list");
const httpxChangesEmpty = $("#httpx-changes-empty");
const httpxChangesCount = $("#httpx-changes-count");
const httpxChangesClearBtn = $("#httpx-changes-clear-btn");

let httpxPolling = false;
let allHttpxResults = [];
const httpxSelectedIds = new Set();
let currentDateFilter = "";

async function loadProberInfo() {
  try {
    const res = await fetch("/api/probers");
    const data = await res.json();
    const hx = (data.probers || []).find((p) => p.name === "httpx");
    if (!hx || !hx.available) {
      httpxBtn.disabled = true;
      proberInfo.innerHTML = "⚠ <b>Wrong httpx binary detected!</b> You are running the Python library, not the ProjectDiscovery Go binary.<br>Run: <code>pip uninstall httpx</code> and ensure <code>~/go/bin</code> is in your PATH.";
    } else {
      proberInfo.textContent = `Prober: ${hx.name}`;
    }
  } catch {
    proberInfo.textContent = "⚠ Could not load prober status.";
  }
}

httpxDateSelect.addEventListener("change", (e) => {
  currentDateFilter = e.target.value;
  renderHttpxTable();
});

[httpxDomainSelect, httpxStatusSelect, httpxTitleFilter, httpxTechFilter, httpxSearch].forEach(el => {
  el.addEventListener("input", renderHttpxTable);
  el.addEventListener("change", renderHttpxTable);
});

httpxClearFiltersBtn.addEventListener("click", () => {
  httpxDateSelect.value = "";
  currentDateFilter = "";
  httpxDomainSelect.value = "";
  httpxStatusSelect.value = "";
  httpxTitleFilter.value = "";
  httpxTechFilter.value = "";
  httpxSearch.value = "";
  renderHttpxTable();
});

function populateDatalists() {
  const titles = new Set();
  const techs = new Set();

  for (const r of allHttpxResults) {
    if (r.title) titles.add(r.title);
    for (const t of (r.tech || [])) techs.add(t);
  }

  httpxTitleList.innerHTML = "";
  for (const t of [...titles].sort()) {
    const opt = document.createElement("option");
    opt.value = t;
    httpxTitleList.appendChild(opt);
  }

  httpxTechList.innerHTML = "";
  for (const t of [...techs].sort()) {
    const opt = document.createElement("option");
    opt.value = t;
    httpxTechList.appendChild(opt);
  }
}

function getFilteredHttpx() {
  let filtered = allHttpxResults;
  
  if (currentDateFilter) {
    filtered = filtered.filter(r => r.scan_date === currentDateFilter);
  }

  const domainVal = httpxDomainSelect.value;
  if (domainVal) {
    filtered = filtered.filter(r => r.hostname === domainVal || r.hostname.endsWith("." + domainVal));
  }
  
  const statusVal = httpxStatusSelect.value;
  if (statusVal) {
    const prefix = statusVal.charAt(0);
    filtered = filtered.filter(r => String(r.status_code).startsWith(prefix));
  }
  
  const titleQuery = httpxTitleFilter.value.toLowerCase().trim();
  if (titleQuery) {
    filtered = filtered.filter(r => (r.title || "").toLowerCase().includes(titleQuery));
  }
  
  const techQuery = httpxTechFilter.value.toLowerCase().trim();
  if (techQuery) {
    filtered = filtered.filter(r => (r.tech || []).some(t => t.toLowerCase().includes(techQuery)));
  }
  
  const searchQuery = httpxSearch.value.toLowerCase().trim();
  if (searchQuery) {
    filtered = filtered.filter(r => {
      const text = [
        r.hostname, r.url, r.status_code, r.title, r.server, r.ip, r.cname,
        ...(r.tech || [])
      ].join(" ").toLowerCase();
      return text.includes(searchQuery);
    });
  }
  
  return filtered;
}

function updateHttpxSelectionUI() {
  const visible = getFilteredHttpx();
  const visibleSel = visible.filter((r) => httpxSelectedIds.has(r.id)).length;

  if (!visible.length || visibleSel === 0) {
    httpxSelectAllCb.checked = false; httpxSelectAllCb.indeterminate = false;
  } else if (visibleSel === visible.length) {
    httpxSelectAllCb.checked = true; httpxSelectAllCb.indeterminate = false;
  } else {
    httpxSelectAllCb.checked = false; httpxSelectAllCb.indeterminate = true;
  }
  
  httpxSelectAllCb.disabled = visible.length === 0;
}

httpxSelectAllCb.addEventListener("change", () => {
  const visible = getFilteredHttpx();
  if (httpxSelectAllCb.checked) for (const r of visible) httpxSelectedIds.add(r.id);
  else for (const r of visible) httpxSelectedIds.delete(r.id);
  renderHttpxTable();
});

httpxTbody.addEventListener("change", (e) => {
  const cb = e.target;
  if (!cb.classList.contains("row-cb")) return;
  const tr = cb.closest("tr[data-id]");
  if (!tr) return;
  if (cb.checked) httpxSelectedIds.add(tr.dataset.id);
  else httpxSelectedIds.delete(tr.dataset.id);
  updateHttpxSelectionUI();
});

function renderHttpxTable() {
  httpxTbody.innerHTML = "";
  
  const visible = getFilteredHttpx();
  
  const isFiltered = currentDateFilter || httpxDomainSelect.value || httpxStatusSelect.value || httpxTitleFilter.value.trim() || httpxTechFilter.value.trim() || httpxSearch.value.trim();
  if (!allHttpxResults.length) {
    httpxCount.textContent = "";
  } else if (isFiltered) {
    httpxCount.textContent = `(${visible.length}/${allHttpxResults.length})`;
  } else {
    httpxCount.textContent = `(${allHttpxResults.length})`;
  }
  
  if (!visible.length) {
    httpxEmpty.classList.remove("hidden");
    httpxTable.classList.add("hidden");
  } else {
    httpxEmpty.classList.add("hidden");
    httpxTable.classList.remove("hidden");
  }
  
  for (const r of visible) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    
    const checked = httpxSelectedIds.has(r.id) ? "checked" : "";
    
    let statusClass = "status-2xx";
    if (r.status_code >= 300 && r.status_code < 400) statusClass = "status-3xx";
    else if (r.status_code >= 400 && r.status_code < 500) statusClass = "status-4xx";
    else if (r.status_code >= 500) statusClass = "status-5xx";
    
    const techChips = (r.tech || []).map(t => `<span class="chip chip-sm">${escapeHtml(t)}</span>`).join(" ");
    
    const hostLink = r.url ? `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.hostname)}</a>` : `<code>${escapeHtml(r.hostname)}</code>`;
    
    tr.innerHTML = `
      <td><input type="checkbox" class="row-cb" ${checked} aria-label="Select ${escapeHtml(r.hostname)}"></td>
      <td>${hostLink}</td>
      <td><span class="status-badge ${statusClass}">${r.status_code}</span></td>
      <td class="title-cell" title="${escapeHtml(r.title || "")}">${escapeHtml(r.title || "")}</td>
      <td>${techChips}</td>
      <td>${escapeHtml(r.server || "")}</td>
      <td class="mono">${escapeHtml(r.ip || "")}</td>
      <td class="mono">${escapeHtml(r.cname || "")}</td>
    `;
    httpxTbody.appendChild(tr);
  }
  
  updateHttpxSelectionUI();
}

async function loadHttpxResults() {
  const res = await fetch(`/api/projects/${PID}/httpx`);
  if (!res.ok) return;
  const data = await res.json();
  allHttpxResults = data.results || [];
  
  const dates = data.scan_dates || [];
  const currentVal = httpxDateSelect.value;
  httpxDateSelect.innerHTML = '<option value="">All dates</option>';
  for (const d of dates) {
    const opt = document.createElement("option");
    opt.value = d;
    opt.textContent = d;
    httpxDateSelect.appendChild(opt);
  }
  if (dates.includes(currentVal)) {
    httpxDateSelect.value = currentVal;
    currentDateFilter = currentVal;
  } else {
    httpxDateSelect.value = "";
    currentDateFilter = "";
  }

  const liveIds = new Set(allHttpxResults.map((r) => r.id));
  for (const id of [...httpxSelectedIds]) if (!liveIds.has(id)) httpxSelectedIds.delete(id);

  populateDatalists();
  renderHttpxTable();
  if (data.running_job_id) pollHttpxJob(data.running_job_id);
}

async function loadHttpxChanges() {
  const res = await fetch(`/api/projects/${PID}/httpx/changes`);
  if (!res.ok) return;
  const data = await res.json();
  const changes = data.changes || [];
  
  httpxChangesCount.textContent = changes.length ? `(${changes.length})` : "";
  httpxChangesList.innerHTML = "";
  
  if (!changes.length) {
    httpxChangesEmpty.classList.remove("hidden");
    return;
  }
  httpxChangesEmpty.classList.add("hidden");
  
  for (const c of changes) {
    const li = document.createElement("li");
    let text = "";
    if (c.field === "status") {
      text = `status changed from <b>\`${escapeHtml(c.old_value)}\`</b> to <b>\`${escapeHtml(c.new_value)}\`</b>`;
    } else if (c.field === "title") {
      text = `title changed from <b>\`${escapeHtml(c.old_value)}\`</b> to <b>\`${escapeHtml(c.new_value)}\`</b>`;
    } else if (c.field === "tech") {
      text = `tech changed from <b>\`${escapeHtml(c.old_value)}\`</b> to <b>\`${escapeHtml(c.new_value)}\`</b>`;
    }
    
    const timeStr = c.changed_at ? c.changed_at.replace("T", " ").slice(0, 19) : "";
    
    li.innerHTML = `
      <div style="display:flex; flex-direction:column; width:100%; gap:4px;">
        <span class="small muted">${timeStr}</span>
        <span>
          <a href="https://${escapeHtml(c.hostname)}" target="_blank" rel="noopener"><code>${escapeHtml(c.hostname)}</code></a> 
          ${text}
        </span>
      </div>
    `;
    httpxChangesList.appendChild(li);
  }
}

httpxChangesClearBtn.addEventListener("click", async () => {
  if (!(await showConfirm("Clear all change logs?"))) return;
  try {
    await fetch(`/api/projects/${PID}/httpx/changes`, { method: "DELETE" });
    loadHttpxChanges();
  } catch { showToast("Network error."); }
});

async function startHttpx() {
  httpxPolling = true; httpxBtn.disabled = true;
  httpxStatus.textContent = "⏳ Starting HTTP probing…";
  try {
    const res = await fetch(`/api/projects/${PID}/httpx`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.job_id) { pollHttpxJob(data.job_id); return; }
    if (!res.ok) {
      httpxPolling = false; httpxBtn.disabled = false;
      httpxStatus.textContent = `❌ ${data.error || "Failed to start."}`;
      return;
    }
    pollHttpxJob(data.job_id);
  } catch {
    httpxPolling = false; httpxBtn.disabled = false;
    httpxStatus.textContent = "❌ Network error.";
  }
}

function updateHttpxStatusUI(job) {
  if (job.status === "running") {
    httpxStatus.textContent = `⏳ Probing ${job.total_hosts} hostnames…`;
  } else if (job.status === "done") {
    httpxStatus.textContent =
      `✅ Done — ${job.probed}/${job.total_hosts} probed ` +
      `(${job.new_count} new, ${job.updated_count} updated, ${job.failed} failed).`;
  } else if (job.status === "failed") {
    httpxStatus.textContent = `❌ Failed: ${job.error || "unknown error"}`;
  }
}

async function pollHttpxJob(jobId) {
  if (httpxPolling && jobId === pollHttpxJob._currentId) return;
  pollHttpxJob._currentId = jobId;
  httpxPolling = true; httpxBtn.disabled = true;

  try {
    while (true) {
      const res = await fetch(`/api/projects/${PID}/httpx/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      updateHttpxStatusUI(job);
      if (job.status === "done" || job.status === "failed") {
        if (job.status === "done") {
          await loadHttpxResults();
          await loadHttpxChanges();
        }
        break;
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  } finally {
    httpxPolling = false; pollHttpxJob._currentId = null; httpxBtn.disabled = false;
    loadProberInfo();
  }
}

// ── Crawl Modal Logic ──
const crawlModal = $("#crawl-modal");
const crawlForm = $("#crawl-form");
const crawlDepth = $("#crawl-depth");
const crawlConcurrency = $("#crawl-concurrency");
const crawlRatelimit = $("#crawl-ratelimit");
const crawlHeaders = $("#crawl-headers");

let pendingCrawlUrls = [];

httpxCrawlBtn.addEventListener("click", () => {
  const selected = allHttpxResults.filter(r => httpxSelectedIds.has(r.id));
  if (!selected.length) {
    showToast("Select at least one URL to crawl.");
    return;
  }

  pendingCrawlUrls = selected.map(r => r.url).filter(Boolean);
  if (!pendingCrawlUrls.length) {
    showToast("Selected items have no valid URLs.");
    return;
  }

  crawlModal.classList.remove("hidden");
});

$("#crawl-cancel-btn").addEventListener("click", () => {
  crawlModal.classList.add("hidden");
});

crawlForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const startBtn = $("#crawl-start-btn");
  startBtn.disabled = true;
  startBtn.textContent = "Starting...";

  const rawHeaders = crawlHeaders.value.split("\n");
  const customHeaders = rawHeaders.map(h => h.trim()).filter(h => h.length > 0);

  const payload = {
    urls: pendingCrawlUrls,
    katana_opts: {
      d: parseInt(crawlDepth.value) || 3,
      c: parseInt(crawlConcurrency.value) || 5,
      rl: parseInt(crawlRatelimit.value) || 30
    },
    custom_headers: customHeaders
  };

  try {
    const res = await fetch(`/api/projects/${PID}/crawl`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    
    if (res.status === 409 && data.job_id) {
      pollCrawlJob(data.job_id);
      crawlModal.classList.add("hidden");
      return;
    }
    if (!res.ok) throw new Error(data.error || "Failed to start crawl");
    
    showToast(`Crawling ${pendingCrawlUrls.length} URLs started.`);
    httpxSelectedIds.clear();
    renderHttpxTable();
    crawlModal.classList.add("hidden");
    pollCrawlJob(data.job_id);
  } catch (err) {
    showToast(err.message);
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = "Start Crawl";
  }
});

async function deleteHttpxAll() {
  if (!allHttpxResults.length) return;
  if (!(await showConfirm(`Delete ALL ${allHttpxResults.length} HTTP results?\nThis cannot be undone.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/httpx`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || "Failed to delete."); return; }
    allHttpxResults = []; httpxSelectedIds.clear();
    loadHttpxResults();
  } catch { showToast("Network error while deleting."); }
}

httpxDeleteAllBtn.addEventListener("click", deleteHttpxAll);
httpxBtn.addEventListener("click", startHttpx);

/* ════════════════════════════════════════════════════════════════════
   CRAWL RESULTS
   ════════════════════════════════════════════════════════════════════ */

const crawlCount       = $("#crawl-count");
const crawlStatus      = $("#crawl-status");
const crawlEmpty       = $("#crawl-empty");
const crawlTable       = $("#crawl-table");
const crawlTbody       = $("#crawl-tbody");
const crawlSelectAllCb = $("#crawl-select-all-cb");
const crawlSelectAllCbTbody = $("#crawl-select-all-cb-tbody");
const crawlToolbar     = $("#crawl-toolbar");
const crawlSelCount    = $("#crawl-sel-count");
const crawlDeleteSel   = $("#crawl-delete-sel-btn");
const crawlDeleteAll   = $("#crawl-delete-all-btn");
const crawlDomainSelect= $("#crawl-domain-select");
const crawlExtSelect   = $("#crawl-ext-select");
const crawlSearch      = $("#crawl-search");
const crawlClearFilters= $("#crawl-clear-filters-btn");
const crawlSendParamsBtn = $("#crawl-send-params-btn");

let crawlPolling = false;
let allCrawlResults = [];
const crawlSelectedIds = new Set();

function setCrawlState(isRunning, msg = "") {
  if (isRunning) {
    httpxCrawlBtn.disabled = true;
    httpxCrawlBtn.textContent = "⏳ Crawling...";
    crawlStatus.style.color = "var(--accent)";
  } else {
    httpxCrawlBtn.disabled = false;
    httpxCrawlBtn.textContent = "✈️ Send to Crawl";
  }
  if (msg) {
    crawlStatus.textContent = msg;
    if (msg.startsWith("✅")) crawlStatus.style.color = "var(--success, #10b981)";
    else if (msg.startsWith("❌")) crawlStatus.style.color = "var(--danger)";
  }
}

crawlDomainSelect.addEventListener("change", renderCrawlTable);
crawlExtSelect.addEventListener("change", renderCrawlTable);
crawlSearch.addEventListener("input", renderCrawlTable);
crawlClearFilters.addEventListener("click", () => {
  crawlDomainSelect.value = "";
  crawlExtSelect.value = "";
  crawlSearch.value = "";
  renderCrawlTable();
});

function populateCrawlExtensions() {
  const exts = new Set();
  for (const r of allCrawlResults) {
    if (r.extension) exts.add(r.extension);
  }
  crawlExtSelect.innerHTML = '<option value="">All extensions</option>';
  for (const e of [...exts].sort()) {
    const opt = document.createElement("option");
    opt.value = e;
    opt.textContent = e;
    crawlExtSelect.appendChild(opt);
  }
}

function getFilteredCrawl() {
  return allCrawlResults.filter(r => {
    if (crawlDomainSelect.value && r.hostname !== crawlDomainSelect.value) return false;
    
    if (crawlExtSelect.value && r.extension !== crawlExtSelect.value) return false;
    
    if (crawlSearch.value.trim()) {
      const q = crawlSearch.value.toLowerCase().trim();
      if (!r.url.toLowerCase().includes(q)) return false;
    }
    
    return true;
  });
}

function updateCrawlSelectionUI() {
  const visible = getFilteredCrawl();
  const visibleSel = visible.filter(r => crawlSelectedIds.has(r.id)).length;

  if (!visible.length || visibleSel === 0) {
    crawlSelectAllCb.checked = false; crawlSelectAllCb.indeterminate = false;
    crawlSelectAllCbTbody.checked = false; crawlSelectAllCbTbody.indeterminate = false;
  } else if (visibleSel === visible.length) {
    crawlSelectAllCb.checked = true; crawlSelectAllCb.indeterminate = false;
    crawlSelectAllCbTbody.checked = true; crawlSelectAllCbTbody.indeterminate = false;
  } else {
    crawlSelectAllCb.checked = false; crawlSelectAllCb.indeterminate = true;
    crawlSelectAllCbTbody.checked = false; crawlSelectAllCbTbody.indeterminate = true;
  }
  
  const n = crawlSelectedIds.size;
  crawlSelCount.textContent = n ? `${n} selected` : "";
  crawlDeleteSel.classList.toggle("hidden", n === 0);
  crawlSendParamsBtn.disabled = n === 0;
}

[crawlSelectAllCb, crawlSelectAllCbTbody].forEach(cb => {
  cb.addEventListener("change", () => {
    const visible = getFilteredCrawl();
    if (cb.checked) visible.forEach(r => crawlSelectedIds.add(r.id));
    else visible.forEach(r => crawlSelectedIds.delete(r.id));
    renderCrawlTable();
  });
});

crawlTbody.addEventListener("change", (e) => {
  const cb = e.target;
  if (!cb.classList.contains("row-cb")) return;
  const tr = cb.closest("tr[data-id]");
  if (!tr) return;
  if (cb.checked) crawlSelectedIds.add(tr.dataset.id);
  else crawlSelectedIds.delete(tr.dataset.id);
  updateCrawlSelectionUI();
});

function renderCrawlTable() {
  crawlTbody.innerHTML = "";
  
  const visible = getFilteredCrawl();
  
  const isFiltered = crawlDomainSelect.value || crawlExtSelect.value || crawlSearch.value.trim();
  if (!allCrawlResults.length) {
    crawlCount.textContent = "";
  } else if (isFiltered) {
    crawlCount.textContent = `(${visible.length}/${allCrawlResults.length})`;
  } else {
    crawlCount.textContent = `(${allCrawlResults.length})`;
  }
  
  if (!visible.length) {
    crawlEmpty.classList.remove("hidden");
    crawlTable.classList.add("hidden");
  } else {
    crawlEmpty.classList.add("hidden");
    crawlTable.classList.remove("hidden");
  }
  
  crawlToolbar.classList.toggle("hidden", !allCrawlResults.length);

  for (const r of visible) {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    
    const checked = crawlSelectedIds.has(r.id) ? "checked" : "";
    const shortUrl = r.url.length > 80 ? r.url.slice(0, 80) + "..." : r.url;
    
    tr.innerHTML = `
      <td><input type="checkbox" class="row-cb" ${checked}></td>
      <td><a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(shortUrl)}</a></td>
      <td><span class="chip chip-sm">${escapeHtml(r.source)}</span></td>
    `;
    crawlTbody.appendChild(tr);
  }
  
  updateCrawlSelectionUI();
}

async function loadCrawlResults() {
  const res = await fetch(`/api/projects/${PID}/crawl`);
  if (!res.ok) return;
  const data = await res.json();
  allCrawlResults = data.results || [];
  
  const currentDomain = crawlDomainSelect.value;
  crawlDomainSelect.innerHTML = '<option value="">All domains</option>';
  for (const d of data.domains || []) {
    const opt = document.createElement("option");
    opt.value = d;
    opt.textContent = d;
    crawlDomainSelect.appendChild(opt);
  }
  if (data.domains.includes(currentDomain)) crawlDomainSelect.value = currentDomain;

  const liveIds = new Set(allCrawlResults.map(r => r.id));
  for (const id of [...crawlSelectedIds]) if (!liveIds.has(id)) crawlSelectedIds.delete(id);

  populateCrawlExtensions();
  renderCrawlTable();
  
  if (data.running_job_id) {
    setCrawlState(true, "⏳ Crawl is running in background...");
    pollCrawlJob(data.running_job_id);
  } else {
    setCrawlState(false);
  }
}

async function pollCrawlJob(jobId) {
  if (crawlPolling && jobId === pollCrawlJob._currentId) return;
  pollCrawlJob._currentId = jobId;
  crawlPolling = true;
  
  setCrawlState(true, "⏳ Crawling started...");

  try {
    while (true) {
      const res = await fetch(`/api/projects/${PID}/crawl/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      
      if (job.status === "running") {
        setCrawlState(true, `⏳ Crawling... Found ${job.found_count} URLs so far.`);
      } else if (job.status === "done") {
        setCrawlState(false, `✅ Crawl finished. Found ${job.found_count} URLs.`);
        await loadCrawlResults();
        break;
      } else if (job.status === "failed") {
        setCrawlState(false, `❌ Crawl failed: ${job.error || "unknown error"}`);
        break;
      }
      await new Promise(r => setTimeout(r, 1500));
    }
  } finally {
    crawlPolling = false; pollCrawlJob._currentId = null;
  }
}

async function deleteCrawlSelected() {
  const ids = [...crawlSelectedIds];
  if (!ids.length) return;
  if (!(await showConfirm(`Delete ${ids.length} crawled URL${ids.length === 1 ? "" : "s"}?`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/crawl`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!res.ok) throw new Error("Failed to delete");
    const del = new Set(ids);
    allCrawlResults = allCrawlResults.filter(r => !del.has(r.id));
    crawlSelectedIds.clear();
    renderCrawlTable();
  } catch { showToast("Network error while deleting."); }
}

async function deleteCrawlAll() {
  if (!allCrawlResults.length) return;
  if (!(await showConfirm(`Delete ALL ${allCrawlResults.length} crawled URLs?\nThis cannot be undone.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/crawl`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    if (!res.ok) throw new Error("Failed to delete");
    allCrawlResults = []; crawlSelectedIds.clear();
    loadCrawlResults();
  } catch { showToast("Network error while deleting."); }
}

crawlDeleteSel.addEventListener("click", deleteCrawlSelected);
crawlDeleteAll.addEventListener("click", deleteCrawlAll);

/* ════════════════════════════════════════════════════════════════════
   PARAMETER DISCOVERY
   ════════════════════════════════════════════════════════════════════ */

const paramsCount      = $("#params-count");
const paramsStatus     = $("#params-status");
const paramsEmpty      = $("#params-empty");
const paramsList       = $("#params-list");
const paramsSelectAllCb = $("#params-select-all-cb");
const paramsToolbar    = $("#params-toolbar");
const paramsSelCount   = $("#params-sel-count");
const paramsDeleteSel  = $("#params-delete-sel-btn");
const paramsDeleteAll  = $("#params-delete-all-btn");
const paramsSearch     = $("#params-search");
const paramsAddInput   = $("#params-add-input");
const paramsAddBtn     = $("#params-add-btn");
const paramsSortBtn    = $("#params-sort-btn");

let paramsPolling = false;
let allParams = [];
const paramSelectedWords = new Set();
let currentSort = "asc";

crawlSendParamsBtn.addEventListener("click", async () => {
  const selected = allCrawlResults.filter(r => crawlSelectedIds.has(r.id));
  if (!selected.length) {
    showToast("Select at least one crawled URL to run parameter discovery.");
    return;
  }
  
  const urls = selected.map(r => r.url).filter(Boolean);
  if (!urls.length) return;

  if (paramsPolling) {
    showToast("A parameter scan is already running.");
    return;
  }

  startParamsScan(urls);
});

async function startParamsScan(urls) {
  paramsPolling = true;
  paramsStatus.textContent = `⏳ Starting parameter discovery on ${urls.length} URLs...`;
  paramsStatus.style.color = "var(--accent)";

  try {
    const res = await fetch(`/api/projects/${PID}/params/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls }),
    });
    const data = await res.json().catch(() => ({}));
    
    if (res.status === 409 && data.job_id) {
      pollParamsJob(data.job_id);
      return;
    }
    if (!res.ok) throw new Error(data.error || "Failed to start scan");
    
    pollParamsJob(data.job_id);
  } catch (err) {
    paramsPolling = false;
    paramsStatus.textContent = `❌ ${err.message}`;
    paramsStatus.style.color = "var(--danger)";
  }
}

async function pollParamsJob(jobId) {
  if (paramsPolling && jobId === pollParamsJob._currentId) return;
  pollParamsJob._currentId = jobId;
  paramsPolling = true;

  try {
    while (true) {
      const res = await fetch(`/api/projects/${PID}/params/jobs/${jobId}`);
      if (!res.ok) break;
      const job = await res.json();
      
      if (job.status === "running") {
        paramsStatus.textContent = `⏳ Running fallparams on ${job.total_urls} URLs...`;
      } else if (job.status === "done") {
        paramsStatus.textContent = `✅ Done — found ${job.found_count} new parameters.`;
        paramsStatus.style.color = "var(--success, #10b981)";
        await loadParams();
        break;
      } else if (job.status === "failed") {
        paramsStatus.textContent = `❌ Failed: ${job.error || "unknown error"}`;
        paramsStatus.style.color = "var(--danger)";
        break;
      }
      await new Promise(r => setTimeout(r, 1500));
    }
  } finally {
    paramsPolling = false;
    pollParamsJob._currentId = null;
  }
}

async function loadParams() {
  const res = await fetch(`/api/projects/${PID}/params`);
  if (!res.ok) return;
  const data = await res.json();
  allParams = data.params || [];
  
  const liveWords = new Set(allParams);
  for (const w of [...paramSelectedWords]) if (!liveWords.has(w)) paramSelectedWords.delete(w);

  renderParams();
}

function renderParams() {
  const q = paramsSearch.value.toLowerCase().trim();
  let filtered = allParams;
  
  if (q) {
    filtered = filtered.filter(w => w.toLowerCase().includes(q));
  }
  
  if (currentSort === "asc") {
    filtered.sort((a, b) => a.localeCompare(b));
  } else {
    filtered.sort((a, b) => b.localeCompare(a));
  }

  if (!allParams.length) {
    paramsCount.textContent = "";
  } else if (q) {
    paramsCount.textContent = `(${filtered.length}/${allParams.length})`;
  } else {
    paramsCount.textContent = `(${allParams.length})`;
  }

  paramsList.innerHTML = "";
  paramsToolbar.classList.toggle("hidden", allParams.length === 0);

  if (!filtered.length) {
    paramsEmpty.classList.remove("hidden");
    paramsList.classList.add("hidden");
  } else {
    paramsEmpty.classList.add("hidden");
    paramsList.classList.remove("hidden");
  }

  for (const word of filtered) {
    const li = document.createElement("li");
    li.dataset.word = word;
    const checked = paramSelectedWords.has(word) ? "checked" : "";
    
    li.innerHTML = `
      <input type="checkbox" class="row-cb" ${checked} aria-label="Select ${escapeHtml(word)}">
      <code>${escapeHtml(word)}</code>
    `;
    paramsList.appendChild(li);
  }

  updateParamsSelectionUI();
}

function updateParamsSelectionUI() {
  const q = paramsSearch.value.toLowerCase().trim();
  let visible = allParams;
  if (q) visible = visible.filter(w => w.toLowerCase().includes(q));

  const visibleSel = visible.filter(w => paramSelectedWords.has(w)).length;

  if (!visible.length || visibleSel === 0) {
    paramsSelectAllCb.checked = false; paramsSelectAllCb.indeterminate = false;
  } else if (visibleSel === visible.length) {
    paramsSelectAllCb.checked = true; paramsSelectAllCb.indeterminate = false;
  } else {
    paramsSelectAllCb.checked = false; paramsSelectAllCb.indeterminate = true;
  }

  const n = paramSelectedWords.size;
  paramsSelCount.textContent = n ? `${n} selected` : "";
  paramsDeleteSel.classList.toggle("hidden", n === 0);
  paramsSelectAllCb.disabled = visible.length === 0;
}

paramsSearch.addEventListener("input", renderParams);

paramsSelectAllCb.addEventListener("change", () => {
  const q = paramsSearch.value.toLowerCase().trim();
  let visible = allParams;
  if (q) visible = visible.filter(w => w.toLowerCase().includes(q));
  
  if (paramsSelectAllCb.checked) visible.forEach(w => paramSelectedWords.add(w));
  else visible.forEach(w => paramSelectedWords.delete(w));
  
  renderParams();
});

paramsList.addEventListener("change", (e) => {
  const cb = e.target;
  if (!cb.classList.contains("row-cb")) return;
  const li = cb.closest("li[data-word]");
  if (!li) return;
  
  if (cb.checked) paramSelectedWords.add(li.dataset.word);
  else paramSelectedWords.delete(li.dataset.word);
  
  updateParamsSelectionUI();
});

paramsSortBtn.addEventListener("click", () => {
  currentSort = currentSort === "asc" ? "desc" : "asc";
  paramsSortBtn.textContent = `Sort: ${currentSort === "asc" ? "A-Z" : "Z-A"}`;
  renderParams();
});

paramsAddBtn.addEventListener("click", async () => {
  const word = paramsAddInput.value.trim();
  if (!word) return;

  paramsAddBtn.disabled = true;
  try {
    const res = await fetch(`/api/projects/${PID}/params`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word }),
    });
    
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || "Failed to add parameter");
    }
    
    paramsAddInput.value = "";
    await loadParams();
    showToast(`Added parameter: ${word}`);
  } catch (err) {
    showToast(err.message);
  } finally {
    paramsAddBtn.disabled = false;
  }
});

paramsAddInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    paramsAddBtn.click();
  }
});

async function deleteParamsSelected() {
  const words = [...paramSelectedWords];
  if (!words.length) return;
  if (!(await showConfirm(`Delete ${words.length} selected parameter${words.length === 1 ? "" : "s"}?`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/params`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ words }),
    });
    if (!res.ok) throw new Error("Failed to delete");
    
    paramSelectedWords.clear();
    await loadParams();
  } catch (err) {
    showToast("Network error while deleting.");
  }
}

async function deleteParamsAll() {
  if (!allParams.length) return;
  if (!(await showConfirm(`Delete ALL ${allParams.length} parameters?\nThis cannot be undone.`))) return;

  try {
    const res = await fetch(`/api/projects/${PID}/params`, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    if (!res.ok) throw new Error("Failed to delete");
    
    allParams = [];
    paramSelectedWords.clear();
    renderParams();
  } catch (err) {
    showToast("Network error while deleting.");
  }
}

paramsDeleteSel.addEventListener("click", deleteParamsSelected);
paramsDeleteAll.addEventListener("click", deleteParamsAll);


/* ════════════════════════════════════════════════════════════════════
   INIT
   ════════════════════════════════════════════════════════════════════ */

btn.addEventListener("click", startDiscovery);
loadTools();
loadSubdomains();
loadResolverInfo();
loadResolved();
loadProberInfo();
loadHttpxResults();
loadHttpxChanges();
loadCrawlResults();
loadParams();