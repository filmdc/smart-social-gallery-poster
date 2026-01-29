"""
Database models for the social media posting feature.

Uses the same SQLite database as the main gallery but with independent
tables and schema versioning via a social_meta table.
"""

import sqlite3

SOCIAL_SCHEMA_VERSION = 5

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS social_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'employee',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS registration_requests (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at REAL,
    denial_reason TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    FOREIGN KEY (reviewed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS user_preferences (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    favorite_folders TEXT DEFAULT '[]',
    favorite_files TEXT DEFAULT '[]',
    starting_folder TEXT DEFAULT NULL,
    updated_at REAL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS programs (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    created_by TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    created_by TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS file_programs (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    assigned_at REAL NOT NULL,
    assigned_by TEXT,
    UNIQUE(file_id, program_id),
    FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS file_campaigns (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    assigned_at REAL NOT NULL,
    assigned_by TEXT,
    UNIQUE(file_id, campaign_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sharepoint_sync_folders (
    id TEXT PRIMARY KEY,
    sp_folder_path TEXT NOT NULL,
    sp_folder_name TEXT NOT NULL,
    local_folder_name TEXT NOT NULL,
    include_subfolders INTEGER NOT NULL DEFAULT 1,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at REAL,
    last_sync_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    created_by TEXT,
    UNIQUE(sp_folder_path),
    FOREIGN KEY (created_by) REFERENCES users(id)
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
    if from_version < 2:
        # Add user_preferences table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                favorite_folders TEXT DEFAULT '[]',
                favorite_files TEXT DEFAULT '[]',
                starting_folder TEXT DEFAULT NULL,
                updated_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

    if from_version < 3:
        # Add email column to users table
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except sqlite3.OperationalError:
            pass  # Column might already exist

        # Add registration_requests table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registration_requests (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by TEXT,
                reviewed_at REAL,
                denial_reason TEXT,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            )
        """)

        # Add password_reset_tokens table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

    if from_version < 4:
        # Add programs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS programs (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                created_by TEXT,
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
        """)

        # Add campaigns table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                created_by TEXT,
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
        """)

        # Add file_programs junction table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_programs (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                program_id TEXT NOT NULL,
                assigned_at REAL NOT NULL,
                assigned_by TEXT,
                UNIQUE(file_id, program_id),
                FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
                FOREIGN KEY (assigned_by) REFERENCES users(id)
            )
        """)

        # Add file_campaigns junction table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_campaigns (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                assigned_at REAL NOT NULL,
                assigned_by TEXT,
                UNIQUE(file_id, campaign_id),
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                FOREIGN KEY (assigned_by) REFERENCES users(id)
            )
        """)
        conn.commit()

    if from_version < 5:
        # Add sharepoint_sync_folders table for selective folder sync
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sharepoint_sync_folders (
                id TEXT PRIMARY KEY,
                sp_folder_path TEXT NOT NULL,
                sp_folder_name TEXT NOT NULL,
                local_folder_name TEXT NOT NULL,
                include_subfolders INTEGER NOT NULL DEFAULT 1,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                last_sync_at REAL,
                last_sync_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                created_by TEXT,
                UNIQUE(sp_folder_path),
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
        """)
        conn.commit()
