"""
Maintenance module for Smart Asset Gallery.

Handles cleanup of:
- ZIP download cache
- Smashcut output files
- Orphaned thumbnails
- SharePoint cache
- SQLite WAL files and VACUUM operations

Can be triggered on startup via STARTUP_MAINTENANCE=true environment variable
for system recovery after disk space issues.
"""

import os
import shutil
import time
import logging
import sqlite3
import hashlib
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration from environment
STARTUP_MAINTENANCE = os.environ.get('STARTUP_MAINTENANCE', 'false').lower() == 'true'
AGGRESSIVE_CLEANUP = os.environ.get('AGGRESSIVE_CLEANUP', 'false').lower() == 'true'

# Default retention periods (in seconds)
ZIP_RETENTION_HOURS = int(os.environ.get('ZIP_RETENTION_HOURS', '24'))
SMASHCUT_RETENTION_HOURS = int(os.environ.get('SMASHCUT_RETENTION_HOURS', '168'))  # 7 days
THUMBNAIL_ORPHAN_CHECK_DAYS = int(os.environ.get('THUMBNAIL_ORPHAN_CHECK_DAYS', '7'))

# Storage alert thresholds (percentage of disk used)
STORAGE_WARNING_THRESHOLD = int(os.environ.get('STORAGE_WARNING_THRESHOLD', '80'))
STORAGE_CRITICAL_THRESHOLD = int(os.environ.get('STORAGE_CRITICAL_THRESHOLD', '90'))
STORAGE_EMERGENCY_THRESHOLD = int(os.environ.get('STORAGE_EMERGENCY_THRESHOLD', '95'))

# Track maintenance state
_maintenance_lock = threading.Lock()
_maintenance_running = False
_last_maintenance_run = 0


def get_cache_dirs(base_smartgallery_path):
    """Get all cache directory paths."""
    return {
        'thumbnails': os.path.join(base_smartgallery_path, '.thumbnails_cache'),
        'zip': os.path.join(base_smartgallery_path, '.zip_downloads'),
        'smashcut': os.path.join(base_smartgallery_path, '.smashcut_output'),
        'sharepoint': os.path.join(base_smartgallery_path, '.sharepoint_cache'),
        'sqlite': os.path.join(base_smartgallery_path, '.sqlite_cache'),
    }


def get_volume_disk_space(path):
    """
    Get disk space information for the volume containing the given path.

    Args:
        path: Any path on the volume to check

    Returns:
        dict with 'total_bytes', 'used_bytes', 'free_bytes', 'percent_used'
    """
    try:
        # Use shutil.disk_usage which works cross-platform
        usage = shutil.disk_usage(path)
        total = usage.total
        free = usage.free
        used = usage.used

        return {
            'total_bytes': total,
            'used_bytes': used,
            'free_bytes': free,
            'total_gb': total / (1024 ** 3),
            'used_gb': used / (1024 ** 3),
            'free_gb': free / (1024 ** 3),
            'percent_used': (used / total * 100) if total > 0 else 0,
            'percent_free': (free / total * 100) if total > 0 else 0,
        }
    except OSError as e:
        logger.error(f"Error getting disk usage for {path}: {e}")
        return {
            'total_bytes': 0,
            'used_bytes': 0,
            'free_bytes': 0,
            'total_gb': 0,
            'used_gb': 0,
            'free_gb': 0,
            'percent_used': 0,
            'percent_free': 0,
            'error': str(e)
        }


def get_storage_health(base_smartgallery_path):
    """
    Get storage health status with alerts based on thresholds.

    Args:
        base_smartgallery_path: Base path to check volume for

    Returns:
        dict with 'status', 'level', 'message', 'percent_used', 'recommendations'
    """
    disk_info = get_volume_disk_space(base_smartgallery_path)

    if disk_info.get('error'):
        return {
            'status': 'unknown',
            'level': 'error',
            'message': f"Cannot determine disk status: {disk_info['error']}",
            'percent_used': 0,
            'recommendations': ['Check disk accessibility']
        }

    percent_used = disk_info['percent_used']
    free_gb = disk_info['free_gb']

    if percent_used >= STORAGE_EMERGENCY_THRESHOLD:
        return {
            'status': 'emergency',
            'level': 'emergency',
            'message': f"EMERGENCY: Disk {percent_used:.1f}% full! Only {free_gb:.1f}GB free. Immediate action required.",
            'percent_used': percent_used,
            'free_gb': free_gb,
            'recommendations': [
                'Run aggressive maintenance immediately',
                'Delete unnecessary files',
                'Consider expanding storage',
                'Clear smashcut output files',
                'Remove old ZIP downloads'
            ]
        }
    elif percent_used >= STORAGE_CRITICAL_THRESHOLD:
        return {
            'status': 'critical',
            'level': 'critical',
            'message': f"CRITICAL: Disk {percent_used:.1f}% full. Only {free_gb:.1f}GB remaining.",
            'percent_used': percent_used,
            'free_gb': free_gb,
            'recommendations': [
                'Run maintenance cleanup',
                'Review and delete old smashcut videos',
                'Clear ZIP download cache',
                'Consider expanding storage soon'
            ]
        }
    elif percent_used >= STORAGE_WARNING_THRESHOLD:
        return {
            'status': 'warning',
            'level': 'warning',
            'message': f"Warning: Disk {percent_used:.1f}% full. {free_gb:.1f}GB remaining.",
            'percent_used': percent_used,
            'free_gb': free_gb,
            'recommendations': [
                'Schedule maintenance cleanup',
                'Monitor storage growth',
                'Consider reducing retention periods'
            ]
        }
    else:
        return {
            'status': 'healthy',
            'level': 'ok',
            'message': f"Storage healthy: {percent_used:.1f}% used, {free_gb:.1f}GB free.",
            'percent_used': percent_used,
            'free_gb': free_gb,
            'recommendations': []
        }


def check_storage_and_auto_cleanup(base_smartgallery_path, database_file):
    """
    Check storage levels and trigger automatic cleanup if thresholds exceeded.

    This is called periodically by the scheduler to prevent disk full situations.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the database file

    Returns:
        dict with 'action_taken', 'health_before', 'health_after', 'cleanup_results'
    """
    health = get_storage_health(base_smartgallery_path)
    result = {
        'action_taken': None,
        'health_before': health,
        'health_after': None,
        'cleanup_results': None
    }

    if health['status'] == 'emergency':
        logger.warning(f"EMERGENCY storage level detected: {health['percent_used']:.1f}%")
        logger.info("Triggering automatic aggressive cleanup...")
        result['action_taken'] = 'aggressive_cleanup'
        result['cleanup_results'] = run_all_maintenance(base_smartgallery_path, database_file, aggressive=True)
        result['health_after'] = get_storage_health(base_smartgallery_path)

    elif health['status'] == 'critical':
        logger.warning(f"Critical storage level detected: {health['percent_used']:.1f}%")
        logger.info("Triggering automatic cleanup...")
        result['action_taken'] = 'normal_cleanup'
        result['cleanup_results'] = run_all_maintenance(base_smartgallery_path, database_file, aggressive=False)
        result['health_after'] = get_storage_health(base_smartgallery_path)

    return result


def cleanup_zip_cache(zip_cache_dir, max_age_hours=None):
    """
    Clean up old ZIP files from the download cache.

    Args:
        zip_cache_dir: Path to the ZIP cache directory
        max_age_hours: Maximum age in hours (default: ZIP_RETENTION_HOURS)

    Returns:
        dict with 'deleted_count', 'freed_bytes', 'errors'
    """
    if max_age_hours is None:
        max_age_hours = ZIP_RETENTION_HOURS

    result = {'deleted_count': 0, 'freed_bytes': 0, 'errors': []}

    if not os.path.exists(zip_cache_dir):
        return result

    max_age_seconds = max_age_hours * 3600
    now = time.time()

    try:
        for filename in os.listdir(zip_cache_dir):
            filepath = os.path.join(zip_cache_dir, filename)
            if not os.path.isfile(filepath):
                continue

            # Only clean .zip files
            if not filename.endswith('.zip'):
                continue

            try:
                file_mtime = os.path.getmtime(filepath)
                file_age = now - file_mtime

                if file_age > max_age_seconds:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    result['deleted_count'] += 1
                    result['freed_bytes'] += file_size
                    logger.info(f"Deleted old ZIP: {filename} (age: {file_age/3600:.1f}h, size: {file_size/1024/1024:.1f}MB)")
            except OSError as e:
                result['errors'].append(f"Error deleting {filename}: {e}")
                logger.warning(f"Failed to delete ZIP {filename}: {e}")
    except OSError as e:
        result['errors'].append(f"Error reading ZIP cache directory: {e}")
        logger.error(f"Error reading ZIP cache directory: {e}")

    if result['deleted_count'] > 0:
        logger.info(f"ZIP cleanup: deleted {result['deleted_count']} files, freed {result['freed_bytes']/1024/1024:.1f}MB")

    return result


def cleanup_smashcut_cache(smashcut_dir, max_age_hours=None):
    """
    Clean up old smashcut output files.

    Args:
        smashcut_dir: Path to the smashcut output directory
        max_age_hours: Maximum age in hours (default: SMASHCUT_RETENTION_HOURS)

    Returns:
        dict with 'deleted_count', 'freed_bytes', 'errors'
    """
    if max_age_hours is None:
        max_age_hours = SMASHCUT_RETENTION_HOURS

    result = {'deleted_count': 0, 'freed_bytes': 0, 'errors': []}

    if not os.path.exists(smashcut_dir):
        return result

    max_age_seconds = max_age_hours * 3600
    now = time.time()

    try:
        for filename in os.listdir(smashcut_dir):
            filepath = os.path.join(smashcut_dir, filename)
            if not os.path.isfile(filepath):
                continue

            # Clean .mp4 files and temporary .txt concat lists
            if not (filename.endswith('.mp4') or filename.endswith('.txt')):
                continue

            try:
                file_mtime = os.path.getmtime(filepath)
                file_age = now - file_mtime

                if file_age > max_age_seconds:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    result['deleted_count'] += 1
                    result['freed_bytes'] += file_size
                    logger.info(f"Deleted old smashcut file: {filename} (age: {file_age/3600:.1f}h, size: {file_size/1024/1024:.1f}MB)")
            except OSError as e:
                result['errors'].append(f"Error deleting {filename}: {e}")
                logger.warning(f"Failed to delete smashcut file {filename}: {e}")
    except OSError as e:
        result['errors'].append(f"Error reading smashcut directory: {e}")
        logger.error(f"Error reading smashcut directory: {e}")

    if result['deleted_count'] > 0:
        logger.info(f"Smashcut cleanup: deleted {result['deleted_count']} files, freed {result['freed_bytes']/1024/1024:.1f}MB")

    return result


def cleanup_orphaned_thumbnails(thumbnail_cache_dir, database_file):
    """
    Clean up thumbnail files for media that no longer exists in the database.

    This checks thumbnail filenames (which are MD5 hashes of path+mtime) against
    files that actually exist in the database.

    Args:
        thumbnail_cache_dir: Path to the thumbnail cache directory
        database_file: Path to the SQLite database

    Returns:
        dict with 'deleted_count', 'freed_bytes', 'errors'
    """
    result = {'deleted_count': 0, 'freed_bytes': 0, 'errors': []}

    if not os.path.exists(thumbnail_cache_dir):
        return result

    if not os.path.exists(database_file):
        return result

    try:
        # Get all valid thumbnail hashes from the database
        conn = sqlite3.connect(database_file, timeout=30)
        conn.row_factory = sqlite3.Row

        # Build set of valid thumbnail hashes
        valid_hashes = set()
        cursor = conn.execute("SELECT path, mtime FROM files")
        for row in cursor:
            if row['path'] and row['mtime']:
                hash_input = row['path'] + str(row['mtime'])
                thumb_hash = hashlib.md5(hash_input.encode()).hexdigest()
                valid_hashes.add(thumb_hash)

        conn.close()
        logger.info(f"Found {len(valid_hashes)} valid thumbnail hashes in database")

        # Check each thumbnail file
        for filename in os.listdir(thumbnail_cache_dir):
            filepath = os.path.join(thumbnail_cache_dir, filename)
            if not os.path.isfile(filepath):
                continue

            # Extract hash from filename (format: hash.ext or hash_animated.ext)
            base_name = os.path.splitext(filename)[0]
            if base_name.endswith('_animated'):
                base_name = base_name[:-9]  # Remove '_animated' suffix

            # Check if this hash corresponds to a valid file
            if base_name not in valid_hashes:
                try:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    result['deleted_count'] += 1
                    result['freed_bytes'] += file_size
                except OSError as e:
                    result['errors'].append(f"Error deleting {filename}: {e}")

        if result['deleted_count'] > 0:
            logger.info(f"Thumbnail cleanup: deleted {result['deleted_count']} orphaned thumbnails, freed {result['freed_bytes']/1024/1024:.1f}MB")

    except sqlite3.Error as e:
        result['errors'].append(f"Database error: {e}")
        logger.error(f"Database error during thumbnail cleanup: {e}")
    except OSError as e:
        result['errors'].append(f"Filesystem error: {e}")
        logger.error(f"Filesystem error during thumbnail cleanup: {e}")

    return result


def cleanup_sharepoint_cache(sharepoint_cache_dir, database_file, social_db_path=None):
    """
    Clean up SharePoint cache files that are no longer tracked or have been moved.

    Files synced from SharePoint are tracked via sp_item_id in the database.
    This cleans up cache files that:
    1. Don't correspond to any file in the database (orphaned)
    2. Have been moved to a different location (duplicate cache)

    Args:
        sharepoint_cache_dir: Path to the SharePoint cache directory
        database_file: Path to the gallery SQLite database
        social_db_path: Path to social database (may be same as database_file)

    Returns:
        dict with 'deleted_count', 'freed_bytes', 'errors'
    """
    result = {'deleted_count': 0, 'freed_bytes': 0, 'errors': []}

    if not os.path.exists(sharepoint_cache_dir):
        return result

    if not os.path.exists(database_file):
        return result

    try:
        conn = sqlite3.connect(database_file, timeout=30)
        conn.row_factory = sqlite3.Row

        # Get all tracked SharePoint files and their current locations
        tracked_paths = set()
        try:
            cursor = conn.execute("""
                SELECT path FROM files
                WHERE source_type = 'sharepoint' OR sp_item_id IS NOT NULL
            """)
            for row in cursor:
                if row['path']:
                    tracked_paths.add(os.path.normpath(row['path']))
        except sqlite3.OperationalError:
            # Column might not exist in older schemas
            pass

        conn.close()

        # Walk the SharePoint cache directory
        for root, dirs, files in os.walk(sharepoint_cache_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                normalized_path = os.path.normpath(filepath)

                # Skip if file is actively tracked at this location
                if normalized_path in tracked_paths:
                    continue

                # Check if file was moved elsewhere (tracked at different path)
                # In that case, the cache copy is redundant
                try:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    result['deleted_count'] += 1
                    result['freed_bytes'] += file_size
                    logger.info(f"Deleted orphaned SharePoint cache file: {filepath}")
                except OSError as e:
                    result['errors'].append(f"Error deleting {filepath}: {e}")

        # Clean up empty directories
        for root, dirs, files in os.walk(sharepoint_cache_dir, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        logger.debug(f"Removed empty directory: {dir_path}")
                except OSError:
                    pass

        if result['deleted_count'] > 0:
            logger.info(f"SharePoint cache cleanup: deleted {result['deleted_count']} files, freed {result['freed_bytes']/1024/1024:.1f}MB")

    except sqlite3.Error as e:
        result['errors'].append(f"Database error: {e}")
        logger.error(f"Database error during SharePoint cleanup: {e}")
    except OSError as e:
        result['errors'].append(f"Filesystem error: {e}")
        logger.error(f"Filesystem error during SharePoint cleanup: {e}")

    return result


def vacuum_database(database_file):
    """
    Run VACUUM on the SQLite database to reclaim space and optimize.
    Also checkpoints and truncates WAL file.

    Args:
        database_file: Path to the SQLite database

    Returns:
        dict with 'size_before', 'size_after', 'freed_bytes', 'errors'
    """
    result = {'size_before': 0, 'size_after': 0, 'freed_bytes': 0, 'wal_truncated': False, 'errors': []}

    if not os.path.exists(database_file):
        return result

    try:
        # Get size before
        result['size_before'] = os.path.getsize(database_file)

        # Also check WAL file size
        wal_file = database_file + '-wal'
        shm_file = database_file + '-shm'
        wal_size_before = 0
        if os.path.exists(wal_file):
            wal_size_before = os.path.getsize(wal_file)
            result['size_before'] += wal_size_before

        conn = sqlite3.connect(database_file, timeout=60)

        try:
            # Force WAL checkpoint to write all changes to main database
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            result['wal_truncated'] = True
            logger.info("WAL checkpoint completed")
        except sqlite3.Error as e:
            result['errors'].append(f"WAL checkpoint error: {e}")
            logger.warning(f"WAL checkpoint failed: {e}")

        try:
            # Run VACUUM to reclaim space
            conn.execute("VACUUM")
            logger.info("Database VACUUM completed")
        except sqlite3.Error as e:
            result['errors'].append(f"VACUUM error: {e}")
            logger.warning(f"VACUUM failed: {e}")

        try:
            # Analyze for query optimization
            conn.execute("ANALYZE")
            logger.info("Database ANALYZE completed")
        except sqlite3.Error as e:
            result['errors'].append(f"ANALYZE error: {e}")

        conn.close()

        # Get size after
        result['size_after'] = os.path.getsize(database_file)
        if os.path.exists(wal_file):
            result['size_after'] += os.path.getsize(wal_file)

        result['freed_bytes'] = result['size_before'] - result['size_after']

        if result['freed_bytes'] > 0:
            logger.info(f"Database maintenance: freed {result['freed_bytes']/1024/1024:.1f}MB "
                       f"(before: {result['size_before']/1024/1024:.1f}MB, after: {result['size_after']/1024/1024:.1f}MB)")

    except sqlite3.Error as e:
        result['errors'].append(f"Database error: {e}")
        logger.error(f"Database maintenance error: {e}")
    except OSError as e:
        result['errors'].append(f"Filesystem error: {e}")
        logger.error(f"Filesystem error during database maintenance: {e}")

    return result


def get_disk_usage_report(base_smartgallery_path, database_file):
    """
    Generate a comprehensive report of disk usage including volume info, cache directories,
    and media file breakdown.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the SQLite database

    Returns:
        dict with size information for each category and volume info
    """
    cache_dirs = get_cache_dirs(base_smartgallery_path)
    report = {}

    # Track cache directory paths to exclude from media scan
    cache_paths = set(cache_dirs.values())

    for name, path in cache_dirs.items():
        if not os.path.exists(path):
            report[name] = {'exists': False, 'size_bytes': 0, 'file_count': 0}
            continue

        total_size = 0
        file_count = 0
        oldest_file = None
        newest_file = None

        try:
            for root, dirs, files in os.walk(path):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        stat = os.stat(filepath)
                        total_size += stat.st_size
                        file_count += 1
                        mtime = stat.st_mtime
                        if oldest_file is None or mtime < oldest_file:
                            oldest_file = mtime
                        if newest_file is None or mtime > newest_file:
                            newest_file = mtime
                    except OSError:
                        pass
        except OSError:
            pass

        report[name] = {
            'exists': True,
            'size_bytes': total_size,
            'size_mb': total_size / 1024 / 1024,
            'size_gb': total_size / (1024 ** 3),
            'file_count': file_count,
            'path': path,
            'oldest_file_age_hours': (time.time() - oldest_file) / 3600 if oldest_file else None,
            'newest_file_age_hours': (time.time() - newest_file) / 3600 if newest_file else None,
        }

    # Add database info
    if os.path.exists(database_file):
        db_size = os.path.getsize(database_file)
        wal_file = database_file + '-wal'
        wal_size = 0
        if os.path.exists(wal_file):
            wal_size = os.path.getsize(wal_file)
            db_size += wal_size

        report['database'] = {
            'exists': True,
            'size_bytes': db_size,
            'size_mb': db_size / 1024 / 1024,
            'size_gb': db_size / (1024 ** 3),
            'file_count': 1,
            'path': database_file,
            'wal_size_bytes': wal_size,
            'wal_size_mb': wal_size / 1024 / 1024,
        }

    # Calculate total cache usage
    total_cache_bytes = sum(r.get('size_bytes', 0) for r in report.values())
    report['cache_total'] = {
        'size_bytes': total_cache_bytes,
        'size_mb': total_cache_bytes / 1024 / 1024,
        'size_gb': total_cache_bytes / (1024 ** 3),
    }

    # Scan media files (excluding cache directories)
    # Group by type: images, videos, audio, other
    media_stats = {
        'images': {'size_bytes': 0, 'file_count': 0, 'extensions': {}},
        'videos': {'size_bytes': 0, 'file_count': 0, 'extensions': {}},
        'audio': {'size_bytes': 0, 'file_count': 0, 'extensions': {}},
        'other': {'size_bytes': 0, 'file_count': 0, 'extensions': {}},
    }

    image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.svg'}
    video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv', '.flv', '.mpeg', '.mpg'}
    audio_exts = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}

    try:
        for root, dirs, files in os.walk(base_smartgallery_path):
            # Skip cache directories
            root_path = os.path.normpath(root)
            skip = False
            for cache_path in cache_paths:
                if root_path == os.path.normpath(cache_path) or root_path.startswith(os.path.normpath(cache_path) + os.sep):
                    skip = True
                    break
            if skip:
                continue

            for filename in files:
                filepath = os.path.join(root, filename)
                try:
                    file_size = os.path.getsize(filepath)
                    ext = os.path.splitext(filename)[1].lower()

                    if ext in image_exts:
                        category = 'images'
                    elif ext in video_exts:
                        category = 'videos'
                    elif ext in audio_exts:
                        category = 'audio'
                    else:
                        category = 'other'

                    media_stats[category]['size_bytes'] += file_size
                    media_stats[category]['file_count'] += 1
                    media_stats[category]['extensions'][ext] = media_stats[category]['extensions'].get(ext, 0) + 1
                except OSError:
                    pass
    except OSError:
        pass

    # Add media stats to report
    for category, stats in media_stats.items():
        report[f'media_{category}'] = {
            'exists': True,
            'size_bytes': stats['size_bytes'],
            'size_mb': stats['size_bytes'] / 1024 / 1024,
            'size_gb': stats['size_bytes'] / (1024 ** 3),
            'file_count': stats['file_count'],
            'top_extensions': sorted(stats['extensions'].items(), key=lambda x: x[1], reverse=True)[:5],
        }

    # Calculate total media usage
    total_media_bytes = sum(media_stats[cat]['size_bytes'] for cat in media_stats)
    report['media_total'] = {
        'size_bytes': total_media_bytes,
        'size_mb': total_media_bytes / 1024 / 1024,
        'size_gb': total_media_bytes / (1024 ** 3),
        'file_count': sum(media_stats[cat]['file_count'] for cat in media_stats),
    }

    # Add volume disk space info
    report['volume'] = get_volume_disk_space(base_smartgallery_path)

    # Calculate "other system" usage (volume used - media - cache)
    volume_used = report['volume'].get('used_bytes', 0)
    accounted_for = total_cache_bytes + total_media_bytes
    other_system = max(0, volume_used - accounted_for)
    report['other_system'] = {
        'size_bytes': other_system,
        'size_mb': other_system / 1024 / 1024,
        'size_gb': other_system / (1024 ** 3),
        'description': 'OS, apps, logs, and other system files',
    }

    # Add storage health status
    report['health'] = get_storage_health(base_smartgallery_path)

    # Add configuration info
    report['config'] = {
        'zip_retention_hours': ZIP_RETENTION_HOURS,
        'smashcut_retention_hours': SMASHCUT_RETENTION_HOURS,
        'warning_threshold': STORAGE_WARNING_THRESHOLD,
        'critical_threshold': STORAGE_CRITICAL_THRESHOLD,
        'emergency_threshold': STORAGE_EMERGENCY_THRESHOLD,
    }

    return report


def run_all_maintenance(base_smartgallery_path, database_file, aggressive=None):
    """
    Run all maintenance tasks.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the SQLite database
        aggressive: If True, use shorter retention periods. Defaults to AGGRESSIVE_CLEANUP env var.

    Returns:
        dict with results from all maintenance tasks
    """
    global _maintenance_running, _last_maintenance_run

    if aggressive is None:
        aggressive = AGGRESSIVE_CLEANUP

    with _maintenance_lock:
        if _maintenance_running:
            logger.warning("Maintenance already running, skipping")
            return {'skipped': True, 'reason': 'already_running'}
        _maintenance_running = True

    try:
        start_time = time.time()
        cache_dirs = get_cache_dirs(base_smartgallery_path)

        # Aggressive mode uses shorter retention
        zip_hours = 1 if aggressive else ZIP_RETENTION_HOURS
        smashcut_hours = 24 if aggressive else SMASHCUT_RETENTION_HOURS

        logger.info(f"Starting maintenance (aggressive={aggressive})")
        logger.info(f"  ZIP retention: {zip_hours}h, Smashcut retention: {smashcut_hours}h")

        # Get usage before
        usage_before = get_disk_usage_report(base_smartgallery_path, database_file)

        results = {
            'timestamp': datetime.now().isoformat(),
            'aggressive': aggressive,
            'usage_before': usage_before,
        }

        # Run cleanups
        results['zip'] = cleanup_zip_cache(cache_dirs['zip'], zip_hours)
        results['smashcut'] = cleanup_smashcut_cache(cache_dirs['smashcut'], smashcut_hours)
        results['thumbnails'] = cleanup_orphaned_thumbnails(cache_dirs['thumbnails'], database_file)
        results['sharepoint'] = cleanup_sharepoint_cache(cache_dirs['sharepoint'], database_file)
        results['database'] = vacuum_database(database_file)

        # Get usage after
        results['usage_after'] = get_disk_usage_report(base_smartgallery_path, database_file)

        # Calculate totals
        total_freed = sum([
            results['zip'].get('freed_bytes', 0),
            results['smashcut'].get('freed_bytes', 0),
            results['thumbnails'].get('freed_bytes', 0),
            results['sharepoint'].get('freed_bytes', 0),
            results['database'].get('freed_bytes', 0),
        ])

        total_deleted = sum([
            results['zip'].get('deleted_count', 0),
            results['smashcut'].get('deleted_count', 0),
            results['thumbnails'].get('deleted_count', 0),
            results['sharepoint'].get('deleted_count', 0),
        ])

        results['summary'] = {
            'total_freed_bytes': total_freed,
            'total_freed_mb': total_freed / 1024 / 1024,
            'total_deleted_files': total_deleted,
            'duration_seconds': time.time() - start_time,
        }

        logger.info(f"Maintenance complete: freed {results['summary']['total_freed_mb']:.1f}MB, "
                   f"deleted {total_deleted} files in {results['summary']['duration_seconds']:.1f}s")

        _last_maintenance_run = time.time()
        return results

    finally:
        with _maintenance_lock:
            _maintenance_running = False


def run_startup_maintenance(base_smartgallery_path, database_file):
    """
    Run intensive maintenance on startup for system recovery.

    This is triggered when STARTUP_MAINTENANCE=true or when the system
    needs recovery after disk space issues.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the SQLite database

    Returns:
        dict with maintenance results
    """
    logger.info("=" * 60)
    logger.info("STARTUP MAINTENANCE - Running intensive cleanup")
    logger.info("=" * 60)

    # Get initial disk usage report
    usage_before = get_disk_usage_report(base_smartgallery_path, database_file)
    total_before = usage_before.get('total', {}).get('size_mb', 0)

    logger.info(f"Cache disk usage before maintenance: {total_before:.1f}MB")
    for name, info in usage_before.items():
        if name != 'total' and info.get('exists'):
            logger.info(f"  {name}: {info.get('size_mb', 0):.1f}MB ({info.get('file_count', 0)} files)")

    # Run aggressive cleanup
    results = run_all_maintenance(base_smartgallery_path, database_file, aggressive=True)

    if results.get('skipped'):
        logger.warning("Startup maintenance skipped - another maintenance task is running")
        return results

    # Log results
    total_after = results.get('usage_after', {}).get('total', {}).get('size_mb', 0)
    freed = results.get('summary', {}).get('total_freed_mb', 0)

    logger.info("=" * 60)
    logger.info(f"STARTUP MAINTENANCE COMPLETE")
    logger.info(f"  Freed: {freed:.1f}MB")
    logger.info(f"  Cache usage: {total_before:.1f}MB -> {total_after:.1f}MB")
    logger.info("=" * 60)

    return results


def scheduled_maintenance_task(base_smartgallery_path, database_file):
    """
    Scheduled maintenance task for the APScheduler.
    Runs periodic cleanup with standard retention periods.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the SQLite database
    """
    try:
        logger.info("Running scheduled maintenance...")
        results = run_all_maintenance(base_smartgallery_path, database_file, aggressive=False)

        if not results.get('skipped'):
            freed_mb = results.get('summary', {}).get('total_freed_mb', 0)
            if freed_mb > 1:
                logger.info(f"Scheduled maintenance freed {freed_mb:.1f}MB")
    except Exception as e:
        logger.error(f"Scheduled maintenance error: {e}")
