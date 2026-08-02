(() => {
    "use strict";

    const CAMERA_IDS = { CAM1: "CAM 01", CAM2: "CAM 02", CAM3: "CAM 03" };
    const SLOT_BY_ID = Object.fromEntries(
        Object.entries(CAMERA_IDS).map(([slot, id]) => [id, slot])
    );
    const SELECTED_CAMERA_KEY = "sentrylab.selectedCameraSlot";
    const ZOOM_LEVELS_KEY = "sentrylab.cameraZoomLevels";
    function savedCameraSlot() {
        try {
            const slot = window.localStorage.getItem(SELECTED_CAMERA_KEY);
            return CAMERA_IDS[slot] ? slot : "CAM1";
        } catch (_error) {
            return "CAM1";
        }
    }
    function savedZoomLevels() {
        try {
            return { "CAM 01": 1, "CAM 02": 1, ...JSON.parse(window.localStorage.getItem(ZOOM_LEVELS_KEY) || "{}") };
        } catch (_error) {
            return { "CAM 01": 1, "CAM 02": 1 };
        }
    }
    const state = {
        selectedSlot: savedCameraSlot(),
        cameras: {},
        feedEnabled: { CAM1: false, CAM2: false, CAM3: false },
        restrictedZone: {},
        restrictedStatus: {},
        proximity: {},
        proximityStatus: {},
        ppe: {},
        ppeStatus: {},
        modelStatus: null,
        alarmStatus: null,
        controls: {},
        presets: {},
        zoomLevels: savedZoomLevels(),
        presetSaveMode: false,
        controlBusy: false,
        configSnapshot: null,
        zonePoints: [],
        draggedZonePoint: -1,
        polling: false,
    };

    const byId = (id) => document.getElementById(id);
    const all = (selector) => Array.from(document.querySelectorAll(selector));
    const selectedCameraId = () => CAMERA_IDS[state.selectedSlot];
    const selectedCamera = () => state.cameras[selectedCameraId()] || null;
    const restrictedSettings = (cameraId = selectedCameraId()) =>
        state.restrictedZone[cameraId]?.settings || {
            enabled: false,
            overlay_enabled: true,
        };
    const proximitySettings = (cameraId = selectedCameraId()) =>
        state.proximity[cameraId]?.settings || {
            enabled: false,
            overlay_enabled: true,
        };
    const ppeSettings = (cameraId = selectedCameraId()) =>
        state.ppe[cameraId]?.settings || {
            enabled: false,
            overlay_enabled: true,
        };

    async function getJson(url, options = {}) {
        const response = await fetch(url, { cache: "no-store", ...options });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
        return payload;
    }

    function stateTone(camera) {
        if (!camera || ["ERROR", "UNCONFIGURED"].includes(camera.state)) {
            return { label: "OFFLINE", dot: "bg-red-500", text: "text-red-400", bg: "bg-red-500/20" };
        }
        if (camera.state === "ONLINE") {
            return { label: "ONLINE", dot: "bg-green-400", text: "text-green-400", bg: "bg-green-500/20" };
        }
        if (camera.state === "RECONNECTING" || camera.state === "CONNECTING") {
            return { label: camera.state, dot: "bg-yellow-400", text: "text-yellow-400", bg: "bg-yellow-500/20" };
        }
        return { label: camera.state, dot: "bg-slate-500", text: "text-on-surface-variant", bg: "bg-surface-variant" };
    }

    function setText(id, value) {
        const element = byId(id);
        if (element) element.textContent = value;
    }

    function updateSelectorDots() {
        Object.entries(CAMERA_IDS).forEach(([slot, cameraId], index) => {
            const selector = byId(`cam-selector-${index + 1}`);
            const dot = byId(`${slot.toLowerCase()}-dot`);
            const camera = state.cameras[cameraId];
            const tone = stateTone(camera);
            if (selector) selector.classList.toggle("cam-active", slot === state.selectedSlot);
            if (dot) dot.className = `indicator-dot w-2 h-2 rounded-full ${tone.dot}`;
        });
    }

    function updateAlarmCard() {
        const alarm = state.alarmStatus;
        let label = "NOT SET";
        let detail = "Set SENTRYLAB_ALARM_COM_PORT to enable the ESP32 alarm";
        let classes = "bg-surface-variant text-on-surface-variant";
        if (alarm?.alarm_active) {
            label = "SOUNDING";
            detail = alarm.unsafe_sources.length
                ? `UNSAFE: ${alarm.unsafe_sources.join(" + ")}`
                : "Confirmed unsafe condition";
            classes = "bg-red-500/20 text-red-400";
        } else if (alarm?.connected) {
            label = "CONNECTED";
            detail = `${alarm.port} at ${alarm.baud_rate} baud`;
            classes = "bg-green-500/20 text-green-400";
        } else if (alarm?.configured) {
            label = "RECONNECTING";
            detail = alarm.last_error || `Waiting for ESP32 on ${alarm.port}`;
            classes = "bg-yellow-500/20 text-yellow-400";
        }
        setText("audio-alarm-status", label);
        setText("audio-alarm-detail", detail);
        const badge = byId("audio-alarm-status");
        if (badge) {
            badge.className = `px-2.5 py-1 text-[10px] font-bold rounded-full ${classes}`;
        }
    }

    function updateStatusCard() {
        const cameraId = selectedCameraId();
        const camera = selectedCamera();
        const tone = stateTone(camera);
        const feedOn = state.feedEnabled[state.selectedSlot];

        setText("camera-status-label", `${cameraId} STATUS`);
        setText("camera-status-message", camera?.last_error || (
            camera?.state === "ONLINE" ? "Camera connection healthy" : "Waiting for camera runtime"
        ));
        setText("camera-operating-status", tone.label);
        setText("camera-feed-status", feedOn && camera?.state === "ONLINE" ? "ONLINE" : "STANDBY");
        const captureDetail = camera?.width && camera?.height
            ? `${camera.width}x${camera.height} | ${(camera.capture_fps || 0).toFixed(1)} FPS`
            : "Waiting for capture telemetry";
        setText(
            "camera-feed-detail",
            feedOn ? `Live shared-frame stream | ${captureDetail}` : "Camera feed is switched off"
        );
        const restricted = restrictedSettings(cameraId);
        const proximity = proximitySettings(cameraId);
        const ppe = ppeSettings(cameraId);
        const detectorStatus = state.restrictedStatus[cameraId];
        const proximityStatus = state.proximityStatus[cameraId];
        const ppeStatus = state.ppeStatus[cameraId];
        const activeNames = [];
        if (restricted.enabled) activeNames.push("Restricted Zone");
        if (proximity.enabled) activeNames.push("Unsafe Proximity");
        if (ppe.enabled) activeNames.push("PPE Compliance");
        const activeDetectors = [];
        if (restricted.enabled) activeDetectors.push("restricted_zone");
        if (proximity.enabled) activeDetectors.push("unsafe_proximity");
        if (ppe.enabled) activeDetectors.push("ppe_compliance");
        const missingGroups = (state.modelStatus?.groups || []).filter(
            (group) => activeDetectors.includes(group.detector) && !group.ready
        );
        const cameraPowered = Boolean(camera?.power_on);
        setText("ai-monitoring-status", !cameraPowered
            ? "PAUSED"
            : missingGroups.length
                ? "MODEL MISSING"
                : (activeNames.length ? "ACTIVE" : "OFF"));
        const inactiveMissing = !activeNames.length && state.modelStatus && !state.modelStatus.ready;
        setText("ai-monitoring-detail",
            !cameraPowered
                ? "Camera is off; all detector services are paused"
                : missingGroups.length
                ? `Install models for ${missingGroups.map((group) => group.label).join(" + ")}`
                : detectorStatus?.last_error || proximityStatus?.last_error || ppeStatus?.last_error ||
                (activeNames.length
                    ? `${activeNames.join(" + ")} monitoring enabled`
                    : inactiveMissing
                        ? `${state.modelStatus.missing_count} AI model files are not installed`
                        : "No AI detector enabled")
        );
        setText("camera-status-last-update", camera?.last_frame_at
            ? new Date(camera.last_frame_at * 1000).toLocaleTimeString()
            : "—");

        const operating = byId("camera-operating-status");
        if (operating) {
            operating.className = `px-3 py-1 rounded-full text-[10px] font-bold tracking-wide ${tone.bg} ${tone.text}`;
        }
        const feedStatus = byId("camera-feed-status");
        if (feedStatus) {
            const active = feedOn && camera?.state === "ONLINE";
            feedStatus.className = `px-2.5 py-1 text-[10px] font-bold rounded-full ${active ? "bg-green-500/20 text-green-400" : "bg-surface-variant text-on-surface-variant"}`;
        }
    }

    function renderUseCases() {
        setText("usecase-panel-title", `ACTIVE USE CASES — ${selectedCameraId()}`);
        const list = byId("usecase-list");
        if (!list) return;
        const restricted = restrictedSettings();
        const proximity = proximitySettings();
        const ppe = ppeSettings();
        const useCases = [
            ["clinical_notes", "PPE Detection", ppe.enabled, ppe.enabled ? "Coat + mask + gloves" : "Disabled"],
            ["fence", "Restricted Zone", restricted.enabled, restricted.enabled ? "Monitoring" : "Disabled"],
            ["social_distance", "Unsafe Proximity", proximity.enabled, proximity.enabled ? "Monitoring at 1.5 m" : "Disabled"],
        ];
        list.innerHTML = useCases.map(([icon, label, active, detail]) => `
            <div class="flex items-center gap-3 p-3 rounded-xl bg-surface-container-low border border-outline-variant/30 ${active ? "" : "opacity-60"}">
                <span class="material-symbols-outlined ${active ? "text-primary" : "text-outline"}">${icon}</span>
                <div class="flex-1">
                    <p class="text-[13px] font-bold text-on-surface">${label}</p>
                    <p class="text-[10px] text-outline uppercase tracking-wider">${detail}</p>
                </div>
                <span class="w-2 h-2 rounded-full ${active ? "bg-green-400" : "bg-slate-500"}"></span>
            </div>`).join("");
        const chips = byId("cam-status-usecase-chips");
        if (chips) {
            const labels = [];
            if (restricted.enabled) labels.push("Restricted Zone");
            if (proximity.enabled) labels.push("Unsafe Proximity");
            if (ppe.enabled) labels.push("PPE Compliance");
            chips.innerHTML = labels.length
                ? labels.map((label) => `<span class="text-[10px] text-primary">${label} active</span>`).join("")
                : '<span class="text-[10px] text-outline">No AI service active</span>';
        }
    }

    function hideFeeds() {
        ["tapo-stream-img", "pose-stream-img"].forEach((id) => {
            const image = byId(id);
            if (!image) return;
            image.classList.add("hidden");
            image.removeAttribute("src");
        });
        byId("main-video-feed")?.classList.remove("hidden");
    }

    function showSelectedFeed() {
        hideFeeds();
        const camera = selectedCamera();
        const feedOn = state.feedEnabled[state.selectedSlot];
        if (!feedOn || camera?.state !== "ONLINE") return;

        const image = byId("pose-stream-img");
        if (!image) return;
        image.onload = () => updateStatusCard();
        image.onerror = () => {
            setText("camera-feed-detail", "Stream unavailable; camera is reconnecting");
            setText("camera-feed-status", "WAITING");
        };
        image.src = `/api/cameras/${encodeURIComponent(selectedCameraId())}/stream`;
        image.classList.remove("hidden");
        byId("main-video-feed")?.classList.add("hidden");
    }

    function updatePowerButton() {
        const enabled = state.feedEnabled[state.selectedSlot];
        setText("camera-power-label", `Camera: ${enabled ? "ON" : "OFF"}`);
        setText("camera-power-icon", enabled ? "videocam" : "videocam_off");
        const button = byId("camera-power-toggle");
        if (button) {
            const camera = selectedCamera();
            button.disabled = !camera || ["DISABLED", "UNCONFIGURED"].includes(camera.state);
            button.classList.toggle("opacity-40", button.disabled);
        }
    }

    function selectCamera(slot) {
        if (!CAMERA_IDS[slot]) return;
        state.selectedSlot = slot;
        try {
            window.localStorage.setItem(SELECTED_CAMERA_KEY, slot);
        } catch (_error) {
            // Backend power state still preserves the feed when storage is unavailable.
        }
        updateSelectorDots();
        updateStatusCard();
        updatePowerButton();
        renderUseCases();
        showSelectedFeed();
        refreshCameraControls(selectedCameraId());
        refreshRestrictedZone(selectedCameraId());
        refreshIncidents();
    }

    async function refreshCameras() {
        if (state.polling) return;
        state.polling = true;
        try {
            const response = await fetch("/api/cameras", { cache: "no-store" });
            if (!response.ok) throw new Error(`Camera status ${response.status}`);
            const payload = await response.json();
            const previousFeed = state.feedEnabled[state.selectedSlot];
            const previousCameraState = selectedCamera()?.state;
            state.cameras = Object.fromEntries(
                payload.cameras.map((camera) => [camera.camera_id, camera])
            );
            payload.cameras.forEach((camera) => {
                const slot = SLOT_BY_ID[camera.camera_id];
                if (slot) state.feedEnabled[slot] = Boolean(camera.power_on);
            });
            updateSelectorDots();
            updateStatusCard();
            updatePowerButton();
            updateCameraControls();
            if (
                previousFeed !== state.feedEnabled[state.selectedSlot]
                || previousCameraState !== selectedCamera()?.state
            ) showSelectedFeed();
        } catch (error) {
            setText("camera-status-message", `Dashboard connection error: ${error.message}`);
            setText("camera-operating-status", "OFFLINE");
        } finally {
            state.polling = false;
        }
    }

    async function refreshModels() {
        try {
            state.modelStatus = await getJson("/api/models/status");
            updateStatusCard();
        } catch (_error) {
            state.modelStatus = null;
        }
    }

    async function refreshAlarm() {
        try {
            state.alarmStatus = await getJson("/api/alarm/status");
        } catch (_error) {
            state.alarmStatus = null;
        }
        updateAlarmCard();
    }

    function renderRecentIncidents(incidents) {
        const container = byId("recent-events-list");
        if (!container) return;
        if (!incidents.length) {
            container.innerHTML = `
                <div class="p-4 rounded-xl bg-surface-container-low border border-outline-variant/30 text-center">
                    <span class="material-symbols-outlined text-green-400 text-[28px]">verified</span>
                    <p class="text-[12px] text-on-surface-variant mt-2">No incidents recorded for this camera.</p>
                </div>`;
            return;
        }
        container.innerHTML = incidents.map((incident) => {
            const unsafe = incident.unsafe_at !== null;
            const color = unsafe ? "text-red-400 bg-red-500/10" : "text-yellow-400 bg-yellow-500/10";
            const label = unsafe ? "UNSAFE" : "WARNING";
            const time = new Date(incident.opened_at * 1000).toLocaleString();
            const clipAction = incident.clip_path ? `
                <span class="inline-flex items-center gap-1 mt-2 text-[10px] font-bold text-primary group-hover:underline">
                    <span class="material-symbols-outlined text-[14px]">play_circle</span>
                    Open 10s Clip
                </span>` : `
                <span class="inline-flex items-center gap-1 mt-2 text-[10px] font-bold text-on-surface-variant group-hover:text-primary">
                    <span class="material-symbols-outlined text-[14px]">visibility</span>
                    View Details
                </span>`;
            return `
                <a class="group block p-3 rounded-xl bg-surface-container-low border border-outline-variant/30 hover:border-primary/50 hover:bg-surface-container-high transition-colors"
                   href="/event.html?incident_id=${encodeURIComponent(incident.id)}">
                    <div class="flex justify-between items-start gap-3">
                        <div>
                            <p class="text-[12px] font-bold text-on-surface">${incident.detector}</p>
                            <p class="text-[10px] text-outline mt-1">${time}</p>
                        </div>
                        <span class="px-2 py-1 rounded text-[9px] font-bold ${color}">${label}</span>
                    </div>
                    ${clipAction}
                </a>`;
        }).join("");
    }

    async function refreshIncidents() {
        try {
            const response = await fetch(
                `/api/dashboard/summary?camera_id=${encodeURIComponent(selectedCameraId())}&recent_limit=5`,
                { cache: "no-store" }
            );
            if (!response.ok) throw new Error(`Incident summary ${response.status}`);
            const summary = await response.json();
            setText("stat-total-events", summary.total_events);
            setText("stat-safe-pct", summary.safe_pct === null ? "—" : `${summary.safe_pct}%`);
            setText("stat-warnings", summary.warnings);
            setText("stat-unsafe", summary.unsafe);
            renderRecentIncidents(summary.recent_incidents);
        } catch (_error) {
            setText("stat-total-events", "—");
        }
    }

    async function manualRefreshIncidents() {
        const button = byId("recent-events-refresh");
        const icon = button?.querySelector(".material-symbols-outlined");
        if (button) button.disabled = true;
        icon?.classList.add("animate-spin");
        try {
            await refreshIncidents();
        } finally {
            icon?.classList.remove("animate-spin");
            if (button) button.disabled = false;
        }
    }

    async function setSelectedCameraPower(on) {
        const cameraId = selectedCameraId();
        const button = byId("camera-power-toggle");
        if (button) button.disabled = true;
        try {
            const result = await getJson(
                `/api/cameras/${encodeURIComponent(cameraId)}/power`,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ on }),
                }
            );
            state.cameras[cameraId] = result.camera;
            state.feedEnabled[state.selectedSlot] = result.power_on;
            if (!result.power_on) hideFeeds();
            updateStatusCard();
            updatePowerButton();
            updateCameraControls();
            showSelectedFeed();
            await refreshRestrictedZone(cameraId);
        } catch (error) {
            window.alert(`Could not turn ${cameraId} ${on ? "on" : "off"}.\n\n${error.message}`);
        } finally {
            updatePowerButton();
            updateCameraControls();
        }
    }

    async function refreshRestrictedZone(cameraId = selectedCameraId()) {
        try {
            const encoded = encodeURIComponent(cameraId);
            const [settings, zone, detectorStatus, proximity, proximityStatus, ppe, ppeStatus] = await Promise.all([
                getJson(`/api/cameras/${encoded}/detectors/restricted-zone`),
                getJson(`/api/cameras/${encoded}/restricted-zone?preset=HOME`),
                getJson(`/api/cameras/${encoded}/detectors/restricted-zone/status`),
                getJson(`/api/cameras/${encoded}/detectors/unsafe-proximity`),
                getJson(`/api/cameras/${encoded}/detectors/unsafe-proximity/status`),
                getJson(`/api/cameras/${encoded}/detectors/ppe`),
                getJson(`/api/cameras/${encoded}/detectors/ppe/status`),
            ]);
            state.restrictedZone[cameraId] = { settings, zone };
            state.restrictedStatus[cameraId] = detectorStatus;
            state.proximity[cameraId] = { settings: proximity };
            state.proximityStatus[cameraId] = proximityStatus;
            state.ppe[cameraId] = { settings: ppe };
            state.ppeStatus[cameraId] = ppeStatus;
            if (cameraId === selectedCameraId()) {
                renderUseCases();
                updateStatusCard();
            }
        } catch (error) {
            if (cameraId === selectedCameraId()) {
                setText("ai-monitoring-detail", `Restricted Zone status unavailable: ${error.message}`);
            }
        }
    }

    async function openConfigModal() {
        const cameraId = selectedCameraId();
        const modal = byId("camera-config-modal");
        setText("modal-title", `Configure ${cameraId}`);
        modal?.classList.remove("hidden");
        try {
            await refreshRestrictedZone(cameraId);
            const current = state.restrictedZone[cameraId];
            const proximity = proximitySettings(cameraId);
            const ppe = ppeSettings(cameraId);
            state.configSnapshot = structuredClone({
                restricted: current.settings,
                proximity,
                ppe,
            });
            byId("toggle-restrictedZone").checked = current.settings.enabled;
            byId("toggle-unsafeProximity").checked = proximity.enabled;
            byId("toggle-ppeDetection").checked = ppe.enabled;
            byId("toggle-zoneOverlay").checked =
                current.settings.overlay_enabled && proximity.overlay_enabled && ppe.overlay_enabled;
            setText(
                "restricted-zone-summary",
                current.zone.points.length
                    ? `HOME preset • ${current.zone.points.length} polygon points`
                    : "No polygon configured for HOME preset"
            );
        } catch (error) {
            setText("restricted-zone-summary", error.message);
        }
    }

    function closeConfigModal() {
        byId("camera-config-modal")?.classList.add("hidden");
        state.configSnapshot = null;
    }

    async function saveConfig() {
        const cameraId = selectedCameraId();
        const button = byId("save-btn");
        button.disabled = true;
        try {
            const encoded = encodeURIComponent(cameraId);
            const overlayEnabled = byId("toggle-zoneOverlay").checked;
            const [settings, proximity, ppe] = await Promise.all([
                getJson(`/api/cameras/${encoded}/detectors/restricted-zone`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        enabled: byId("toggle-restrictedZone").checked,
                        overlay_enabled: overlayEnabled,
                    }),
                }),
                getJson(`/api/cameras/${encoded}/detectors/unsafe-proximity`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        enabled: byId("toggle-unsafeProximity").checked,
                        overlay_enabled: overlayEnabled,
                    }),
                }),
                getJson(`/api/cameras/${encoded}/detectors/ppe`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        enabled: byId("toggle-ppeDetection").checked,
                        overlay_enabled: overlayEnabled,
                    }),
                }),
            ]);
            const current = state.restrictedZone[cameraId] || { zone: { points: [] } };
            state.restrictedZone[cameraId] = { ...current, settings };
            state.proximity[cameraId] = { settings: proximity };
            state.ppe[cameraId] = { settings: ppe };
            await refreshRestrictedZone(cameraId);
            closeConfigModal();
        } catch (error) {
            window.alert(`Could not save AI detector settings.\n\n${error.message}`);
        } finally {
            button.disabled = false;
        }
    }

    function resizeZoneCanvas() {
        const canvas = byId("zone-editor-canvas");
        const stage = byId("zone-editor-stage");
        if (!canvas || !stage) return;
        const rect = stage.getBoundingClientRect();
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.max(1, Math.round(rect.width * ratio));
        canvas.height = Math.max(1, Math.round(rect.height * ratio));
        canvas.style.width = `${rect.width}px`;
        canvas.style.height = `${rect.height}px`;
        drawZoneEditor();
    }

    function drawZoneEditor() {
        const canvas = byId("zone-editor-canvas");
        if (!canvas) return;
        const context = canvas.getContext("2d");
        const width = canvas.width;
        const height = canvas.height;
        context.clearRect(0, 0, width, height);
        const points = state.zonePoints.map(([x, y]) => [x * width, y * height]);
        if (points.length) {
            context.beginPath();
            context.moveTo(...points[0]);
            points.slice(1).forEach((point) => context.lineTo(...point));
            if (points.length >= 3) context.closePath();
            context.fillStyle = "rgba(239, 68, 68, 0.22)";
            context.strokeStyle = "#ef4444";
            context.lineWidth = Math.max(2, (window.devicePixelRatio || 1) * 2);
            if (points.length >= 3) context.fill();
            context.stroke();
            points.forEach(([x, y], index) => {
                context.beginPath();
                context.arc(x, y, 7 * (window.devicePixelRatio || 1), 0, Math.PI * 2);
                context.fillStyle = "#ffffff";
                context.fill();
                context.stroke();
                context.fillStyle = "#111827";
                context.font = `bold ${10 * (window.devicePixelRatio || 1)}px sans-serif`;
                context.fillText(String(index + 1), x + 10, y - 10);
            });
        }
        const ready = state.zonePoints.length >= 3;
        byId("zone-save-btn").disabled = !ready;
        setText("zone-editor-status", ready
            ? `${state.zonePoints.length} points ready to save for HOME preset.`
            : `${state.zonePoints.length} point(s) — at least 3 are required.`);
    }

    function normalizedPointer(event) {
        const canvas = byId("zone-editor-canvas");
        const rect = canvas.getBoundingClientRect();
        return [
            Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
            Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
        ];
    }

    function nearestZonePoint(point) {
        let nearest = -1;
        let distance = 0.035;
        state.zonePoints.forEach((candidate, index) => {
            const current = Math.hypot(candidate[0] - point[0], candidate[1] - point[1]);
            if (current < distance) {
                nearest = index;
                distance = current;
            }
        });
        return nearest;
    }

    async function openZoneEditor() {
        const cameraId = selectedCameraId();
        try {
            await refreshRestrictedZone(cameraId);
            state.zonePoints = structuredClone(state.restrictedZone[cameraId].zone.points || []);
            const modal = byId("zone-editor-modal");
            const feed = byId("zone-editor-feed");
            modal.dataset.cameraId = cameraId;
            modal.classList.remove("hidden");
            modal.classList.add("flex");
            feed.classList.remove("opacity-0");
            feed.onload = () => {
                feed.classList.remove("opacity-0");
                window.requestAnimationFrame(resizeZoneCanvas);
            };
            feed.onerror = () => {
                feed.classList.add("opacity-0");
                setText(
                    "zone-editor-status",
                    "Live camera feed unavailable. Start the camera feed and try again."
                );
            };
            feed.src = `/api/cameras/${encodeURIComponent(cameraId)}/raw-stream?t=${Date.now()}`;
            window.requestAnimationFrame(resizeZoneCanvas);
        } catch (error) {
            window.alert(`Could not open the Restricted Zone editor.\n\n${error.message}`);
        }
    }

    function closeZoneEditor() {
        const modal = byId("zone-editor-modal");
        modal?.classList.add("hidden");
        modal?.classList.remove("flex");
        const feed = byId("zone-editor-feed");
        if (feed) {
            feed.onload = null;
            feed.onerror = null;
            feed.removeAttribute("src");
        }
        state.draggedZonePoint = -1;
    }

    async function saveZone() {
        const cameraId = byId("zone-editor-modal")?.dataset.cameraId || selectedCameraId();
        const button = byId("zone-save-btn");
        button.disabled = true;
        try {
            const zone = await getJson(
                `/api/cameras/${encodeURIComponent(cameraId)}/restricted-zone?preset=HOME`,
                {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ points: state.zonePoints }),
                }
            );
            const current = state.restrictedZone[cameraId] || { settings: restrictedSettings(cameraId) };
            state.restrictedZone[cameraId] = { ...current, zone };
            setText("restricted-zone-summary", `HOME preset • ${zone.points.length} polygon points`);
            closeZoneEditor();
        } catch (error) {
            window.alert(`Could not save the restricted area.\n\n${error.message}`);
            drawZoneEditor();
        }
    }

    function storeZoomLevels() {
        try {
            window.localStorage.setItem(ZOOM_LEVELS_KEY, JSON.stringify(state.zoomLevels));
        } catch (_error) {
            // Zoom still works for the current page when storage is unavailable.
        }
    }

    function applyDigitalZoom() {
        const level = Number(state.zoomLevels[selectedCameraId()] || 1);
        ["pose-stream-img", "tapo-stream-img"].forEach((id) => {
            const image = byId(id);
            if (image) {
                image.style.transform = `scale(${level})`;
                image.style.transformOrigin = "center center";
            }
        });
        setText("current-zoom-text", `${level.toFixed(2).replace(/0$/, "")}x`);
        setText("overlay-zoom-text", `ZOOM: ${level.toFixed(2).replace(/0$/, "")}x`);
    }

    function setControlDisabled(element, disabled, title = "") {
        if (!element) return;
        element.disabled = disabled;
        element.classList.toggle("opacity-30", disabled);
        element.classList.toggle("cursor-not-allowed", disabled);
        element.title = title;
    }

    function updateCameraControls() {
        const cameraId = selectedCameraId();
        const capability = state.controls[cameraId] || {};
        const cameraOn = Boolean(selectedCamera()?.power_on);
        const zoom = Number(state.zoomLevels[cameraId] || 1);
        const zoomAvailable = Boolean(capability.digital_zoom && cameraOn);
        setControlDisabled(byId("zoom-out"), !zoomAvailable || zoom <= (capability.min_zoom || 1),
            cameraOn ? "Zoom out" : "Turn the camera on first");
        setControlDisabled(byId("zoom-in"), !zoomAvailable || zoom >= (capability.max_zoom || 3),
            cameraOn ? "Zoom in" : "Turn the camera on first");

        const ptzAvailable = Boolean(capability.pan_tilt && capability.configured && cameraOn && !state.controlBusy);
        all(".ptz-control").forEach((button) => setControlDisabled(
            button,
            !ptzAvailable,
            cameraId === "CAM 02" ? "CAM 02 supports zoom only" :
                (!capability.configured ? "Start CAM 01 with scripts/setup_tapo.ps1" : "Move Tapo camera")
        ));
        const presetAvailable = Boolean(capability.presets && capability.configured && cameraOn && !state.controlBusy);
        all(".preset-btn").forEach((button) => {
            setControlDisabled(button, !presetAvailable,
                cameraId === "CAM 02" ? "Presets are only available on CAM 01" : "Open saved position");
            const saved = Boolean(state.presets[cameraId]?.[button.dataset.preset]);
            button.classList.toggle("border-primary", saved);
            button.classList.toggle("text-primary", saved);
        });
        setControlDisabled(byId("save-preset-mode"), !presetAvailable,
            "Save the current Tapo direction into P1, P2, or P3");
        byId("save-preset-mode")?.classList.toggle("bg-primary/20", state.presetSaveMode);
        byId("save-preset-mode")?.classList.toggle("text-primary", state.presetSaveMode);
        setControlDisabled(byId("patrol-toggle"), true, "Auto Patrol will be added after manual PTZ validation");
        byId("joystick-container")?.classList.toggle("opacity-40", !ptzAvailable);
        setText("camera-control-status",
            cameraId === "CAM 02"
                ? "CAM 02: digital zoom only."
                : !capability.configured
                    ? "CAM 01: start with setup_tapo.ps1 to enable pan, tilt and presets."
                    : state.presetSaveMode
                        ? "Save mode: choose P1, P2 or P3."
                        : "CAM 01: digital zoom + physical pan/tilt. Presets with a blue border are saved."
        );
        applyDigitalZoom();
    }

    async function refreshCameraControls(cameraId = selectedCameraId()) {
        try {
            const capability = await getJson(`/api/cameras/${encodeURIComponent(cameraId)}/controls`);
            state.controls[cameraId] = capability;
            if (capability.presets && capability.configured) {
                try {
                    const response = await getJson(`/api/cameras/${encodeURIComponent(cameraId)}/presets`);
                    state.presets[cameraId] = response.presets || {};
                } catch (error) {
                    capability.last_error = error.message;
                }
            }
        } catch (error) {
            state.controls[cameraId] = { digital_zoom: true, pan_tilt: false, presets: false, last_error: error.message };
        }
        if (cameraId === selectedCameraId()) updateCameraControls();
    }

    function changeZoom(delta) {
        const cameraId = selectedCameraId();
        const capability = state.controls[cameraId] || { min_zoom: 1, max_zoom: 3 };
        const value = Number(state.zoomLevels[cameraId] || 1) + delta;
        state.zoomLevels[cameraId] = Math.max(capability.min_zoom || 1, Math.min(capability.max_zoom || 3, value));
        storeZoomLevels();
        updateCameraControls();
        const indicator = byId("zoom-indicator");
        indicator?.classList.remove("opacity-0");
        window.setTimeout(() => indicator?.classList.add("opacity-0"), 700);
    }

    async function runPtz(action) {
        state.controlBusy = true;
        updateCameraControls();
        try {
            await getJson(`/api/cameras/${encodeURIComponent(selectedCameraId())}/ptz`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: action.toLowerCase() }),
            });
        } catch (error) {
            window.alert(`Tapo control failed.\n\n${error.message}`);
        } finally {
            state.controlBusy = false;
            updateCameraControls();
        }
    }

    async function usePreset(slot) {
        const action = state.presetSaveMode ? "save" : "goto";
        state.controlBusy = true;
        updateCameraControls();
        try {
            await getJson(`/api/cameras/${encodeURIComponent(selectedCameraId())}/presets/${slot}`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action }),
            });
            if (action === "save") {
                state.presets[selectedCameraId()] = { ...(state.presets[selectedCameraId()] || {}), [slot]: true };
                state.presetSaveMode = false;
            }
        } catch (error) {
            window.alert(`Tapo preset failed.\n\n${error.message}`);
        } finally {
            state.controlBusy = false;
            updateCameraControls();
        }
    }

    function initializePlaceholders() {
        setText("stat-total-events", "—");
        setText("stat-safe-pct", "—");
        setText("stat-warnings", "—");
        setText("stat-unsafe", "—");
        const events = byId("recent-events-list");
        if (events) events.innerHTML = `
            <div class="p-4 rounded-xl bg-surface-container-low border border-outline-variant/30 text-center">
                <span class="material-symbols-outlined text-outline text-[28px]">database</span>
                <p class="text-[12px] text-on-surface-variant mt-2">Event database will be connected in the next backend stage.</p>
            </div>`;
    }

    window.cancelConfigModal = closeConfigModal;
    window.resetToDefaults = () => {
        byId("toggle-restrictedZone").checked = false;
        byId("toggle-unsafeProximity").checked = false;
        byId("toggle-ppeDetection").checked = false;
        byId("toggle-zoneOverlay").checked = true;
    };
    window.saveConfig = saveConfig;

    document.addEventListener("DOMContentLoaded", () => {
        all(".cam-selector").forEach((selector) => {
            selector.addEventListener("click", () => selectCamera(selector.dataset.cam));
        });
        byId("camera-power-toggle")?.addEventListener("click", () => {
            setSelectedCameraPower(!state.feedEnabled[state.selectedSlot]);
        });
        byId("recent-events-refresh")?.addEventListener("click", manualRefreshIncidents);
        byId("zoom-in")?.addEventListener("click", () => changeZoom(0.25));
        byId("zoom-out")?.addEventListener("click", () => changeZoom(-0.25));
        all(".ptz-control").forEach((button) => {
            button.addEventListener("click", () => runPtz(button.dataset.dir));
        });
        byId("save-preset-mode")?.addEventListener("click", () => {
            state.presetSaveMode = !state.presetSaveMode;
            updateCameraControls();
        });
        all(".preset-btn").forEach((button) => {
            button.addEventListener("click", () => usePreset(button.dataset.preset));
        });
        byId("fullscreen-toggle")?.addEventListener("click", async () => {
            const container = byId("video-feed-container");
            if (!document.fullscreenElement) await container?.requestFullscreen();
            else await document.exitFullscreen();
        });
        byId("usecase-configure-link")?.addEventListener("click", openConfigModal);
        byId("edit-restricted-zone-btn")?.addEventListener("click", openZoneEditor);
        byId("zone-editor-close")?.addEventListener("click", closeZoneEditor);
        byId("zone-editor-backdrop")?.addEventListener("click", closeZoneEditor);
        byId("zone-cancel-btn")?.addEventListener("click", closeZoneEditor);
        byId("zone-undo-btn")?.addEventListener("click", () => {
            state.zonePoints.pop();
            drawZoneEditor();
        });
        byId("zone-clear-btn")?.addEventListener("click", () => {
            state.zonePoints = [];
            drawZoneEditor();
        });
        byId("zone-save-btn")?.addEventListener("click", saveZone);

        const zoneCanvas = byId("zone-editor-canvas");
        zoneCanvas?.addEventListener("pointerdown", (event) => {
            const point = normalizedPointer(event);
            state.draggedZonePoint = nearestZonePoint(point);
            if (state.draggedZonePoint < 0) {
                state.zonePoints.push(point);
                state.draggedZonePoint = state.zonePoints.length - 1;
            }
            zoneCanvas.setPointerCapture(event.pointerId);
            drawZoneEditor();
        });
        zoneCanvas?.addEventListener("pointermove", (event) => {
            if (state.draggedZonePoint < 0) return;
            state.zonePoints[state.draggedZonePoint] = normalizedPointer(event);
            drawZoneEditor();
        });
        const stopDragging = () => { state.draggedZonePoint = -1; };
        zoneCanvas?.addEventListener("pointerup", stopDragging);
        zoneCanvas?.addEventListener("pointercancel", stopDragging);
        window.addEventListener("resize", () => {
            if (!byId("zone-editor-modal")?.classList.contains("hidden")) resizeZoneCanvas();
        });

        initializePlaceholders();
        renderUseCases();
        selectCamera(state.selectedSlot);
        refreshCameras();
        refreshModels();
        refreshAlarm();
        refreshIncidents();
        window.setInterval(refreshCameras, 2000);
        window.setInterval(refreshModels, 30000);
        window.setInterval(refreshAlarm, 2000);
        window.setInterval(refreshIncidents, 5000);
        window.setInterval(() => refreshRestrictedZone(selectedCameraId()), 5000);
    });
})();
