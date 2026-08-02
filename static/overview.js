(() => {
    "use strict";

    const state = { cameras: [], incidents: [], selected: null };
    const el = (id) => document.getElementById(id);

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    }

    async function json(url) {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
        return payload;
    }

    function activeIncident(cameraId) {
        return state.incidents.find((item) => item.camera_id === cameraId && !item.closed_at) || null;
    }

    function unsafeCount(cameraId) {
        return state.incidents.filter((item) => item.camera_id === cameraId && item.unsafe_at).length;
    }

    function cameraTone(camera) {
        if (camera.state === "ONLINE") return ["online", "ONLINE"];
        if (["CONNECTING", "RECONNECTING"].includes(camera.state)) return ["warning", camera.state];
        if (camera.state === "ERROR") return ["unsafe", "ERROR"];
        return ["offline", camera.state];
    }

    function safetyTone(camera) {
        const incident = activeIncident(camera.camera_id);
        if (!incident) return ["online", "CLEAR"];
        return incident.unsafe_at ? ["unsafe", "UNSAFE"] : ["warning", "WARNING"];
    }

    function filteredCameras() {
        const query = el("camera-search").value.trim().toLowerCase();
        const requiredState = el("camera-state").value;
        const sort = el("camera-sort").value;
        const rows = state.cameras.filter((camera) => {
            if (query && !`${camera.camera_id} ${camera.name}`.toLowerCase().includes(query)) return false;
            return !requiredState || camera.state === requiredState;
        });
        rows.sort((a, b) => {
            if (sort === "state") return a.state.localeCompare(b.state) || a.camera_id.localeCompare(b.camera_id);
            if (sort === "events") return unsafeCount(b.camera_id) - unsafeCount(a.camera_id) || a.camera_id.localeCompare(b.camera_id);
            return a.camera_id.localeCompare(b.camera_id);
        });
        return rows;
    }

    function render() {
        const rows = filteredCameras();
        const stamp = Date.now();
        el("camera-grid").innerHTML = rows.map((camera) => {
            const [tone, connection] = cameraTone(camera);
            const [safetyClass, safety] = safetyTone(camera);
            const canView = camera.state === "ONLINE";
            const resolution = camera.width && camera.height ? `${camera.width}×${camera.height}` : "Waiting";
            const image = canView
                ? `<img src="/api/cameras/${encodeURIComponent(camera.camera_id)}/snapshot?t=${stamp}" alt="${escapeHtml(camera.camera_id)} latest frame" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="camera-placeholder" style="display:none"><span class="material-symbols-outlined">videocam_off</span><span>Frame unavailable</span></div>`
                : '<div class="camera-placeholder"><span class="material-symbols-outlined">videocam_off</span><span>Camera is not streaming</span></div>';
            return `<article class="camera-card">
                <div class="camera-frame">${image}<span class="badge ${tone} camera-badge">${escapeHtml(connection)}</span></div>
                <div class="camera-meta">
                    <div class="camera-title-row"><div><h2 class="camera-title">${escapeHtml(camera.camera_id)}</h2><p class="camera-name">${escapeHtml(camera.name)}</p></div><span class="badge ${safetyClass}">${safety}</span></div>
                    <div class="metric-grid">
                        <div class="metric"><span>Resolution</span><strong>${resolution}</strong></div>
                        <div class="metric"><span>Capture FPS</span><strong>${Number(camera.capture_fps || 0).toFixed(1)}</strong></div>
                        <div class="metric"><span>Unsafe events</span><strong>${unsafeCount(camera.camera_id)}</strong></div>
                    </div>
                    <div class="card-footer"><button class="btn view-camera" data-camera="${escapeHtml(camera.camera_id)}" ${canView ? "" : "disabled"}><span class="material-symbols-outlined">videocam</span>Live feed</button><a class="btn" href="/event.html?camera_id=${encodeURIComponent(camera.camera_id)}"><span class="material-symbols-outlined">list_alt</span>Events</a></div>
                </div>
            </article>`;
        }).join("");
        el("camera-empty").classList.toggle("visible", rows.length === 0);
    }

    function renderStats(summary) {
        const configured = state.cameras.filter((camera) => camera.configured).length;
        const online = state.cameras.filter((camera) => camera.state === "ONLINE").length;
        const warnings = state.incidents.filter((item) => !item.closed_at && !item.unsafe_at).length;
        el("stat-cameras").textContent = configured;
        el("stat-online").textContent = online;
        el("stat-warnings").textContent = warnings;
        el("stat-unsafe").textContent = summary.unsafe || 0;
        const badge = el("connection-badge");
        badge.classList.toggle("online", online > 0);
        badge.classList.toggle("degraded", online === 0);
        el("connection-label").textContent = online ? `${online} of ${configured} configured online` : "Runtime not streaming";
    }

    async function load() {
        const button = el("refresh-overview");
        button.disabled = true;
        try {
            const [cameraData, incidentData, summary] = await Promise.all([
                json("/api/cameras"), json("/api/incidents?limit=500"), json("/api/dashboard/summary?recent_limit=10"),
            ]);
            state.cameras = cameraData.cameras || [];
            state.incidents = incidentData.incidents || [];
            renderStats(summary);
            render();
        } catch (error) {
            el("connection-badge").classList.add("degraded");
            el("connection-label").textContent = "System unavailable";
            el("camera-empty").classList.add("visible");
            el("camera-empty").querySelector("p").textContent = error.message;
        } finally {
            button.disabled = false;
        }
    }

    function openCamera(cameraId) {
        const camera = state.cameras.find((item) => item.camera_id === cameraId);
        if (!camera || camera.state !== "ONLINE") return;
        state.selected = camera;
        el("camera-modal-title").textContent = `${camera.camera_id} · ${camera.name}`;
        el("camera-detail-state").textContent = camera.state;
        el("camera-detail-resolution").textContent = camera.width && camera.height ? `${camera.width}×${camera.height}` : "Waiting";
        el("camera-detail-fps").textContent = `${Number(camera.capture_fps || 0).toFixed(1)} FPS`;
        el("camera-detail-reconnects").textContent = camera.reconnect_count || 0;
        el("camera-events-link").href = `/event.html?camera_id=${encodeURIComponent(camera.camera_id)}`;
        const image = el("camera-live-feed");
        const notice = el("camera-feed-notice");
        notice.style.display = "none";
        image.style.display = "block";
        image.onerror = () => { image.style.display = "none"; notice.style.display = "block"; };
        image.src = `/api/cameras/${encodeURIComponent(camera.camera_id)}/stream?t=${Date.now()}`;
        el("camera-modal").classList.add("open");
    }

    function closeCamera() {
        const image = el("camera-live-feed");
        image.onerror = null;
        image.removeAttribute("src");
        el("camera-modal").classList.remove("open");
        state.selected = null;
    }

    ["camera-search", "camera-state", "camera-sort"].forEach((id) => {
        el(id).addEventListener(id === "camera-search" ? "input" : "change", render);
    });
    el("refresh-overview").addEventListener("click", load);
    el("camera-grid").addEventListener("click", (event) => {
        const button = event.target.closest(".view-camera");
        if (button) openCamera(button.dataset.camera);
    });
    el("camera-modal-close").addEventListener("click", closeCamera);
    el("camera-modal").addEventListener("click", (event) => { if (event.target === el("camera-modal")) closeCamera(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeCamera(); });
    load();
    setInterval(() => { if (!state.selected) load(); }, 10000);
})();
