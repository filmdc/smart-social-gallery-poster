#!/bin/bash
# SmartGallery Launcher Script

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source venv/bin/activate

# Configure paths
export BASE_OUTPUT_PATH="/home/dchevalier/Documents/Projects/ComfyUI/output"
export BASE_INPUT_PATH="/home/dchevalier/Documents/Projects/ComfyUI/input"
export FFPROBE_MANUAL_PATH="/usr/bin/ffprobe"
export SERVER_PORT=8189

# Launch SmartGallery
echo "Starting SmartGallery..."
echo "Access URL: http://127.0.0.1:8189/galleryout/"
python smartgallery.py
