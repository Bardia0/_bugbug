const $ = (sel) => document.querySelector(sel);

const modal = $("#modal");
if (modal) initProjectsPage();

function initProjectsPage() {
  const projectsData = JSON.parse($("#projects-data").textContent || "[]");
  const byId = new Map(projectsData.map((p) => [p.id, p]));

  const form = $("#project-form");
  const title = $("#modal-title");
  const errBox = $("#form-error");
  const saveBtn = $("#save-btn");

  const fId = $("#f-id");
  const fName = $("#f-name");
  const fPlatform = $("#f-platform");
  const fUrl = $("#f-url");
  const fScope = $("#f-scope");
  const fNotes = $("#f-notes");

  function showError(msg) {
    errBox.textContent = msg;
    errBox.classList.remove("hidden");
  }

  function setPlatform(value) {
    const v = value || "";
    if (v && ![...fPlatform.options].some((o) => o.value === v)) {
      fPlatform.add(new Option(v, v));
    }
    fPlatform.value = v;
  }

  function openModal(project = null) {
    form.reset();
    errBox.classList.add("hidden");
    fId.value = project ? project.id : "";
    title.textContent = project ? "Edit project" : "New project";
    if (project) {
      fName.value = project.name || "";
      setPlatform(project.platform);
      fUrl.value = project.program_url || "";
      fScope.value = (project.scope || []).join("\n");
      fNotes.value = project.notes || "";
    } else {
      setPlatform("");
    }
    modal.classList.remove("hidden");
    fName.focus();
  }

  function closeModal() {
    modal.classList.add("hidden");
  }

  $("#new-project-btn")?.addEventListener("click", () => openModal());
  $("#new-project-btn-empty")?.addEventListener("click", () => openModal());
  $("#cancel-btn").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
  });

  // Edit / Delete via delegation (works for every card)
  document.addEventListener("click", (e) => {
    const editBtn = e.target.closest('[data-action="edit"]');
    if (editBtn) {
      e.preventDefault();
      const p = byId.get(editBtn.dataset.id);
      if (p) openModal(p);
      return;
    }
    const delBtn = e.target.closest('[data-action="delete"]');
    if (delBtn) {
      e.preventDefault();
      const p = byId.get(delBtn.dataset.id);
      if (p) deleteProject(p);
    }
  });

  async function deleteProject(p) {
    if (!(await showConfirm(`Are you sure you want to delete "${p.name}"?\nThis cannot be undone.`, "Delete Project"))) return;
    
    try {
      const res = await fetch(`/api/projects/${p.id}`, { method: "DELETE" });
      if (res.ok) {
        window.location.href = "/";
      } else {
        const data = await res.json().catch(() => ({}));
        showToast(data.error || "Failed to delete project.");
      }
    } catch {
      showToast("Network error while deleting.");
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = fId.value;
    const payload = {
      name: fName.value.trim(),
      platform: fPlatform.value,
      program_url: fUrl.value.trim(),
      scope: fScope.value,
      notes: fNotes.value,
    };
    if (!payload.name) {
      showError("Project name is required.");
      return;
    }
    saveBtn.disabled = true;
    try {
      const res = await fetch(id ? `/api/projects/${id}` : "/api/projects", {
        method: id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showError(data.error || "Something went wrong.");
        return;
      }
      window.location.href = "/";
    } catch {
      showError("Network error.");
    } finally {
      saveBtn.disabled = false;
    }
  });

  const editId = new URLSearchParams(window.location.search).get("edit");
  if (editId && byId.has(editId)) openModal(byId.get(editId));
}