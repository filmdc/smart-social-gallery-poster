"""
Authentication module for the social media posting feature.

Provides flask-login integration, User class, password hashing,
and role-based access decorators.
"""

import time
import uuid
import functools
import sqlite3

import bcrypt
from flask import redirect, url_for, flash, request
from flask_login import LoginManager, UserMixin, current_user

from social.models import get_social_db

# Module-level reference set by init_login_manager
_db_path = None
login_manager = LoginManager()


class User(UserMixin):
    """User model backed by SQLite.

    Supported roles:
    - admin: Full access to all features including user management, social account management,
             SharePoint sync, and all post management operations.
    - marketing_admin: Can manage folders, delete media, post to social media without requiring
                       approval. Cannot manage users or connect social accounts.
    - employee: Can browse gallery, use media to start new social media post submissions,
                and submit posts for approval. Cannot delete media or manage folders.
    """

    # Valid roles for this application
    VALID_ROLES = ('admin', 'marketing_admin', 'employee')

    def __init__(self, id, username, display_name, role, is_active, password_hash=None,
                 created_at=None, last_login=None, email=None):
        self.id = id
        self.username = username
        self.email = email
        self.display_name = display_name
        self.role = role
        self._is_active = is_active
        self.password_hash = password_hash
        self.created_at = created_at
        self.last_login = last_login

    @property
    def is_active(self):
        return bool(self._is_active)

    @property
    def is_admin(self):
        """Full admin access - user management, social accounts, SharePoint, etc."""
        return self.role == 'admin'

    @property
    def is_marketing_admin(self):
        """Marketing admin - can manage media, post without approval."""
        return self.role == 'marketing_admin'

    @property
    def is_employee(self):
        """Employee - can browse and submit posts for approval."""
        return self.role == 'employee'

    @property
    def can_manage_media(self):
        """Can delete files and manage folders."""
        return self.role in ('admin', 'marketing_admin')

    @property
    def can_post_without_approval(self):
        """Can approve and publish posts without needing approval from others."""
        return self.role in ('admin', 'marketing_admin')

    @property
    def can_manage_users(self):
        """Can create, edit, and delete users."""
        return self.role == 'admin'

    @property
    def can_manage_social_accounts(self):
        """Can connect and disconnect social media accounts."""
        return self.role == 'admin'

    @property
    def can_manage_sharepoint(self):
        """Can configure and sync SharePoint integration."""
        return self.role == 'admin'

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    @staticmethod
    def hash_password(password):
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @classmethod
    def from_row(cls, row):
        if row is None:
            return None
        return cls(
            id=row['id'],
            username=row['username'],
            display_name=row['display_name'],
            role=row['role'],
            is_active=row['is_active'],
            password_hash=row['password_hash'],
            created_at=row['created_at'],
            last_login=row['last_login'],
            email=row['email'] if 'email' in row.keys() else None,
        )

    @classmethod
    def get_by_id(cls, user_id, db_path):
        conn = get_social_db(db_path)
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return cls.from_row(row)
        finally:
            conn.close()

    @classmethod
    def get_by_username(cls, username, db_path):
        conn = get_social_db(db_path)
        try:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            return cls.from_row(row)
        finally:
            conn.close()

    @classmethod
    def get_by_email(cls, email, db_path):
        """Get a user by their email address (case-insensitive)."""
        if not email:
            return None
        conn = get_social_db(db_path)
        try:
            # Normalize to lowercase since emails are stored lowercase
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
            return cls.from_row(row)
        finally:
            conn.close()

    @classmethod
    def get_admins(cls, db_path):
        """Get all admin users (for notifications)."""
        conn = get_social_db(db_path)
        try:
            rows = conn.execute("SELECT * FROM users WHERE role = 'admin' AND is_active = 1").fetchall()
            return [cls.from_row(row) for row in rows]
        finally:
            conn.close()

    @classmethod
    def create(cls, username, password, display_name, role, db_path, email=None):
        user_id = str(uuid.uuid4())
        password_hash = cls.hash_password(password)
        now = time.time()
        conn = get_social_db(db_path)
        try:
            conn.execute(
                "INSERT INTO users (id, username, email, password_hash, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (user_id, username, email, password_hash, display_name, role, now)
            )
            conn.commit()
            return cls(id=user_id, username=username, display_name=display_name,
                       role=role, is_active=1, password_hash=password_hash, created_at=now, email=email)
        finally:
            conn.close()

    @classmethod
    def count(cls, db_path):
        conn = get_social_db(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
            return row['cnt']
        finally:
            conn.close()

    def update_last_login(self, db_path):
        now = time.time()
        conn = get_social_db(db_path)
        try:
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, self.id))
            conn.commit()
            self.last_login = now
        finally:
            conn.close()

    def update_profile(self, db_path, display_name=None, email=None):
        """Update user profile information."""
        conn = get_social_db(db_path)
        try:
            updates = []
            params = []
            if display_name is not None:
                updates.append("display_name = ?")
                params.append(display_name)
                self.display_name = display_name
            if email is not None:
                updates.append("email = ?")
                params.append(email.lower() if email else None)
                self.email = email.lower() if email else None
            if updates:
                params.append(self.id)
                conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()
        finally:
            conn.close()

    def change_password(self, db_path, new_password):
        """Change the user's password."""
        new_hash = self.hash_password(new_password)
        conn = get_social_db(db_path)
        try:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, self.id))
            conn.commit()
            self.password_hash = new_hash
        finally:
            conn.close()


def init_login_manager(app, db_path, site_name='Smart Asset Gallery'):
    """Initialize flask-login with the Flask app."""
    global _db_path
    _db_path = db_path

    login_manager.init_app(app)
    login_manager.login_view = 'social.login'
    login_manager.login_message = f'Please log in to access {site_name}.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.get_by_id(user_id, _db_path)


def admin_required(f):
    """Decorator that requires the user to be logged in and have admin role."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('social.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def media_manager_required(f):
    """Decorator that requires the user to have media management permissions.

    Allows admin and marketing_admin roles.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.can_manage_media:
            flash('Media management access required.', 'error')
            return redirect(url_for('social.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def approver_required(f):
    """Decorator that requires the user to have post approval permissions.

    Allows admin and marketing_admin roles who can approve/publish posts.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.can_post_without_approval:
            flash('Post approval access required.', 'error')
            return redirect(url_for('social.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def has_users(db_path):
    """Check if any users exist in the database."""
    return User.count(db_path) > 0


# =========================================================================
# REGISTRATION REQUESTS
# =========================================================================

REGISTRATION_EXPIRY_DAYS = 30


def create_registration_request(email, display_name, password, reason, db_path):
    """
    Create a new registration request.

    Returns:
        tuple: (request_id, error_message)
    """
    request_id = str(uuid.uuid4())
    password_hash = User.hash_password(password)
    now = time.time()
    expires_at = now + (REGISTRATION_EXPIRY_DAYS * 24 * 60 * 60)

    conn = get_social_db(db_path)
    try:
        # Check if email already exists as a user
        existing_user = conn.execute(
            "SELECT id FROM users WHERE email = ? OR username = ?",
            (email, email)
        ).fetchone()
        if existing_user:
            return None, "An account with this email already exists."

        # Check if there's already a pending request for this email
        existing_request = conn.execute(
            "SELECT id, status FROM registration_requests WHERE email = ?",
            (email,)
        ).fetchone()
        if existing_request:
            if existing_request['status'] == 'pending':
                return None, "A registration request for this email is already pending."
            elif existing_request['status'] == 'denied':
                # Allow re-request after denial - delete old request
                conn.execute("DELETE FROM registration_requests WHERE id = ?",
                           (existing_request['id'],))

        conn.execute(
            "INSERT INTO registration_requests (id, email, display_name, password_hash, reason, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (request_id, email, display_name, password_hash, reason, now, expires_at)
        )
        conn.commit()
        return request_id, None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def get_pending_registration_requests(db_path):
    """Get all pending registration requests that haven't expired."""
    conn = get_social_db(db_path)
    try:
        now = time.time()
        rows = conn.execute(
            "SELECT * FROM registration_requests WHERE status = 'pending' AND expires_at > ? ORDER BY created_at DESC",
            (now,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_registration_request(request_id, db_path):
    """Get a specific registration request by ID."""
    conn = get_social_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM registration_requests WHERE id = ?",
            (request_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def approve_registration_request(request_id, admin_user_id, role, db_path):
    """
    Approve a registration request and create the user account.

    Returns:
        tuple: (user, error_message)
    """
    conn = get_social_db(db_path)
    try:
        req = conn.execute(
            "SELECT * FROM registration_requests WHERE id = ?",
            (request_id,)
        ).fetchone()

        if not req:
            return None, "Registration request not found."
        if req['status'] != 'pending':
            return None, f"Registration request is not pending (status: {req['status']})."
        if req['expires_at'] < time.time():
            return None, "Registration request has expired."

        # Create the user account
        user_id = str(uuid.uuid4())
        now = time.time()

        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, display_name, role, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (user_id, req['email'], req['email'], req['password_hash'], req['display_name'], role, now)
        )

        # Update the request status
        conn.execute(
            "UPDATE registration_requests SET status = 'approved', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (admin_user_id, now, request_id)
        )
        conn.commit()

        user = User.get_by_id(user_id, db_path)
        return user, None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def deny_registration_request(request_id, admin_user_id, reason, db_path):
    """
    Deny a registration request.

    Returns:
        tuple: (success, error_message)
    """
    conn = get_social_db(db_path)
    try:
        req = conn.execute(
            "SELECT * FROM registration_requests WHERE id = ?",
            (request_id,)
        ).fetchone()

        if not req:
            return False, "Registration request not found."
        if req['status'] != 'pending':
            return False, f"Registration request is not pending (status: {req['status']})."

        now = time.time()
        conn.execute(
            "UPDATE registration_requests SET status = 'denied', reviewed_by = ?, reviewed_at = ?, denial_reason = ? WHERE id = ?",
            (admin_user_id, now, reason, request_id)
        )
        conn.commit()
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def cleanup_expired_registration_requests(db_path):
    """Remove expired registration requests."""
    conn = get_social_db(db_path)
    try:
        now = time.time()
        conn.execute(
            "DELETE FROM registration_requests WHERE expires_at < ? AND status = 'pending'",
            (now,)
        )
        conn.commit()
    finally:
        conn.close()


# =========================================================================
# PASSWORD RESET
# =========================================================================

import secrets

PASSWORD_RESET_EXPIRY_HOURS = 24


def create_password_reset_token(user_id, db_path):
    """
    Create a password reset token for a user.

    Returns:
        tuple: (token, error_message)
    """
    token = secrets.token_urlsafe(32)
    token_id = str(uuid.uuid4())
    now = time.time()
    expires_at = now + (PASSWORD_RESET_EXPIRY_HOURS * 60 * 60)

    conn = get_social_db(db_path)
    try:
        # Invalidate any existing tokens for this user
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ?",
            (user_id,)
        )

        conn.execute(
            "INSERT INTO password_reset_tokens (id, user_id, token, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token_id, user_id, token, now, expires_at)
        )
        conn.commit()
        return token, None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()


def validate_password_reset_token(token, db_path):
    """
    Validate a password reset token.

    Returns:
        tuple: (user_id, error_message)
    """
    conn = get_social_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ?",
            (token,)
        ).fetchone()

        if not row:
            return None, "Invalid or expired reset link."
        if row['expires_at'] < time.time():
            return None, "This reset link has expired. Please request a new one."
        if row['used_at'] is not None:
            return None, "This reset link has already been used."

        return row['user_id'], None
    finally:
        conn.close()


def use_password_reset_token(token, new_password, db_path):
    """
    Use a password reset token to change the user's password.

    Returns:
        tuple: (success, error_message)
    """
    user_id, error = validate_password_reset_token(token, db_path)
    if error:
        return False, error

    conn = get_social_db(db_path)
    try:
        password_hash = User.hash_password(new_password)
        now = time.time()

        # Update user's password
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id)
        )

        # Mark token as used
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token = ?",
            (now, token)
        )
        conn.commit()
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def cleanup_expired_reset_tokens(db_path):
    """Remove expired or used password reset tokens."""
    conn = get_social_db(db_path)
    try:
        now = time.time()
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at < ? OR used_at IS NOT NULL",
            (now,)
        )
        conn.commit()
    finally:
        conn.close()
