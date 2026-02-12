const socket = io();
let co2History = [];
const MAX_HISTORY = 30;

socket.on('connect', () => {
    const status = document.getElementById('statusText');
    status.textContent = 'Connected';
    status.className = 'value online';
});

socket.on('disconnect', () => {
    const status = document.getElementById('statusText');
    status.textContent = 'Disconnected';
    status.className = 'value offline';
});

socket.on('simulation_update', (data) => {
    //Update Sidebar Metrics
    document.getElementById('simStep').textContent = data.step;
    document.getElementById('co2Value').textContent = data.co2.toFixed(2);
    document.getElementById('waitValue').textContent = data.avg_wait_time.toFixed(1);
    document.getElementById('totalVehicles').textContent = data.total_vehicles;

    //Update Car Counts (North, South, East, West)
    if (data.cars) {
        Object.entries(data.cars).forEach(([dir, count]) => {
            const id = `count${dir.charAt(0).toUpperCase() + dir.slice(1)}`;
            const el = document.getElementById(id);
            if (el) el.textContent = count;
        });
    }

    //Update Lights
    if (data.lights) {
        Object.entries(data.lights).forEach(([dir, color]) => {
            const id = `light${dir.charAt(0).toUpperCase() + dir.slice(1)}`;
            updateLightUI(id, color);
        });
    }

    //Update Chart
    updateChart(data.co2);
});

function updateLightUI(containerId, activeColor) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    const lights = container.querySelectorAll('.light');
    lights.forEach(l => l.classList.remove('active'));
    
    const target = container.querySelector(`.${activeColor.toLowerCase()}`);
    if (target) target.classList.add('active');
}

function updateChart(newVal) {
    co2History.push(newVal);
    if (co2History.length > MAX_HISTORY) co2History.shift();
    
    const canvas = document.getElementById('co2Canvas');
    const ctx = canvas.getContext('2d');
    const w = canvas.width = canvas.offsetWidth;
    const h = canvas.height = canvas.offsetHeight;
    
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = '#3b82f6';
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    co2History.forEach((val, i) => {
        const x = (i / (co2History.length - 1)) * w;
        const y = h - ((val / (Math.max(...co2History) || 1)) * h * 0.8) - 10;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();
}