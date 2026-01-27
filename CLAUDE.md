# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Smart Asset Gallery is a web-based asset management, viewer, and social media posting platform for Community Action Lehigh Valley's marketing team. It's a Flask-based Python application with SQLite for caching and parallel processing for performance. Originally based on SmartGallery for ComfyUI.

## Common Commands

```bash
# Setup virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run the application
python smartgallery.py

# Run with environment variables (Linux/Mac)
export BASE_OUTPUT_PATH="/path/to/assets"
export BASE_INPUT_PATH="/path/to/source/media"
python smartgallery.py
```

Access the gallery at `http://127.0.0.1:8189/galleryout/`

## Architecture

### Main Backend
The core backend is in `smartgallery.py` (~2900 lines). Key sections:
- Configuration via environment variables with fallback defaults
- Helper functions for metadata extraction and analysis
- Core utilities (file processing, thumbnails, database, folder scanning)
- Flask routes for the REST API
- Startup banner, update checking, and main entry point

### Social Media Module (`social/`)
A Flask Blueprint (`/galleryout/social/*`) with:
- `__init__.py`: Blueprint factory, feature toggle
- `auth.py`: flask-login setup, User class, password hashing, decorators
- `models.py`: Table creation, schema migration (5 tables: users, social_accounts, posts, post_media, post_platforms)
- `oauth.py`: OAuth2 flows for Facebook/Instagram/LinkedIn, token encryption
- `posting.py`: Per-platform publish functions (Graph API, LinkedIn API)
- `scheduler.py`: APScheduler for scheduled posts + token refresh
- `routes.py`: All social feature Flask routes
- `sharepoint.py`: SharePoint document library integration via Microsoft Graph API

### Key Components
- **Metadata Extraction**: Extracts workflow data from PNG/JPG/WebP metadata and MP4 video tags (requires ffprobe)
- **Node Summary**: Parses workflows to display node parameters with color-coded categories
- **Parallel Processing**: Uses `concurrent.futures.ProcessPoolExecutor` for thumbnail generation and file scanning
- **Folder Management**: Dynamic folder discovery with SQLite-cached file metadata
- **Social Posting**: Submit/approve/publish workflow for Facebook, Instagram, LinkedIn
- **SharePoint Integration**: Sync files from SharePoint document libraries as gallery sources

### Database
- SQLite database at `{BASE_SMARTGALLERY_PATH}/.sqlite_cache/gallery_cache.sqlite`
- Schema version tracked via `DB_SCHEMA_VERSION` (currently 26)
- Main table: `files` with columns: id, path, mtime, name, type, duration, dimensions, has_workflow, is_favorite, size, last_scanned
- Social tables: `users`, `social_accounts`, `posts`, `post_media`, `post_platforms` (independent schema versioning)

### Frontend
- Main gallery: `templates/index.html` (contains embedded CSS and JavaScript)
- Social templates: `templates/social/` (login, compose, dashboard, settings)
- Smash Cut generator: `templates/smashcut.html`
- Static files in `static/galleryout/` (favicon)

## Configuration

All settings are configured via environment variables with fallback values in the code:
- `BASE_OUTPUT_PATH`: Main media/assets folder (required)
- `BASE_INPUT_PATH`: Source media input folder
- `BASE_SMARTGALLERY_PATH`: Location for cache/database (defaults to output path)
- `FFPROBE_MANUAL_PATH`: Path to ffprobe executable for video metadata extraction
- `SERVER_PORT`: Web server port (default: 8189)
- `MAX_PARALLEL_WORKERS`: CPU cores for parallel processing (empty = all cores)
- `DELETE_TO`: Optional trash folder path (empty = permanent delete)
- `SOCIAL_FEATURES_ENABLED`: Enable social media posting (default: true)
- `FB_APP_ID` / `FB_APP_SECRET`: Meta (Facebook/Instagram) OAuth credentials
- `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET`: LinkedIn OAuth credentials
- `SHAREPOINT_TENANT_ID` / `SHAREPOINT_CLIENT_ID` / `SHAREPOINT_CLIENT_SECRET`: SharePoint integration
- `SHAREPOINT_SITE_URL` / `SHAREPOINT_LIBRARY_NAME`: SharePoint document library to sync

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

Social routes (all prefixed with `/galleryout/social/`):
- `GET/POST /login`, `/logout`, `/setup`: Authentication
- `GET /dashboard`: Post queue management
- `GET /compose`: Post compose/edit form
- `POST /posts`: Create/update post
- `POST /posts/<id>/submit|approve|reject|publish`: Workflow actions
- `GET /settings`: Connected accounts + user management (admin)
- `GET /oauth/<platform>/authorize|callback`: OAuth flows
- `GET /sharepoint/status|folders|files`: SharePoint browsing
- `POST /sharepoint/sync`: Trigger SharePoint sync
