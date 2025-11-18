async function fetchDashboardData() {
    try {
        const res = await fetch("/get");
        const data = await res.json();

        // Metrics
        document.getElementById("co2Value").textContent = data.co2.toFixed(1);
        document.getElementById("waitValue").textContent = data.avg_wait_time.toFixed(1);

        // Car counts
        document.getElementById("countNorth").textContent = data.cars.north;
        document.getElementById("countSouth").textContent = data.cars.south;
        document.getElementById("countEast").textContent = data.cars.east;
        document.getElementById("countWest").textContent = data.cars.west;

        // Traffic lights
        updateLight("North", data.lights.north);
        updateLight("South", data.lights.south);
        updateLight("East", data.lights.east);
        updateLight("West", data.lights.west);

    } catch (err) {
        console.error("Error fetching data:", err);
    }
}

function updateLight(direction, color) {
    const light = document.getElementById(`light${direction}`);
    light.classList.remove("red", "green", "yellow");
    light.classList.add(color.toLowerCase());
}

setInterval(fetchDashboardData, 2000);
fetchDashboardData();

 
 