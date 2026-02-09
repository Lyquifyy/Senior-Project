# Use official SUMO image (Ubuntu-based, includes SUMO binaries + TraCI/sumolib)
ARG SUMO_TAG=latest
FROM ghcr.io/eclipse-sumo/sumo:${SUMO_TAG}

# Install Python 3 and pip if not present, plus any extras
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Ensure SUMO_HOME is set (official image usually sets it; override if needed)
ENV SUMO_HOME=${SUMO_HOME:-/usr/share/sumo}
# Add SUMO tools to Python path for traci and sumolib
ENV PYTHONPATH="${SUMO_HOME}/tools:${PYTHONPATH}"

# Install Python dependencies from project (CARLA client + Flask)
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Default to project workspace
WORKDIR /workspaces/Senior-Project
