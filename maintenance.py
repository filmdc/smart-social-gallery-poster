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
    Generate a report of disk usage by cache directories.

    Args:
        base_smartgallery_path: Base path for gallery storage
        database_file: Path to the SQLite database

    Returns:
        dict with size information for each cache type
    """
    cache_dirs = get_cache_dirs(base_smartgallery_path)
    report = {}

    for name, path in cache_dirs.items():
        if not os.path.exists(path):
            report[name] = {'exists': False, 'size_bytes': 0, 'file_count': 0}
            continue

        total_size = 0
        file_count = 0

        try:
            for root, dirs, files in os.walk(path):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        total_size += os.path.getsize(filepath)
                        file_count += 1
                    except OSError:
                        pass
        except OSError:
            pass

        report[name] = {
            'exists': True,
            'size_bytes': total_size,
            'size_mb': total_size / 1024 / 1024,
            'file_count': file_count,
            'path': path
        }

    # Add database info
    if os.path.exists(database_file):
        db_size = os.path.getsize(database_file)
        wal_file = database_file + '-wal'
        if os.path.exists(wal_file):
            db_size += os.path.getsize(wal_file)

        report['database'] = {
            'exists': True,
            'size_bytes': db_size,
            'size_mb': db_size / 1024 / 1024,
            'file_count': 1,
            'path': database_file
        }

    # Calculate total
    total_bytes = sum(r.get('size_bytes', 0) for r in report.values())
    report['total'] = {
        'size_bytes': total_bytes,
        'size_mb': total_bytes / 1024 / 1024
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
