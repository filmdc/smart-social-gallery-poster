"""
Database models for the social media posting feature.

Uses the same SQLite database as the main gallery but with independent
tables and schema versioning via a social_meta table.
"""

import sqlite3

SOCIAL_SCHEMA_VERSION = 1

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS social_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'submitter',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS social_accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_account_id TEXT,
    account_name TEXT,
    account_type TEXT DEFAULT 'page',
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at REAL,
    scopes TEXT DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    created_by TEXT NOT NULL,
    caption TEXT DEFAULT '',
    hashtags TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    rejection_reason TEXT,
    scheduled_at REAL,
    published_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL,
    approved_by TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (approved_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS post_media (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_platforms (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    social_account_id TEXT NOT NULL,
    platform_post_id TEXT,
    platform_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    published_at REAL,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (social_account_id) REFERENCES social_accounts(id)
);
"""


def get_social_db(db_path):
    """Get a database connection with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_social_tables(db_path):
    """Create social tables if they don't exist. Handles schema migration."""
    conn = get_social_db(db_path)
    try:
        conn.executescript(_CREATE_TABLES_SQL)
        conn.commit()

        # Check schema version
        try:
            row = conn.execute(
                "SELECT value FROM social_meta WHERE key = 'schema_version'"
            ).fetchone()
            stored_version = int(row['value']) if row else 0
        except sqlite3.OperationalError:
            stored_version = 0

        if stored_version < SOCIAL_SCHEMA_VERSION:
            _run_migrations(conn, stored_version, SOCIAL_SCHEMA_VERSION)
            conn.execute(
                "INSERT OR REPLACE INTO social_meta (key, value) VALUES ('schema_version', ?)",
                (str(SOCIAL_SCHEMA_VERSION),)
            )
            conn.commit()
    finally:
        conn.close()


def _run_migrations(conn, from_version, to_version):
    """Run incremental schema migrations."""
    # Future migrations go here as elif blocks:
    # if from_version < 2:
    #     conn.execute("ALTER TABLE ...")
    pass
