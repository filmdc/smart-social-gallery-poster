# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SmartGallery is a standalone web gallery for ComfyUI that links generated images/videos to their original workflows. It's a Flask-based Python application with SQLite for caching and parallel processing for performance.

## Common Commands

```bash
# Setup virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# OR: call venv\Scripts\activate.bat  # Windows

# Install dependencies
pip install -r requirements.txt

# Run the application
python smartgallery.py

# Run with environment variables (Linux/Mac)
export BASE_OUTPUT_PATH="/path/to/ComfyUI/output"
export BASE_INPUT_PATH="/path/to/ComfyUI/input"
python smartgallery.py
```

Access the gallery at `http://127.0.0.1:8189/galleryout/`

## Architecture

### Single-File Backend
The entire backend is in `smartgallery.py` (~1770 lines). Key sections:
- **Lines 44-243**: Configuration via environment variables with fallback defaults
- **Lines 260-496**: Helper functions for workflow extraction and node analysis
- **Lines 500-1056**: Core utilities (file processing, thumbnails, database, folder scanning)
- **Lines 1059-1639**: Flask routes for the REST API
- **Lines 1641-1767**: Startup banner, update checking, and main entry point

### Key Components
- **Workflow Extraction**: Extracts ComfyUI workflows from PNG/JPG/WebP metadata and MP4 video tags (requires ffprobe)
- **Node Summary**: Parses workflows to display node parameters with color-coded categories
- **Parallel Processing**: Uses `concurrent.futures.ProcessPoolExecutor` for thumbnail generation and file scanning
- **Folder Management**: Dynamic folder discovery with SQLite-cached file metadata

### Database
- SQLite database at `{BASE_SMARTGALLERY_PATH}/.sqlite_cache/gallery_cache.sqlite`
- Schema version tracked via `DB_SCHEMA_VERSION` (currently 24)
- Main table: `files` with columns: id, path, mtime, name, type, duration, dimensions, has_workflow, is_favorite, size, last_scanned

### Frontend
- Single Jinja2 template: `templates/index.html` (contains embedded CSS and JavaScript)
- Static files in `static/galleryout/` (favicon)

## Configuration

All settings are configured via environment variables with fallback values in the code:
- `BASE_OUTPUT_PATH`: ComfyUI output folder (required)
- `BASE_INPUT_PATH`: ComfyUI input folder (for source media in node summary)
- `BASE_SMARTGALLERY_PATH`: Location for cache/database (defaults to output path)
- `FFPROBE_MANUAL_PATH`: Path to ffprobe executable for video workflow extraction
- `SERVER_PORT`: Web server port (default: 8189)
- `MAX_PARALLEL_WORKERS`: CPU cores for parallel processing (empty = all cores)
- `DELETE_TO`: Optional trash folder path (empty = permanent delete)

## Docker Support

```bash
# Using docker compose
docker compose up -d

# Using Makefile
make build
make run
```

Pre-built images available at `mmartial/smart-comfyui-gallery` on DockerHub.

## API Endpoints

Key routes (all prefixed with `/galleryout/`):
- `GET /view/<folder_key>`: Main gallery view
- `GET /file/<file_id>`: Serve original file
- `GET /thumbnail/<file_id>`: Serve thumbnail
- `GET /workflow/<file_id>`: Download workflow JSON
- `GET /node_summary/<file_id>`: Get parsed workflow summary
- `POST /delete/<file_id>`: Delete file
- `POST /move_batch`: Move multiple files
- `POST /delete_batch`: Delete multiple files
- `GET /sync_status/<folder_key>`: SSE endpoint for folder sync progress
