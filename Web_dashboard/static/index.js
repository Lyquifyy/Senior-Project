const socket = io();
const co2History  = [];
const waitHistory = [];
const MAX_HISTORY = 30;

// ── Camera tabs — built dynamically from what's actually on disk ─────
(() => {
    const tabsEl  = document.getElementById('cameraTabs');
    const hintEl  = document.getElementById('cameraHint');
    const imgEl   = document.getElementById('cameraFeed');
    if (!tabsEl || !imgEl) return;

    let currentCams = [];
    let activeCam   = null;

    const prettyLabel = cam => {
        // e.g. "nw" → "NW", "south" → "South", "east_2" → "East 2"
        const [base, suffix] = cam.split('_');
        const core = base.length <= 2 ? base.toUpperCase()
                                      : base.charAt(0).toUpperCase() + base.slice(1);
        return suffix ? `${core} ${suffix}` : core;
    };

    const rebuildTabs = cams => {
        if (hintEl && hintEl.parentNode === tabsEl) tabsEl.removeChild(hintEl);
        tabsEl.innerHTML = '';
        cams.forEach(cam => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'camera-tab';
            btn.dataset.cam = cam;
            const pretty = prettyLabel(cam);
            btn.textContent = pretty;
            btn.setAttribute('aria-label', `Show ${pretty} camera feed`);
            btn.setAttribute('aria-pressed', cam === activeCam ? 'true' : 'false');
            btn.addEventListener('click', () => selectCam(cam));
            tabsEl.appendChild(btn);
        });
    };

    const selectCam = cam => {
        activeCam = cam;
        tabsEl.querySelectorAll('.camera-tab').forEach(t => {
            const on = t.dataset.cam === cam;
            t.classList.toggle('active', on);
            t.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        // Cache-bust so the browser drops the old MJPEG connection.
        imgEl.src = `/camera/${cam}?t=${Date.now()}`;
        imgEl.alt = `Live CARLA camera feed — ${prettyLabel(cam)} approach`;
    };

    const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

    const poll = async () => {
        try {
            const r = await fetch('/cameras', { cache: 'no-store' });
            if (!r.ok) return;
            const data = await r.json();
            const cams = (data.on_disk || []).slice().sort();
            if (!same(cams, currentCams)) {
                currentCams = cams;
                if (!activeCam || !cams.includes(activeCam)) activeCam = cams[0] || null;
                rebuildTabs(cams);
                if (activeCam) selectCam(activeCam);
            }
        } catch (_) { /* server transient — just retry next tick */ }
    };

    // Poll aggressively at first (cameras appear seconds after co-sim starts),
    // then slow down once we have tabs.
    poll();
    const id = setInterval(() => {
        poll();
        if (currentCams.length > 0) {
            clearInterval(id);
            // Slower steady-state poll in case cameras drop/reappear.
            setInterval(poll, 5000);
        }
    }, 1000);
})();

// ── Connection ──────────────────────────────────────
socket.on('connect', () => {
    const el = document.getElementById('statusText');
    el.textContent = 'Connected';
    el.className = 'value online';
});

socket.on('disconnect', () => {
    const el = document.getElementById('statusText');
    el.textContent = 'Disconnected';
    el.className = 'value offline';
});

// ── Simulation Update ───────────────────────────────
socket.on('simulation_update', (data) => {
    // Sidebar metrics — values come from the server already rounded to
    // whole numbers; just display them.
    document.getElementById('simStep').textContent       = data.step;
    document.getElementById('co2Value').textContent      = Math.round(data.co2);
    document.getElementById('waitValue').textContent     = Math.round(data.avg_wait_time);
    document.getElementById('totalVehicles').textContent = data.total_vehicles;

    // Vehicle counters with bump animation
    if (data.cars) {
        Object.entries(data.cars).forEach(([dir, count]) => {
            const id = `count${dir.charAt(0).toUpperCase() + dir.slice(1)}`;
            bumpCounter(id, count);
        });
    }

    // Traffic lights
    if (data.lights) {
        Object.entries(data.lights).forEach(([dir, color]) => {
            const id = `light${dir.charAt(0).toUpperCase() + dir.slice(1)}`;
            updateLightUI(id, color);
        });
    }

    // Charts
    pushHistory(co2History,  data.co2);
    pushHistory(waitHistory, data.avg_wait_time);
    drawChart('co2Canvas',  co2History,  '#3b82f6', 'rgba(59,130,246,0.15)');
    drawChart('waitCanvas', waitHistory, '#f59e0b', 'rgba(245,158,11,0.15)');
});

// ── Helpers ─────────────────────────────────────────
function pushHistory(arr, val) {
    arr.push(val);
    if (arr.length > MAX_HISTORY) arr.shift();
}

function bumpCounter(id, newVal) {
    const el = document.getElementById(id);
    if (!el) return;
    const prev = parseInt(el.textContent, 10);
    el.textContent = newVal;
    // Keep the accessible name in sync with the visible number so screen
    // reader users navigating to the counter hear the current count without
    // flooding them with every tick's update.
    const direction = id.replace('count', '').toLowerCase();
    el.setAttribute('aria-label',
        `${newVal} vehicle${newVal === 1 ? '' : 's'} at ${direction} approach`);
    if (newVal !== prev) {
        el.classList.remove('bump');
        void el.offsetWidth; // force reflow to restart transition
        el.classList.add('bump');
        setTimeout(() => el.classList.remove('bump'), 150);
    }
}

function updateLightUI(containerId, activeColor) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.querySelectorAll('.light').forEach(l => l.classList.remove('active'));
    const target = container.querySelector(`.${activeColor.toLowerCase()}`);
    if (target) target.classList.add('active');
    // Update the accessible name so signal state is conveyed non-visually.
    const direction = containerId.replace('light', '').toLowerCase();
    container.setAttribute('aria-label',
        `${direction.charAt(0).toUpperCase() + direction.slice(1)} signal: ${activeColor}`);
}

// ── Chart Drawing ────────────────────────────────────
function drawChart(canvasId, history, lineColor, fillColor) {
    if (history.length < 2) return;

    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const W = canvas.width  = canvas.offsetWidth;
    const H = canvas.height = canvas.offsetHeight;

    const PAD_L = 42, PAD_R = 10, PAD_T = 12, PAD_B = 24;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;

    ctx.clearRect(0, 0, W, H);

    const minVal = Math.min(...history);
    const maxVal = Math.max(...history);
    const range  = maxVal - minVal || 1;

    const toX = i => PAD_L + (i / (history.length - 1)) * chartW;
    const toY = v => PAD_T + chartH - ((v - minVal) / range) * chartH;

    // Grid lines + Y labels
    ctx.textAlign = 'right';
    ctx.font = '10px Segoe UI, sans-serif';
    ctx.fillStyle = '#4b5563';

    const TICKS = 4;
    for (let t = 0; t <= TICKS; t++) {
        const v = minVal + (range * t) / TICKS;
        const y = toY(v);

        ctx.strokeStyle = '#1f2335';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(PAD_L, y);
        ctx.lineTo(W - PAD_R, y);
        ctx.stroke();

        ctx.fillStyle = '#9ba1b3';
        ctx.fillText(Math.round(v).toString(), PAD_L - 4, y + 4);
    }

    // Filled area under line
    const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + chartH);
    grad.addColorStop(0, fillColor);
    grad.addColorStop(1, 'transparent');

    ctx.beginPath();
    history.forEach((val, i) => {
        const x = toX(i), y = toY(val);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.lineTo(toX(history.length - 1), PAD_T + chartH);
    ctx.lineTo(PAD_L, PAD_T + chartH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    history.forEach((val, i) => {
        const x = toX(i), y = toY(val);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Latest value dot
    const lastX = toX(history.length - 1);
    const lastY = toY(history[history.length - 1]);
    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, 2 * Math.PI);
    ctx.fillStyle = lineColor;
    ctx.fill();
}
