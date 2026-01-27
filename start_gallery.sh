#!/bin/bash
# Smart Asset Gallery Launcher Script

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source venv/bin/activate

# Configure paths
export BASE_OUTPUT_PATH="/mnt/wsl/PHYSICALDRIVE0p2/home/dchevalier/Documents/Projects/ComfyUI/output"
export BASE_INPUT_PATH="/mnt/wsl/PHYSICALDRIVE0p2/home/dchevalier/Documents/Projects/ComfyUI/input"
export FFPROBE_MANUAL_PATH="/usr/bin/ffprobe"
export SERVER_PORT=8189

# Social media posting features (optional)
# export SOCIAL_FEATURES_ENABLED="true"
# export FB_APP_ID=""
# export FB_APP_SECRET=""
# export LINKEDIN_CLIENT_ID=""
# export LINKEDIN_CLIENT_SECRET=""
# export TOKEN_ENCRYPTION_KEY=""

# SharePoint document library as gallery source (optional)
# export SHAREPOINT_TENANT_ID=""
# export SHAREPOINT_CLIENT_ID=""
# export SHAREPOINT_CLIENT_SECRET=""
# export SHAREPOINT_SITE_URL=""
# export SHAREPOINT_LIBRARY_NAME="Documents"
# export SHAREPOINT_SYNC_INTERVAL="300"

# Launch Smart Asset Gallery
echo "Starting Smart Asset Gallery..."
echo "Access URL: http://127.0.0.1:8189/galleryout/"
python smartgallery.py
