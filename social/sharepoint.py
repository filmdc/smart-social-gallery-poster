"""
SharePoint document library integration for Smart Asset Gallery.

Allows fetching images/videos from SharePoint Online document libraries
(and their sub-folders) to use as gallery asset sources.

Uses Microsoft Graph API via MSAL (Microsoft Authentication Library) for
app-only authentication.

Environment variables:
    SHAREPOINT_TENANT_ID       - Azure AD tenant ID
    SHAREPOINT_CLIENT_ID       - Azure AD app registration client ID
    SHAREPOINT_CLIENT_SECRET   - Azure AD app registration client secret
    SHAREPOINT_SITE_URL        - SharePoint site URL (e.g., https://contoso.sharepoint.com/sites/marketing)
    SHAREPOINT_LIBRARY_NAME    - Document library name (default: "Documents")
    SHAREPOINT_SYNC_INTERVAL   - Seconds between background syncs (default: 300)
    SHAREPOINT_LOCAL_CACHE_DIR - Local directory for cached files (default: {BASE_SMARTGALLERY_PATH}/.sharepoint_cache)
"""

import os
import time
import threading
import hashlib
import mimetypes
import logging

import requests

logger = logging.getLogger(__name__)

# Configuration from environment
SHAREPOINT_TENANT_ID = os.environ.get('SHAREPOINT_TENANT_ID', '')
SHAREPOINT_CLIENT_ID = os.environ.get('SHAREPOINT_CLIENT_ID', '')
SHAREPOINT_CLIENT_SECRET = os.environ.get('SHAREPOINT_CLIENT_SECRET', '')
SHAREPOINT_SITE_URL = os.environ.get('SHAREPOINT_SITE_URL', '')
SHAREPOINT_LIBRARY_NAME = os.environ.get('SHAREPOINT_LIBRARY_NAME', 'Documents')
SHAREPOINT_SYNC_INTERVAL = int(os.environ.get('SHAREPOINT_SYNC_INTERVAL', '300'))

# Supported media extensions (aligned with Smart Asset Gallery)
MEDIA_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff', '.tif',
    '.mp4', '.mov', '.avi', '.mkv', '.webm',
    '.mp3', '.wav', '.ogg', '.flac',
}

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

_access_token = None
_token_expires_at = 0
_sync_thread = None
_stop_event = threading.Event()


def sharepoint_available():
    """Check if all required SharePoint credentials are configured."""
    return bool(SHAREPOINT_TENANT_ID and SHAREPOINT_CLIENT_ID and
                SHAREPOINT_CLIENT_SECRET and SHAREPOINT_SITE_URL)


def _get_access_token():
    """Get or refresh an app-only access token via MSAL client credentials flow."""
    global _access_token, _token_expires_at

    if _access_token and time.time() < _token_expires_at - 60:
        return _access_token

    try:
        import msal
        authority = f'https://login.microsoftonline.com/{SHAREPOINT_TENANT_ID}'
        app = msal.ConfidentialClientApplication(
            SHAREPOINT_CLIENT_ID,
            authority=authority,
            client_credential=SHAREPOINT_CLIENT_SECRET,
        )
        result = app.acquire_token_for_client(scopes=['https://graph.microsoft.com/.default'])

        if 'access_token' in result:
            _access_token = result['access_token']
            _token_expires_at = time.time() + result.get('expires_in', 3600)
            return _access_token
        else:
            error = result.get('error_description', result.get('error', 'Unknown error'))
            logger.error(f"SharePoint auth failed: {error}")
            return None
    except ImportError:
        logger.error("msal package not installed. Run: pip install msal")
        return None
    except Exception as e:
        logger.error(f"SharePoint auth error: {e}")
        return None


def _get_site_id():
    """Resolve the SharePoint site URL to a Graph site ID."""
    token = _get_access_token()
    if not token:
        return None

    # Parse site URL: https://tenant.sharepoint.com/sites/sitename
    from urllib.parse import urlparse
    parsed = urlparse(SHAREPOINT_SITE_URL)
    hostname = parsed.hostname  # e.g. contoso.sharepoint.com
    site_path = parsed.path.rstrip('/')  # e.g. /sites/marketing

    resp = requests.get(
        f'{GRAPH_BASE}/sites/{hostname}:{site_path}',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    if resp.ok:
        return resp.json().get('id')
    else:
        logger.error(f"Failed to resolve SharePoint site: {resp.status_code} {resp.text}")
        return None


def _get_drive_id(site_id):
    """Get the drive ID for the specified document library."""
    token = _get_access_token()
    if not token or not site_id:
        return None

    resp = requests.get(
        f'{GRAPH_BASE}/sites/{site_id}/drives',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    if not resp.ok:
        logger.error(f"Failed to list drives: {resp.status_code} {resp.text}")
        return None

    drives = resp.json().get('value', [])
    for drive in drives:
        if drive.get('name', '').lower() == SHAREPOINT_LIBRARY_NAME.lower():
            return drive['id']

    # Fallback: return the first drive
    if drives:
        logger.warning(f"Library '{SHAREPOINT_LIBRARY_NAME}' not found. Using '{drives[0].get('name')}'")
        return drives[0]['id']

    return None


def list_sharepoint_files(folder_path='', recursive=True):
    """
    List all media files in a SharePoint document library folder.

    Args:
        folder_path: Sub-folder path within the library ('' for root)
        recursive: Whether to recurse into sub-folders

    Returns:
        List of dicts with keys: name, path, size, mtime, download_url, sp_item_id
    """
    token = _get_access_token()
    if not token:
        return []

    site_id = _get_site_id()
    drive_id = _get_drive_id(site_id)
    if not drive_id:
        return []

    files = []
    _list_folder(drive_id, folder_path, files, recursive, token)
    return files


def _list_folder(drive_id, folder_path, files_list, recursive, token):
    """Recursively list media files in a drive folder."""
    if folder_path:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children'
    else:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root/children'

    while url:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            params={'$top': 200},
            timeout=30,
        )
        if not resp.ok:
            logger.error(f"Failed to list folder '{folder_path}': {resp.status_code}")
            break

        data = resp.json()
        for item in data.get('value', []):
            if 'folder' in item and recursive:
                # Recurse into sub-folder
                child_path = f"{folder_path}/{item['name']}" if folder_path else item['name']
                _list_folder(drive_id, child_path, files_list, recursive, token)
            elif 'file' in item:
                ext = os.path.splitext(item['name'])[1].lower()
                if ext in MEDIA_EXTENSIONS:
                    sp_path = f"{folder_path}/{item['name']}" if folder_path else item['name']
                    files_list.append({
                        'name': item['name'],
                        'path': sp_path,
                        'size': item.get('size', 0),
                        'mtime': item.get('lastModifiedDateTime', ''),
                        'download_url': item.get('@microsoft.graph.downloadUrl', ''),
                        'sp_item_id': item['id'],
                        'drive_id': drive_id,
                    })

        # Handle pagination
        url = data.get('@odata.nextLink')


def download_sharepoint_file(drive_id, item_id, local_path):
    """
    Download a single file from SharePoint to a local cache path.

    Args:
        drive_id: The drive ID
        item_id: The SharePoint item ID
        local_path: Full local file path to save to

    Returns:
        True if downloaded successfully, False otherwise
    """
    token = _get_access_token()
    if not token:
        return False

    try:
        # Get download URL
        resp = requests.get(
            f'{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content',
            headers={'Authorization': f'Bearer {token}'},
            allow_redirects=False,
            timeout=15,
        )

        # Graph API returns a 302 redirect to the actual download URL
        if resp.status_code in (301, 302):
            download_url = resp.headers.get('Location')
        elif resp.ok:
            # Direct content
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'wb') as f:
                f.write(resp.content)
            return True
        else:
            logger.error(f"Failed to get download URL for {item_id}: {resp.status_code}")
            return False

        # Download from redirect URL
        if download_url:
            dl_resp = requests.get(download_url, stream=True, timeout=300)
            if dl_resp.ok:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, 'wb') as f:
                    for chunk in dl_resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            else:
                logger.error(f"Download failed for {item_id}: {dl_resp.status_code}")

    except Exception as e:
        logger.error(f"Error downloading {item_id}: {e}")

    return False


def _file_exists_by_sp_item_id(db_path, sp_item_id):
    """Check if a file with this SharePoint item ID exists in the database."""
    if not db_path:
        return False, None
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, path FROM files WHERE sp_item_id = ?",
            (sp_item_id,)
        ).fetchone()
        conn.close()
        if row:
            return True, row['path']
        return False, None
    except Exception as e:
        logger.warning(f"Error checking for existing file: {e}")
        return False, None


def _record_sharepoint_origin(db_path, local_path, sp_file):
    """Record SharePoint origin metadata for a synced file."""
    if not db_path:
        return
    try:
        import sqlite3
        file_id = hashlib.md5(local_path.encode()).hexdigest()
        now = time.time()
        conn = sqlite3.connect(db_path)
        conn.execute("""
            UPDATE files SET
                source_type = 'sharepoint',
                sp_item_id = ?,
                sp_drive_id = ?,
                sp_original_path = ?,
                sp_sync_timestamp = ?,
                original_path = COALESCE(original_path, path)
            WHERE id = ?
        """, (sp_file['sp_item_id'], sp_file['drive_id'], sp_file['path'], now, file_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Error recording SharePoint origin: {e}")


def sync_sharepoint_to_local(cache_dir, db_path=None):
    """
    Sync SharePoint document library files to a local cache directory.
    DEPRECATED: Use sync_configured_folders() for selective folder sync.

    Downloads new/updated files and returns the list of local file paths.
    If a file with the same SharePoint item ID already exists in the database
    (even if moved to a different location), it will NOT be re-downloaded.

    Args:
        cache_dir: Local directory for cached SharePoint files
        db_path: Optional database path for recording sync metadata

    Returns:
        List of local file paths that were synced
    """
    if not sharepoint_available():
        return []

    os.makedirs(cache_dir, exist_ok=True)
    sp_files = list_sharepoint_files()
    synced = []
    skipped_moved = 0

    for sp_file in sp_files:
        # Check if this SharePoint file already exists in the database (possibly moved)
        exists, existing_path = _file_exists_by_sp_item_id(db_path, sp_file['sp_item_id'])
        if exists:
            # File was already synced and possibly moved - don't re-download
            if existing_path and os.path.exists(existing_path):
                synced.append(existing_path)
                skipped_moved += 1
                continue
            # File record exists but physical file is gone - allow re-download
            # This handles the case where a user deleted the local file

        # Build local path preserving folder structure
        local_path = os.path.join(cache_dir, sp_file['path'].replace('/', os.sep))
        local_dir = os.path.dirname(local_path)

        # Check if file needs updating at the cache location
        needs_download = True
        if os.path.exists(local_path):
            local_size = os.path.getsize(local_path)
            if local_size == sp_file['size']:
                needs_download = False

        if needs_download:
            os.makedirs(local_dir, exist_ok=True)
            success = download_sharepoint_file(
                sp_file['drive_id'],
                sp_file['sp_item_id'],
                local_path
            )
            if success:
                synced.append(local_path)
                # Record origin metadata after next database scan picks up the file
                # We'll schedule this to happen after the file is in the database
                _pending_origin_records[local_path] = sp_file
                logger.info(f"Synced: {sp_file['path']}")
        else:
            synced.append(local_path)
            # Ensure origin metadata is recorded for existing cache files
            _pending_origin_records[local_path] = sp_file

    if skipped_moved > 0:
        logger.info(f"Skipped {skipped_moved} files already synced and moved")

    return synced


def sync_folder_to_local(sp_folder_path, local_folder_path, include_subfolders=True, db_path=None):
    """
    Sync a specific SharePoint folder to a local directory.

    Args:
        sp_folder_path: SharePoint folder path within the document library
        local_folder_path: Local directory to sync files into
        include_subfolders: Whether to include subfolders
        db_path: Optional database path for recording sync metadata

    Returns:
        Tuple of (synced_count, list of local file paths)
    """
    if not sharepoint_available():
        return 0, []

    os.makedirs(local_folder_path, exist_ok=True)
    sp_files = list_sharepoint_files(folder_path=sp_folder_path, recursive=include_subfolders)
    synced = []
    skipped_moved = 0

    for sp_file in sp_files:
        # Check if this SharePoint file already exists in the database (possibly moved)
        exists, existing_path = _file_exists_by_sp_item_id(db_path, sp_file['sp_item_id'])
        if exists:
            if existing_path and os.path.exists(existing_path):
                synced.append(existing_path)
                skipped_moved += 1
                continue

        # Build local path - remove the sp_folder_path prefix to get relative path
        sp_relative_path = sp_file['path']
        if sp_folder_path and sp_relative_path.startswith(sp_folder_path + '/'):
            sp_relative_path = sp_relative_path[len(sp_folder_path) + 1:]
        elif sp_folder_path and sp_relative_path == sp_folder_path:
            sp_relative_path = os.path.basename(sp_file['path'])

        local_path = os.path.join(local_folder_path, sp_relative_path.replace('/', os.sep))
        local_dir = os.path.dirname(local_path)

        # Check if file needs updating
        needs_download = True
        if os.path.exists(local_path):
            local_size = os.path.getsize(local_path)
            if local_size == sp_file['size']:
                needs_download = False

        if needs_download:
            os.makedirs(local_dir, exist_ok=True)
            success = download_sharepoint_file(
                sp_file['drive_id'],
                sp_file['sp_item_id'],
                local_path
            )
            if success:
                synced.append(local_path)
                _pending_origin_records[local_path] = sp_file
                logger.info(f"Synced: {sp_file['name']} -> {local_path}")
        else:
            synced.append(local_path)
            _pending_origin_records[local_path] = sp_file

    if skipped_moved > 0:
        logger.info(f"Skipped {skipped_moved} files already synced and moved")

    return len(synced), synced


def sync_configured_folders(base_output_path, social_db_path, gallery_db_path=None):
    """
    Sync all configured SharePoint folders to the main gallery.

    Args:
        base_output_path: The base gallery output path (files sync directly here)
        social_db_path: Path to social database with sync configurations
        gallery_db_path: Path to gallery database for file origin tracking

    Returns:
        Dict with sync results per folder
    """
    import sqlite3
    import time

    if not sharepoint_available():
        return {'error': 'SharePoint not configured'}

    try:
        conn = sqlite3.connect(social_db_path)
        conn.row_factory = sqlite3.Row
        folders = conn.execute(
            "SELECT * FROM sharepoint_sync_folders WHERE is_enabled = 1"
        ).fetchall()
    except Exception as e:
        logger.error(f"Error reading sync configurations: {e}")
        return {'error': str(e)}

    results = {}
    for folder in folders:
        folder_id = folder['id']
        sp_path = folder['sp_folder_path']
        local_name = folder['local_folder_name']
        include_subs = bool(folder['include_subfolders'])

        # Sync to a folder directly in the main gallery
        local_folder = os.path.join(base_output_path, local_name)

        try:
            count, files = sync_folder_to_local(
                sp_folder_path=sp_path,
                local_folder_path=local_folder,
                include_subfolders=include_subs,
                db_path=gallery_db_path
            )

            # Update last sync time
            conn.execute(
                "UPDATE sharepoint_sync_folders SET last_sync_at = ?, last_sync_count = ? WHERE id = ?",
                (time.time(), count, folder_id)
            )
            conn.commit()

            results[local_name] = {'synced': count, 'success': True}
            logger.info(f"Synced {count} files from '{sp_path}' to '{local_name}'")

        except Exception as e:
            logger.error(f"Error syncing folder '{sp_path}': {e}")
            results[local_name] = {'synced': 0, 'success': False, 'error': str(e)}

    conn.close()

    # Apply pending origin records
    if gallery_db_path:
        apply_pending_origin_records(gallery_db_path)

    return results


# Pending origin records to be applied after database scan
_pending_origin_records = {}


def apply_pending_origin_records(db_path):
    """Apply pending SharePoint origin records to files now in the database."""
    global _pending_origin_records
    if not _pending_origin_records:
        return

    applied = 0
    for local_path, sp_file in list(_pending_origin_records.items()):
        _record_sharepoint_origin(db_path, local_path, sp_file)
        applied += 1
        del _pending_origin_records[local_path]

    if applied:
        logger.info(f"Applied {applied} SharePoint origin records")


def list_sharepoint_folders():
    """
    List all sub-folders in the SharePoint document library.

    Returns:
        List of dicts with keys: name, path, item_count
    """
    token = _get_access_token()
    if not token:
        return []

    site_id = _get_site_id()
    drive_id = _get_drive_id(site_id)
    if not drive_id:
        return []

    folders = []
    _list_subfolders(drive_id, '', folders, token)
    return folders


def _list_subfolders(drive_id, folder_path, folders_list, token):
    """Recursively list sub-folders."""
    if folder_path:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children'
    else:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root/children'

    resp = requests.get(
        url,
        headers={'Authorization': f'Bearer {token}'},
        params={'$top': 200, '$filter': "folder ne null"},
        timeout=15,
    )
    if not resp.ok:
        return

    for item in resp.json().get('value', []):
        if 'folder' in item:
            child_path = f"{folder_path}/{item['name']}" if folder_path else item['name']
            folders_list.append({
                'name': item['name'],
                'path': child_path,
                'item_count': item['folder'].get('childCount', 0),
            })
            _list_subfolders(drive_id, child_path, folders_list, token)


def list_folder_children(folder_path=''):
    """
    List immediate children folders of a given folder path (non-recursive).

    This is used for lazy-loading the folder tree in the UI.

    Args:
        folder_path: Path to the parent folder, empty string for root

    Returns:
        List of dicts with keys: name, path, has_children, child_count
    """
    token = _get_access_token()
    if not token:
        return []

    site_id = _get_site_id()
    drive_id = _get_drive_id(site_id)
    if not drive_id:
        return []

    if folder_path:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children'
    else:
        url = f'{GRAPH_BASE}/drives/{drive_id}/root/children'

    resp = requests.get(
        url,
        headers={'Authorization': f'Bearer {token}'},
        params={'$top': 200, '$filter': "folder ne null"},
        timeout=15,
    )
    if not resp.ok:
        logger.warning(f"Failed to list folder children: {resp.status_code} - {resp.text}")
        return []

    folders = []
    for item in resp.json().get('value', []):
        if 'folder' in item:
            child_path = f"{folder_path}/{item['name']}" if folder_path else item['name']
            child_count = item['folder'].get('childCount', 0)
            folders.append({
                'name': item['name'],
                'path': child_path,
                'child_count': child_count,
                'has_children': child_count > 0,  # Indicates if folder can be expanded
            })

    return sorted(folders, key=lambda f: f['name'].lower())


def start_background_sync(base_output_path, interval=None, gallery_db_path=None, social_db_path=None):
    """Start a background thread that periodically syncs configured SharePoint folders.

    Args:
        base_output_path: Base gallery output path for synced files
        interval: Sync interval in seconds (default: SHAREPOINT_SYNC_INTERVAL)
        gallery_db_path: Database path for tracking file origins
        social_db_path: Database path for sync configurations
    """
    global _sync_thread
    if not sharepoint_available():
        return

    if _sync_thread and _sync_thread.is_alive():
        return  # Already running

    _stop_event.clear()
    sync_interval = interval or SHAREPOINT_SYNC_INTERVAL

    def sync_loop():
        while not _stop_event.is_set():
            try:
                if social_db_path:
                    # Use new selective folder sync
                    sync_configured_folders(base_output_path, social_db_path, gallery_db_path)
                else:
                    # Fallback to legacy cache-based sync
                    cache_dir = os.path.join(base_output_path, '.sharepoint_cache')
                    sync_sharepoint_to_local(cache_dir, db_path=gallery_db_path)
                    if gallery_db_path:
                        apply_pending_origin_records(gallery_db_path)
            except Exception as e:
                logger.error(f"SharePoint sync error: {e}")
            _stop_event.wait(sync_interval)

    _sync_thread = threading.Thread(target=sync_loop, daemon=True, name='sharepoint-sync')
    _sync_thread.start()
    logger.info(f"SharePoint background sync started (interval: {sync_interval}s)")


def stop_background_sync():
    """Stop the background sync thread."""
    _stop_event.set()
