(() => {
    "use strict";

    const PAGE_SIZE = 12;
    const names = {
        monitoring: "Safe Monitoring",
        restricted_zone: "Restricted Zone",
        unsafe_proximity: "Unsafe Proximity",
        ppe_compliance: "PPE Compliance",
    };
    const state = {
        entries: [], filtered: [], page: 1, selected: new Set(),
        active: null, filtersInitialized: false, deepLinkOpened: false,
    };
    const el = (id) => document.getElementById(id);

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    }

    async function json(url, options = {}) {
        const response = await fetch(url, { cache: "no-store", ...options });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
        return payload;
    }

    function isActive(incident) {
        return !incident.closed_at;
    }

    function formatTime(timestamp) {
        if (!timestamp) return "-";
        return new Date(Number(timestamp) * 1000).toLocaleString([], {
            year: "numeric", month: "short", day: "2-digit",
            hour: "2-digit", minute: "2-digit", second: "2-digit",
        });
    }

    function duration(incident) {
        const end = Number(incident.closed_at || incident.updated_at || incident.opened_at);
        const seconds = Math.max(0, Math.round(end - Number(incident.opened_at)));
        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        return `${minutes}m ${seconds % 60}s`;
    }

    function applyFilters() {
        const query = el("search-filter").value.trim().toLowerCase();
        const camera = el("camera-filter").value;
        const detector = el("detector-filter").value;
        const severity = el("severity-filter").value;
        state.filtered = state.entries.filter((entry) => {
            const haystack = `${entry.entry_id} ${entry.camera_id} ${entry.message} ${names[entry.detector] || entry.detector}`.toLowerCase();
            if (query && !haystack.includes(query)) return false;
            if (camera && entry.camera_id !== camera) return false;
            if (detector && entry.detector !== detector) return false;
            if (severity && entry.level !== severity) return false;
            return true;
        });
        state.page = 1;
        render();
    }

    function renderStats() {
        el("stat-total").textContent = state.entries.length;
        el("stat-safe").textContent = state.entries.filter((item) => item.level === "SAFE").length;
        el("stat-warning").textContent = state.entries.filter((item) => item.level === "WARNING").length;
        el("stat-unsafe").textContent = state.entries.filter((item) => item.level === "UNSAFE").length;
    }

    function render() {
        const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
        state.page = Math.min(state.page, totalPages);
        const start = (state.page - 1) * PAGE_SIZE;
        const rows = state.filtered.slice(start, start + PAGE_SIZE);
        el("event-rows").innerHTML = rows.map((entry) => {
            const level = entry.level;
            const evidence = level === "UNSAFE" && entry.clip_path
                ? '<span class="green">Clip ready</span>'
                : (level === "UNSAFE" ? '<span class="yellow">Processing / unavailable</span>' : '<span class="small">Not required</span>');
            const selectable = entry.incident_id !== null;
            const detailAction = selectable
                ? `<button class="icon-btn view-incident" data-id="${entry.incident_id}" title="View details"><span class="material-symbols-outlined">visibility</span></button>`
                : '<span class="small">—</span>';
            const downloadAction = level === "UNSAFE" && entry.clip_path
                ? `<a class="icon-btn" href="/api/incidents/${entry.incident_id}/clip" download title="Download clip"><span class="material-symbols-outlined">download</span></a>`
                : "";
            return `<tr>
                <td class="check-cell">${selectable ? `<input class="row-check" type="checkbox" data-id="${entry.incident_id}" ${state.selected.has(entry.incident_id) ? "checked" : ""} aria-label="Select incident ${entry.incident_id}">` : ""}</td>
                <td><div class="mono">${escapeHtml(formatTime(entry.timestamp))}</div><div class="small">${escapeHtml(entry.entry_id)}</div></td>
                <td class="title-cell">${escapeHtml(entry.camera_id)}</td>
                <td><div class="title-cell">${escapeHtml(names[entry.detector] || entry.detector)}</div></td>
                <td><span class="badge ${level.toLowerCase()}">${escapeHtml(level)}</span></td>
                <td>${escapeHtml(entry.message)}</td>
                <td class="hide-mobile">${evidence}</td>
                <td><div class="row-actions">${detailAction}${downloadAction}</div></td>
            </tr>`;
        }).join("");
        el("empty-state").classList.toggle("visible", rows.length === 0);
        el("page-summary").textContent = rows.length
            ? `Showing ${start + 1}-${start + rows.length} of ${state.filtered.length} logs`
            : "Showing 0 logs";
        el("page-number").textContent = `Page ${state.page} of ${totalPages}`;
        el("previous-page").disabled = state.page <= 1;
        el("next-page").disabled = state.page >= totalPages;
        const selectableRows = rows.filter((item) => item.incident_id !== null);
        el("select-all").checked = selectableRows.length > 0 && selectableRows.every((item) => state.selected.has(item.incident_id));
        updateExportButton();
    }

    function updateExportButton() {
        const button = el("export-selected");
        button.disabled = state.selected.size === 0;
        button.lastChild.textContent = state.selected.size ? `Export selected (${state.selected.size})` : "Export selected";
    }

    async function openIncident(id) {
        try {
            const incident = await json(`/api/incidents/${id}`);
            state.active = incident;
            const level = incident.unsafe_at ? "UNSAFE" : "WARNING";
            el("modal-title").textContent = `Incident #${incident.id}`;
            el("detail-camera").textContent = incident.camera_id;
            el("detail-detector").textContent = names[incident.detector] || incident.detector;
            el("detail-outcome").textContent = isActive(incident) ? `${level} · ACTIVE` : level;
            el("detail-duration").textContent = duration(incident);
            el("detail-note").textContent = `Opened ${formatTime(incident.opened_at)}${incident.closed_at ? ` · Closed ${formatTime(incident.closed_at)}` : " · Incident is still active"}${incident.close_reason ? ` · ${incident.close_reason}` : ""}`;
            const video = el("incident-video");
            const notice = el("clip-notice");
            const download = el("download-clip");
            if (incident.clip_path) {
                const browserClipUrl = `/api/incidents/${incident.id}/browser-clip`;
                const downloadUrl = `/api/incidents/${incident.id}/clip`;
                video.src = browserClipUrl;
                video.style.display = "block";
                notice.style.display = "none";
                download.href = downloadUrl;
                download.style.display = "inline-flex";
            } else {
                video.removeAttribute("src");
                video.load();
                video.style.display = "none";
                notice.style.display = "block";
                download.style.display = "none";
            }
            el("delete-incident").style.display = isActive(incident) ? "none" : "inline-flex";
            el("incident-modal").classList.add("open");
        } catch (error) {
            window.alert(`Could not load the incident.\n\n${error.message}`);
        }
    }

    function closeModal() {
        const video = el("incident-video");
        video.pause();
        video.removeAttribute("src");
        video.load();
        el("incident-modal").classList.remove("open");
        state.active = null;
    }

    async function load() {
        try {
            const [logData, cameraData] = await Promise.all([
                json("/api/log-entries?limit=500"),
                json("/api/cameras"),
            ]);
            state.entries = logData.entries || [];
            const cameras = cameraData.cameras || [];
            const selectedCamera = el("camera-filter").value;
            el("camera-filter").innerHTML = '<option value="">All cameras</option>' + cameras.map((camera) => `<option value="${escapeHtml(camera.camera_id)}">${escapeHtml(camera.camera_id)} · ${escapeHtml(camera.name)}</option>`).join("");
            const online = cameras.filter((camera) => camera.state === "ONLINE").length;
            const badge = el("connection-badge");
            badge.classList.toggle("online", online > 0);
            badge.classList.toggle("degraded", online === 0);
            el("connection-label").textContent = online ? `${online} camera${online === 1 ? "" : "s"} online` : "Runtime not streaming";
            if (!state.filtersInitialized) {
                const params = new URLSearchParams(location.search);
                if (params.get("camera_id")) el("camera-filter").value = params.get("camera_id");
                if (params.get("q")) el("search-filter").value = params.get("q");
                state.filtersInitialized = true;
            } else {
                el("camera-filter").value = selectedCamera;
            }
            renderStats();
            applyFilters();
            if (!state.deepLinkOpened) {
                const incidentId = Number(new URLSearchParams(location.search).get("incident_id"));
                state.deepLinkOpened = true;
                if (incidentId && state.entries.some((item) => item.incident_id === incidentId)) {
                    await openIncident(incidentId);
                }
            }
        } catch (error) {
            el("connection-label").textContent = "System unavailable";
            el("connection-badge").classList.add("degraded");
            el("empty-state").classList.add("visible");
            el("empty-state").querySelector("p").textContent = error.message;
        }
    }

    ["search-filter", "camera-filter", "detector-filter", "severity-filter"].forEach((id) => {
        el(id).addEventListener(id === "search-filter" ? "input" : "change", applyFilters);
    });
    el("clear-filters").addEventListener("click", () => {
        ["search-filter", "camera-filter", "detector-filter", "severity-filter"].forEach((id) => { el(id).value = ""; });
        applyFilters();
    });
    el("previous-page").addEventListener("click", () => { state.page--; render(); });
    el("next-page").addEventListener("click", () => { state.page++; render(); });
    el("event-rows").addEventListener("click", (event) => {
        const view = event.target.closest(".view-incident");
        if (view) openIncident(Number(view.dataset.id));
        const check = event.target.closest(".row-check");
        if (check) {
            const id = Number(check.dataset.id);
            check.checked ? state.selected.add(id) : state.selected.delete(id);
            updateExportButton();
        }
    });
    el("select-all").addEventListener("change", (event) => {
        const start = (state.page - 1) * PAGE_SIZE;
        state.filtered.slice(start, start + PAGE_SIZE)
            .filter((item) => item.incident_id !== null)
            .forEach((item) => event.target.checked ? state.selected.add(item.incident_id) : state.selected.delete(item.incident_id));
        render();
    });
    el("export-selected").addEventListener("click", () => {
        if (state.selected.size) location.href = `/api/incidents/export.zip?ids=${[...state.selected].join(",")}`;
    });
    el("modal-close").addEventListener("click", closeModal);
    el("incident-modal").addEventListener("click", (event) => { if (event.target === el("incident-modal")) closeModal(); });
    el("delete-incident").addEventListener("click", async () => {
        if (!state.active || !window.confirm(`Delete incident #${state.active.id} and its evidence clip?`)) return;
        try {
            await json(`/api/incidents/${state.active.id}`, { method: "DELETE" });
            state.selected.delete(state.active.id);
            closeModal();
            await load();
        } catch (error) {
            window.alert(`Could not delete the incident.\n\n${error.message}`);
        }
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeModal(); });
    load();
    setInterval(load, 10000);
})();
