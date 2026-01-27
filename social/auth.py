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
    """User model backed by SQLite."""

    def __init__(self, id, username, display_name, role, is_active, password_hash=None,
                 created_at=None, last_login=None):
        self.id = id
        self.username = username
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
    def create(cls, username, password, display_name, role, db_path):
        user_id = str(uuid.uuid4())
        password_hash = cls.hash_password(password)
        now = time.time()
        conn = get_social_db(db_path)
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (user_id, username, password_hash, display_name, role, now)
            )
            conn.commit()
            return cls(id=user_id, username=username, display_name=display_name,
                       role=role, is_active=1, password_hash=password_hash, created_at=now)
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


def init_login_manager(app, db_path):
    """Initialize flask-login with the Flask app."""
    global _db_path
    _db_path = db_path

    login_manager.init_app(app)
    login_manager.login_view = 'social.login'
    login_manager.login_message = 'Please log in to access social features.'
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


def has_users(db_path):
    """Check if any users exist in the database."""
    return User.count(db_path) > 0
