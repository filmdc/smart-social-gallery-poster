"""
Scheduler for timed post publishing and token refresh.

Uses APScheduler BackgroundScheduler running in-process.
"""

import time
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from social.models import get_social_db
from social.oauth import decrypt_token, refresh_linkedin_token, refresh_facebook_token
from social.posting import PLATFORM_PUBLISHERS

_scheduler = None
_db_path = None
_app_secret_key = None


def init_scheduler(db_path, app_secret_key):
    """Start the background scheduler with publish and token refresh jobs."""
    global _scheduler, _db_path, _app_secret_key
    _db_path = db_path
    _app_secret_key = app_secret_key

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_check_scheduled_posts, 'interval', seconds=60, id='check_scheduled_posts',
                       misfire_grace_time=30)
    _scheduler.add_job(_refresh_expiring_tokens, 'interval', hours=12, id='refresh_tokens',
                       misfire_grace_time=300)
    _scheduler.add_job(_cleanup_expired_data, 'interval', hours=24, id='cleanup_expired_data',
                       misfire_grace_time=3600)
    _scheduler.start()


def shutdown_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _check_scheduled_posts():
    """Find approved posts whose scheduled_at has passed and publish them."""
    if not _db_path:
        return

    conn = get_social_db(_db_path)
    try:
        now = time.time()
        # Find posts that are approved/scheduled and due
        due_posts = conn.execute(
            "SELECT * FROM posts WHERE status = 'approved' AND scheduled_at IS NOT NULL AND scheduled_at <= ?",
            (now,)
        ).fetchall()

        for post in due_posts:
            # Launch publish in a separate thread
            t = threading.Thread(target=_publish_post, args=(dict(post),), daemon=True)
            t.start()
    finally:
        conn.close()


def _publish_post(post):
    """Publish a single post to all its target platforms."""
    if not _db_path:
        return

    post_id = post['id']
    conn = get_social_db(_db_path)

    try:
        # Update post status to publishing
        conn.execute("UPDATE posts SET status = 'publishing', updated_at = ? WHERE id = ?",
                      (time.time(), post_id))
        conn.commit()

        # Get target platforms for this post
        platform_targets = conn.execute(
            "SELECT pp.*, sa.platform, sa.access_token, sa.platform_account_id, "
            "sa.account_name, sa.account_type "
            "FROM post_platforms pp "
            "JOIN social_accounts sa ON pp.social_account_id = sa.id "
            "WHERE pp.post_id = ? AND pp.status = 'pending'",
            (post_id,)
        ).fetchall()

        # Get media files for this post
        media_rows = conn.execute(
            "SELECT pm.*, f.path FROM post_media pm "
            "JOIN files f ON pm.file_id = f.id "
            "WHERE pm.post_id = ? ORDER BY pm.sort_order",
            (post_id,)
        ).fetchall()
        media_paths = [row['path'] for row in media_rows]

        all_published = True
        any_published = False

        for target in platform_targets:
            platform = target['platform']
            publisher = PLATFORM_PUBLISHERS.get(platform)
            if not publisher:
                conn.execute(
                    "UPDATE post_platforms SET status='failed', error_message=? WHERE id=?",
                    (f"Unknown platform: {platform}", target['id'])
                )
                all_published = False
                continue

            # Update platform status to publishing
            conn.execute("UPDATE post_platforms SET status='publishing' WHERE id=?", (target['id'],))
            conn.commit()

            # Build account dict for publisher
            account = dict(target)
            result = publisher(account, dict(post), media_paths, _app_secret_key)

            now = time.time()
            conn.execute(
                "UPDATE post_platforms SET status=?, platform_post_id=?, platform_url=?, "
                "error_message=?, published_at=? WHERE id=?",
                (
                    result['status'],
                    result.get('platform_post_id'),
                    result.get('platform_url'),
                    result.get('error_message'),
                    now if result['status'] == 'published' else None,
                    target['id'],
                )
            )
            conn.commit()

            if result['status'] == 'published':
                any_published = True
            else:
                all_published = False

        # Update overall post status
        now = time.time()
        if all_published and any_published:
            conn.execute("UPDATE posts SET status='published', published_at=?, updated_at=? WHERE id=?",
                          (now, now, post_id))
        elif any_published:
            # Partial success - some platforms failed
            conn.execute("UPDATE posts SET status='published', published_at=?, updated_at=? WHERE id=?",
                          (now, now, post_id))
        else:
            conn.execute("UPDATE posts SET status='failed', updated_at=? WHERE id=?",
                          (now, post_id))
        conn.commit()

    except Exception as e:
        try:
            conn.execute("UPDATE posts SET status='failed', updated_at=? WHERE id=?",
                          (time.time(), post_id))
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


def _refresh_expiring_tokens():
    """Refresh tokens that are expiring within the next 7 days."""
    if not _db_path:
        return

    conn = get_social_db(_db_path)
    try:
        threshold = time.time() + (7 * 24 * 3600)  # 7 days from now
        accounts = conn.execute(
            "SELECT * FROM social_accounts WHERE is_active = 1 AND token_expires_at < ?",
            (threshold,)
        ).fetchall()

        for account in accounts:
            account_dict = dict(account)
            platform = account_dict['platform']
            result = None

            if platform == 'linkedin':
                result = refresh_linkedin_token(account_dict, _app_secret_key)
            elif platform in ('facebook', 'instagram'):
                result = refresh_facebook_token(account_dict, _app_secret_key)

            if result and isinstance(result, dict):
                conn.execute(
                    "UPDATE social_accounts SET access_token=?, refresh_token=?, "
                    "token_expires_at=?, updated_at=? WHERE id=?",
                    (
                        result['access_token'],
                        result.get('refresh_token', account_dict.get('refresh_token')),
                        result['token_expires_at'],
                        time.time(),
                        account_dict['id'],
                    )
                )
                conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def publish_post_now(post_id, db_path, app_secret_key):
    """Immediately publish a post in a background thread."""
    conn = get_social_db(db_path)
    try:
        post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            return
        post_dict = dict(post)
    finally:
        conn.close()

    global _db_path, _app_secret_key
    _db_path = db_path
    _app_secret_key = app_secret_key

    t = threading.Thread(target=_publish_post, args=(post_dict,), daemon=True)
    t.start()


def _cleanup_expired_data():
    """Clean up expired data: move history (30 days), registration requests, password reset tokens."""
    import sqlite3

    if not _db_path:
        return

    now = time.time()
    thirty_days_ago = now - (30 * 24 * 60 * 60)

    # Clean up move history from main gallery database
    try:
        # The gallery database is at the same location but without the social tables
        # We need to connect to the same database file
        conn = sqlite3.connect(_db_path)
        conn.execute("DELETE FROM file_move_history WHERE moved_at < ?", (thirty_days_ago,))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Table might not exist in the database

    # Clean up expired registration requests and password reset tokens
    conn = get_social_db(_db_path)
    try:
        # Clean up expired registration requests (pending ones that expired)
        conn.execute(
            "DELETE FROM registration_requests WHERE status = 'pending' AND expires_at < ?",
            (now,)
        )
        # Clean up used or expired password reset tokens
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at < ? OR used_at IS NOT NULL",
            (now,)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
