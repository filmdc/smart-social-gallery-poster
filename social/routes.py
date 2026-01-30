"""
Flask routes for the social media posting feature.

All routes are registered on the social Blueprint with prefix /galleryout/social/.
"""

import os
import time
import uuid
import json
import sqlite3

from flask import (render_template, request, redirect, url_for, jsonify,
                   flash, session, current_app)
from flask_login import login_user, logout_user, login_required, current_user

from social.auth import (
    User, admin_required, approver_required, has_users,
    create_registration_request, get_pending_registration_requests,
    get_registration_request, approve_registration_request,
    deny_registration_request, create_password_reset_token,
    validate_password_reset_token, use_password_reset_token
)
from social.models import get_social_db
from social.oauth import (
    facebook_available, linkedin_available,
    get_facebook_authorize_url, exchange_facebook_code,
    get_linkedin_authorize_url, exchange_linkedin_code,
    save_social_account,
)
from social.scheduler import publish_post_now

_db_path = None


def _get_external_url(endpoint, **kwargs):
    """
    Generate an external URL that uses HTTPS when behind a reverse proxy.

    Railway, Heroku, and similar platforms terminate SSL at the proxy level,
    so Flask sees HTTP internally. This function detects the X-Forwarded-Proto
    header and ensures the generated URL uses HTTPS when appropriate.
    """
    url = url_for(endpoint, _external=True, **kwargs)
    # Check if we're behind a proxy that's handling HTTPS
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    if forwarded_proto == 'https' and url.startswith('http://'):
        url = 'https://' + url[7:]
    return url


def register_routes(bp, db_path):
    """Register all social routes on the given Blueprint."""
    global _db_path
    _db_path = db_path

    # =========================================================================
    # AUTH ROUTES
    # =========================================================================

    @bp.route('/setup', methods=['GET', 'POST'])
    def setup():
        """First-time admin account creation."""
        if has_users(_db_path):
            return redirect(url_for('social.login'))

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '').strip()
            confirm = request.form.get('confirm_password', '').strip()
            display_name = request.form.get('display_name', '').strip() or username

            errors = []
            if not username or len(username) < 3:
                errors.append('Username must be at least 3 characters.')
            if not email or '@' not in email:
                errors.append('Please enter a valid email address.')
            if not password or len(password) < 8:
                errors.append('Password must be at least 8 characters.')
            if password != confirm:
                errors.append('Passwords do not match.')

            if errors:
                return render_template('social/login.html', setup_mode=True,
                                       errors=errors, username=username,
                                       email=email, display_name=display_name)

            user = User.create(username, password, display_name, 'admin', _db_path, email=email)
            login_user(user)
            user.update_last_login(_db_path)
            flash('Admin account created successfully.', 'success')
            return redirect(url_for('social.dashboard'))

        return render_template('social/login.html', setup_mode=True)

    @bp.route('/login', methods=['GET', 'POST'])
    def login():
        """Login page."""
        if not has_users(_db_path):
            return redirect(url_for('social.setup'))

        if current_user.is_authenticated:
            return redirect(url_for('social.dashboard'))

        if request.method == 'POST':
            username_or_email = request.form.get('username', '').strip()
            password = request.form.get('password', '')

            # Try to find user by username first, then by email
            user = User.get_by_username(username_or_email, _db_path)
            if not user:
                user = User.get_by_email(username_or_email, _db_path)

            if user and user.is_active and user.check_password(password):
                login_user(user)
                user.update_last_login(_db_path)
                next_page = request.args.get('next')
                return redirect(next_page or url_for('social.dashboard'))

            return render_template('social/login.html', setup_mode=False,
                                   errors=['Invalid username/email or password.'],
                                   username=username_or_email)

        return render_template('social/login.html', setup_mode=False)

    @bp.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('gallery_redirect_base'))

    # =========================================================================
    # PROFILE
    # =========================================================================

    @bp.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        """User profile page for editing personal information."""
        if request.method == 'POST':
            action = request.form.get('action', 'update_profile')

            if action == 'update_profile':
                display_name = request.form.get('display_name', '').strip()
                email = request.form.get('email', '').strip()

                errors = []
                if not display_name:
                    errors.append('Display name is required.')
                if email and '@' not in email:
                    errors.append('Please enter a valid email address.')

                if errors:
                    return render_template('social/profile.html', errors=errors)

                current_user.update_profile(_db_path, display_name=display_name, email=email or None)
                flash('Profile updated successfully.', 'success')
                return redirect(url_for('social.profile'))

            elif action == 'change_password':
                current_password = request.form.get('current_password', '')
                new_password = request.form.get('new_password', '')
                confirm_password = request.form.get('confirm_password', '')

                errors = []
                if not current_user.check_password(current_password):
                    errors.append('Current password is incorrect.')
                if len(new_password) < 8:
                    errors.append('New password must be at least 8 characters.')
                if new_password != confirm_password:
                    errors.append('New passwords do not match.')

                if errors:
                    return render_template('social/profile.html', errors=errors, password_error=True)

                current_user.change_password(_db_path, new_password)
                flash('Password changed successfully.', 'success')
                return redirect(url_for('social.profile'))

        return render_template('social/profile.html')

    # =========================================================================
    # REGISTRATION REQUEST
    # =========================================================================

    @bp.route('/request-access', methods=['GET', 'POST'])
    def request_access():
        """Public page for users to request access."""
        if not has_users(_db_path):
            return redirect(url_for('social.setup'))

        if current_user.is_authenticated:
            return redirect(url_for('social.dashboard'))

        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            display_name = request.form.get('display_name', '').strip()
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            reason = request.form.get('reason', '').strip()

            errors = []
            if not email or '@' not in email:
                errors.append('Please enter a valid email address.')
            if not display_name or len(display_name) < 2:
                errors.append('Display name must be at least 2 characters.')
            if not password or len(password) < 8:
                errors.append('Password must be at least 8 characters.')
            if password != confirm_password:
                errors.append('Passwords do not match.')

            if errors:
                return render_template('social/request_access.html',
                                       errors=errors,
                                       email=email,
                                       display_name=display_name,
                                       reason=reason)

            request_id, error = create_registration_request(
                email, display_name, password, reason, _db_path
            )

            if error:
                return render_template('social/request_access.html',
                                       errors=[error],
                                       email=email,
                                       display_name=display_name,
                                       reason=reason)

            # Send notification to admins
            try:
                from social.email import (
                    email_configured, send_registration_request_notification
                )
                if email_configured():
                    admins = User.get_admins(_db_path)
                    admin_emails = [a.email for a in admins if a.email]
                    if admin_emails:
                        send_registration_request_notification(
                            admin_emails, display_name, email, reason
                        )
            except Exception as e:
                # Log but don't fail the request
                print(f"Warning: Could not send admin notification email: {e}")

            flash('Your access request has been submitted. You will receive an email when it is reviewed.', 'success')
            return redirect(url_for('social.login'))

        return render_template('social/request_access.html')

    # =========================================================================
    # PASSWORD RESET
    # =========================================================================

    @bp.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        """Request password reset."""
        if not has_users(_db_path):
            return redirect(url_for('social.setup'))

        if current_user.is_authenticated:
            return redirect(url_for('social.dashboard'))

        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()

            if not email or '@' not in email:
                return render_template('social/forgot_password.html',
                                       errors=['Please enter a valid email address.'],
                                       email=email)

            # Find user by email
            user = User.get_by_email(email, _db_path)

            # Always show success message to prevent email enumeration
            if user and user.is_active:
                token, error = create_password_reset_token(user.id, _db_path)
                if token and not error:
                    try:
                        from social.email import email_configured, send_password_reset
                        if email_configured():
                            send_password_reset(user.email, user.display_name, token)
                    except Exception as e:
                        print(f"Warning: Could not send password reset email: {e}")

            flash('If an account with that email exists, you will receive a password reset link.', 'info')
            return redirect(url_for('social.login'))

        return render_template('social/forgot_password.html')

    @bp.route('/reset-password', methods=['GET', 'POST'])
    def reset_password():
        """Reset password using token."""
        token = request.args.get('token', '')

        if not token:
            flash('Invalid reset link.', 'error')
            return redirect(url_for('social.login'))

        # Validate token
        user_id, error = validate_password_reset_token(token, _db_path)
        if error:
            flash(error, 'error')
            return redirect(url_for('social.forgot_password'))

        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()

            errors = []
            if not password or len(password) < 8:
                errors.append('Password must be at least 8 characters.')
            if password != confirm_password:
                errors.append('Passwords do not match.')

            if errors:
                return render_template('social/reset_password.html',
                                       errors=errors,
                                       token=token)

            success, error = use_password_reset_token(token, password, _db_path)
            if error:
                flash(error, 'error')
                return redirect(url_for('social.forgot_password'))

            flash('Your password has been reset. Please log in with your new password.', 'success')
            return redirect(url_for('social.login'))

        return render_template('social/reset_password.html', token=token)

    # =========================================================================
    # REGISTRATION REQUEST MANAGEMENT (Admin)
    # =========================================================================

    @bp.route('/registration-requests')
    @admin_required
    def registration_requests():
        """List pending registration requests."""
        requests = get_pending_registration_requests(_db_path)
        return render_template('social/registration_requests.html', requests=requests)

    @bp.route('/registration-requests/<request_id>/approve', methods=['POST'])
    @admin_required
    def approve_request(request_id):
        """Approve a registration request."""
        role = request.form.get('role', 'employee')
        if role not in User.VALID_ROLES:
            role = 'employee'

        req = get_registration_request(request_id, _db_path)
        if not req:
            if request.is_json:
                return jsonify({'error': 'Request not found'}), 404
            flash('Registration request not found.', 'error')
            return redirect(url_for('social.registration_requests'))

        user, error = approve_registration_request(
            request_id, current_user.id, role, _db_path
        )

        if error:
            if request.is_json:
                return jsonify({'error': error}), 400
            flash(f'Error approving request: {error}', 'error')
            return redirect(url_for('social.registration_requests'))

        # Send approval notification
        try:
            from social.email import email_configured, send_registration_approved
            if email_configured():
                send_registration_approved(user.email, user.display_name)
        except Exception as e:
            print(f"Warning: Could not send approval email: {e}")

        if request.is_json:
            return jsonify({'success': True, 'user_id': user.id})

        flash(f'Registration approved. {user.display_name} can now log in.', 'success')
        return redirect(url_for('social.registration_requests'))

    @bp.route('/registration-requests/<request_id>/deny', methods=['POST'])
    @admin_required
    def deny_request(request_id):
        """Deny a registration request."""
        reason = request.form.get('reason', '') if not request.is_json else request.json.get('reason', '')

        req = get_registration_request(request_id, _db_path)
        if not req:
            if request.is_json:
                return jsonify({'error': 'Request not found'}), 404
            flash('Registration request not found.', 'error')
            return redirect(url_for('social.registration_requests'))

        success, error = deny_registration_request(
            request_id, current_user.id, reason, _db_path
        )

        if error:
            if request.is_json:
                return jsonify({'error': error}), 400
            flash(f'Error denying request: {error}', 'error')
            return redirect(url_for('social.registration_requests'))

        # Send denial notification
        try:
            from social.email import email_configured, send_registration_denied
            if email_configured():
                send_registration_denied(req['email'], req['display_name'], reason)
        except Exception as e:
            print(f"Warning: Could not send denial email: {e}")

        if request.is_json:
            return jsonify({'success': True})

        flash('Registration request denied.', 'info')
        return redirect(url_for('social.registration_requests'))

    # =========================================================================
    # PROGRAMS & CAMPAIGNS MANAGEMENT
    # =========================================================================

    @bp.route('/categories')
    @login_required
    def categories():
        """View and manage programs and campaigns."""
        # Only admin and marketing_admin can manage categories
        if not current_user.can_post_without_approval:
            flash('You do not have permission to manage categories.', 'error')
            return redirect(url_for('social.dashboard'))

        conn = get_social_db(_db_path)
        try:
            programs = conn.execute(
                "SELECT * FROM programs ORDER BY sort_order, name"
            ).fetchall()
            campaigns = conn.execute(
                "SELECT * FROM campaigns ORDER BY sort_order, name"
            ).fetchall()
        finally:
            conn.close()

        return render_template('social/categories.html',
                               programs=[dict(p) for p in programs],
                               campaigns=[dict(c) for c in campaigns])

    @bp.route('/programs', methods=['POST'])
    @admin_required
    def create_program():
        """Create a new program (admin only)."""
        import uuid
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            if request.is_json:
                return jsonify({'error': 'Program name is required'}), 400
            flash('Program name is required.', 'error')
            return redirect(url_for('social.categories'))

        conn = get_social_db(_db_path)
        try:
            # Check for duplicate
            existing = conn.execute(
                "SELECT id FROM programs WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                if request.is_json:
                    return jsonify({'error': 'A program with this name already exists'}), 400
                flash('A program with this name already exists.', 'error')
                return redirect(url_for('social.categories'))

            program_id = str(uuid.uuid4())
            now = time.time()
            conn.execute(
                "INSERT INTO programs (id, name, description, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                (program_id, name, description, now, current_user.id)
            )
            conn.commit()

            if request.is_json:
                return jsonify({'success': True, 'id': program_id, 'name': name})
            flash(f'Program "{name}" created.', 'success')
        finally:
            conn.close()

        return redirect(url_for('social.categories'))

    @bp.route('/programs/<program_id>', methods=['PUT', 'DELETE'])
    @admin_required
    def manage_program(program_id):
        """Update or delete a program (admin only)."""
        conn = get_social_db(_db_path)
        try:
            if request.method == 'DELETE':
                conn.execute("DELETE FROM programs WHERE id = ?", (program_id,))
                conn.commit()
                return jsonify({'success': True})

            # PUT - update
            data = request.json or {}
            name = data.get('name', '').strip()
            description = data.get('description', '').strip()
            is_active = data.get('is_active', True)

            if name:
                # Check for duplicate name
                existing = conn.execute(
                    "SELECT id FROM programs WHERE name = ? AND id != ?", (name, program_id)
                ).fetchone()
                if existing:
                    return jsonify({'error': 'A program with this name already exists'}), 400

                conn.execute(
                    "UPDATE programs SET name = ?, description = ?, is_active = ? WHERE id = ?",
                    (name, description, 1 if is_active else 0, program_id)
                )
            else:
                conn.execute(
                    "UPDATE programs SET is_active = ? WHERE id = ?",
                    (1 if is_active else 0, program_id)
                )
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    @bp.route('/campaigns', methods=['POST'])
    @approver_required
    def create_campaign():
        """Create a new campaign (marketing_admin or admin)."""
        import uuid
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            if request.is_json:
                return jsonify({'error': 'Campaign name is required'}), 400
            flash('Campaign name is required.', 'error')
            return redirect(url_for('social.categories'))

        conn = get_social_db(_db_path)
        try:
            # Check for duplicate
            existing = conn.execute(
                "SELECT id FROM campaigns WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                if request.is_json:
                    return jsonify({'error': 'A campaign with this name already exists'}), 400
                flash('A campaign with this name already exists.', 'error')
                return redirect(url_for('social.categories'))

            campaign_id = str(uuid.uuid4())
            now = time.time()
            conn.execute(
                "INSERT INTO campaigns (id, name, description, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                (campaign_id, name, description, now, current_user.id)
            )
            conn.commit()

            if request.is_json:
                return jsonify({'success': True, 'id': campaign_id, 'name': name})
            flash(f'Campaign "{name}" created.', 'success')
        finally:
            conn.close()

        return redirect(url_for('social.categories'))

    @bp.route('/campaigns/<campaign_id>', methods=['PUT', 'DELETE'])
    @approver_required
    def manage_campaign(campaign_id):
        """Update or delete a campaign (marketing_admin or admin)."""
        conn = get_social_db(_db_path)
        try:
            if request.method == 'DELETE':
                conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
                conn.commit()
                return jsonify({'success': True})

            # PUT - update
            data = request.json or {}
            name = data.get('name', '').strip()
            description = data.get('description', '').strip()
            is_active = data.get('is_active', True)

            if name:
                # Check for duplicate name
                existing = conn.execute(
                    "SELECT id FROM campaigns WHERE name = ? AND id != ?", (name, campaign_id)
                ).fetchone()
                if existing:
                    return jsonify({'error': 'A campaign with this name already exists'}), 400

                conn.execute(
                    "UPDATE campaigns SET name = ?, description = ?, is_active = ? WHERE id = ?",
                    (name, description, 1 if is_active else 0, campaign_id)
                )
            else:
                conn.execute(
                    "UPDATE campaigns SET is_active = ? WHERE id = ?",
                    (1 if is_active else 0, campaign_id)
                )
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    # =========================================================================
    # FILE CATEGORY ASSIGNMENT
    # =========================================================================

    @bp.route('/files/<file_id>/categories', methods=['GET'])
    @login_required
    def get_file_categories(file_id):
        """Get programs and campaigns assigned to a file."""
        conn = get_social_db(_db_path)
        try:
            programs = conn.execute("""
                SELECT p.id, p.name FROM programs p
                JOIN file_programs fp ON p.id = fp.program_id
                WHERE fp.file_id = ?
            """, (file_id,)).fetchall()
            campaigns = conn.execute("""
                SELECT c.id, c.name FROM campaigns c
                JOIN file_campaigns fc ON c.id = fc.campaign_id
                WHERE fc.file_id = ?
            """, (file_id,)).fetchall()
            return jsonify({
                'programs': [{'id': p['id'], 'name': p['name']} for p in programs],
                'campaigns': [{'id': c['id'], 'name': c['name']} for c in campaigns]
            })
        finally:
            conn.close()

    @bp.route('/files/<file_id>/categories', methods=['PUT'])
    @approver_required
    def update_file_categories(file_id):
        """Update programs and campaigns assigned to a file."""
        import uuid
        data = request.json or {}
        program_ids = data.get('programs', [])
        campaign_ids = data.get('campaigns', [])

        conn = get_social_db(_db_path)
        try:
            now = time.time()

            # Update programs - delete old, insert new
            conn.execute("DELETE FROM file_programs WHERE file_id = ?", (file_id,))
            for program_id in program_ids:
                conn.execute(
                    "INSERT INTO file_programs (id, file_id, program_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), file_id, program_id, now, current_user.id)
                )

            # Update campaigns - delete old, insert new
            conn.execute("DELETE FROM file_campaigns WHERE file_id = ?", (file_id,))
            for campaign_id in campaign_ids:
                conn.execute(
                    "INSERT INTO file_campaigns (id, file_id, campaign_id, assigned_at, assigned_by) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), file_id, campaign_id, now, current_user.id)
                )

            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    @bp.route('/api/programs')
    @login_required
    def list_programs():
        """List all active programs (for dropdowns)."""
        conn = get_social_db(_db_path)
        try:
            programs = conn.execute(
                "SELECT id, name FROM programs WHERE is_active = 1 ORDER BY sort_order, name"
            ).fetchall()
            return jsonify([{'id': p['id'], 'name': p['name']} for p in programs])
        finally:
            conn.close()

    @bp.route('/api/campaigns')
    @login_required
    def list_campaigns():
        """List all active campaigns (for dropdowns)."""
        conn = get_social_db(_db_path)
        try:
            campaigns = conn.execute(
                "SELECT id, name FROM campaigns WHERE is_active = 1 ORDER BY sort_order, name"
            ).fetchall()
            return jsonify([{'id': c['id'], 'name': c['name']} for c in campaigns])
        finally:
            conn.close()

    @bp.route('/files/batch-categories', methods=['POST'])
    @login_required
    def get_batch_file_categories():
        """Get programs and campaigns for multiple files at once."""
        data = request.json or {}
        file_ids = data.get('file_ids', [])

        if not file_ids:
            return jsonify({})

        conn = get_social_db(_db_path)
        try:
            result = {}

            # Get all programs for requested files
            if file_ids:
                placeholders = ','.join(['?' for _ in file_ids])
                program_rows = conn.execute(f"""
                    SELECT fp.file_id, p.id, p.name FROM programs p
                    JOIN file_programs fp ON p.id = fp.program_id
                    WHERE fp.file_id IN ({placeholders})
                """, file_ids).fetchall()

                campaign_rows = conn.execute(f"""
                    SELECT fc.file_id, c.id, c.name FROM campaigns c
                    JOIN file_campaigns fc ON c.id = fc.campaign_id
                    WHERE fc.file_id IN ({placeholders})
                """, file_ids).fetchall()

                # Initialize result dict for all requested files
                for file_id in file_ids:
                    result[file_id] = {'programs': [], 'campaigns': []}

                # Populate programs
                for row in program_rows:
                    file_id = row['file_id']
                    if file_id in result:
                        result[file_id]['programs'].append({
                            'id': row['id'],
                            'name': row['name']
                        })

                # Populate campaigns
                for row in campaign_rows:
                    file_id = row['file_id']
                    if file_id in result:
                        result[file_id]['campaigns'].append({
                            'id': row['id'],
                            'name': row['name']
                        })

            return jsonify(result)
        finally:
            conn.close()

    # =========================================================================
    # DASHBOARD
    # =========================================================================

    @bp.route('/dashboard')
    @login_required
    def dashboard():
        """Post queue management dashboard."""
        status_filter = request.args.get('status', 'all')
        conn = get_social_db(_db_path)
        try:
            if status_filter == 'all':
                posts = conn.execute(
                    "SELECT p.*, u.display_name as creator_name FROM posts p "
                    "LEFT JOIN users u ON p.created_by = u.id "
                    "ORDER BY p.updated_at DESC"
                ).fetchall()
            else:
                posts = conn.execute(
                    "SELECT p.*, u.display_name as creator_name FROM posts p "
                    "LEFT JOIN users u ON p.created_by = u.id "
                    "WHERE p.status = ? ORDER BY p.updated_at DESC",
                    (status_filter,)
                ).fetchall()

            # Enrich posts with media and platform info
            enriched = []
            for post in posts:
                post_dict = dict(post)
                post_dict['media'] = conn.execute(
                    "SELECT pm.*, f.name, f.type, f.path FROM post_media pm "
                    "LEFT JOIN files f ON pm.file_id = f.id "
                    "WHERE pm.post_id = ? ORDER BY pm.sort_order",
                    (post_dict['id'],)
                ).fetchall()
                post_dict['platforms'] = conn.execute(
                    "SELECT pp.*, sa.platform, sa.account_name FROM post_platforms pp "
                    "LEFT JOIN social_accounts sa ON pp.social_account_id = sa.id "
                    "WHERE pp.post_id = ?",
                    (post_dict['id'],)
                ).fetchall()
                enriched.append(post_dict)

            # Count by status
            counts = {}
            for s in ['draft', 'pending_approval', 'approved', 'publishing', 'published', 'rejected', 'failed']:
                row = conn.execute("SELECT COUNT(*) as cnt FROM posts WHERE status = ?", (s,)).fetchone()
                counts[s] = row['cnt']
            counts['all'] = sum(counts.values())

            return render_template('social/dashboard.html', posts=enriched,
                                   status_filter=status_filter, counts=counts)
        finally:
            conn.close()

    # =========================================================================
    # POST CRUD
    # =========================================================================

    @bp.route('/compose')
    @login_required
    def compose():
        """Post compose/edit form."""
        post_id = request.args.get('post_id')
        file_ids = request.args.get('file_ids', '')

        conn = get_social_db(_db_path)
        try:
            post = None
            post_media = []
            post_platforms = []

            if post_id:
                post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
                if post:
                    post = dict(post)
                    # Check permissions - admins and marketing admins can edit any post
                    if not current_user.can_post_without_approval and post['created_by'] != current_user.id:
                        flash('You can only edit your own posts.', 'error')
                        return redirect(url_for('social.dashboard'))

                    post_media = conn.execute(
                        "SELECT pm.*, f.name, f.type, f.id as gallery_file_id FROM post_media pm "
                        "LEFT JOIN files f ON pm.file_id = f.id "
                        "WHERE pm.post_id = ? ORDER BY pm.sort_order",
                        (post_id,)
                    ).fetchall()
                    post_platforms = conn.execute(
                        "SELECT social_account_id FROM post_platforms WHERE post_id = ?",
                        (post_id,)
                    ).fetchall()

            # Get selected files from gallery (for new posts)
            selected_files = []
            if file_ids and not post_id:
                ids = [fid.strip() for fid in file_ids.split(',') if fid.strip()]
                if ids:
                    placeholders = ','.join('?' * len(ids))
                    selected_files = conn.execute(
                        f"SELECT id, name, type FROM files WHERE id IN ({placeholders})",
                        ids
                    ).fetchall()

            # Get available social accounts
            accounts = conn.execute(
                "SELECT * FROM social_accounts WHERE is_active = 1"
            ).fetchall()

            selected_account_ids = [row['social_account_id'] for row in post_platforms]

            return render_template('social/compose.html',
                                   post=post,
                                   post_media=[dict(m) for m in post_media],
                                   selected_files=[dict(f) for f in selected_files],
                                   accounts=[dict(a) for a in accounts],
                                   selected_account_ids=selected_account_ids)
        finally:
            conn.close()

    @bp.route('/posts', methods=['POST'])
    @login_required
    def create_post():
        """Create or update a post."""
        post_id = request.form.get('post_id')
        caption = request.form.get('caption', '').strip()
        hashtags = request.form.get('hashtags', '').strip()
        file_ids = request.form.getlist('file_ids')
        account_ids = request.form.getlist('account_ids')
        action = request.form.get('action', 'draft')  # draft, submit, approve_publish
        scheduled_at = request.form.get('scheduled_at', '').strip()

        now = time.time()
        conn = get_social_db(_db_path)
        try:
            if post_id:
                # Update existing post
                post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
                if not post:
                    flash('Post not found.', 'error')
                    return redirect(url_for('social.dashboard'))

                # Only creator or users with post approval permissions can edit
                if not current_user.can_post_without_approval and post['created_by'] != current_user.id:
                    flash('You can only edit your own posts.', 'error')
                    return redirect(url_for('social.dashboard'))

                # Determine new status
                status = _determine_status(action, post['status'])

                conn.execute(
                    "UPDATE posts SET caption=?, hashtags=?, status=?, scheduled_at=?, updated_at=? WHERE id=?",
                    (caption, hashtags, status,
                     _parse_schedule(scheduled_at) if scheduled_at else None,
                     now, post_id)
                )

                if action == 'approve_publish' and current_user.can_post_without_approval:
                    conn.execute("UPDATE posts SET approved_by=? WHERE id=?",
                                  (current_user.id, post_id))

            else:
                # Create new post
                post_id = str(uuid.uuid4())
                status = _determine_status(action, None)

                conn.execute(
                    "INSERT INTO posts (id, created_by, caption, hashtags, status, scheduled_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (post_id, current_user.id, caption, hashtags, status,
                     _parse_schedule(scheduled_at) if scheduled_at else None,
                     now, now)
                )

                if action == 'approve_publish' and current_user.can_post_without_approval:
                    conn.execute("UPDATE posts SET approved_by=? WHERE id=?",
                                  (current_user.id, post_id))

            # Update media associations
            conn.execute("DELETE FROM post_media WHERE post_id = ?", (post_id,))
            for i, fid in enumerate(file_ids):
                conn.execute(
                    "INSERT INTO post_media (id, post_id, file_id, sort_order) VALUES (?, ?, ?, ?)",
                    (str(uuid.uuid4()), post_id, fid, i)
                )

            # Update platform targets
            conn.execute("DELETE FROM post_platforms WHERE post_id = ?", (post_id,))
            for aid in account_ids:
                conn.execute(
                    "INSERT INTO post_platforms (id, post_id, social_account_id, status) VALUES (?, ?, ?, 'pending')",
                    (str(uuid.uuid4()), post_id, aid)
                )

            conn.commit()

            # If approve & publish immediately (no schedule)
            if action == 'approve_publish' and current_user.can_post_without_approval and not scheduled_at:
                publish_post_now(post_id, _db_path, current_app.secret_key)
                flash('Post is being published.', 'success')
            elif action == 'submit':
                flash('Post submitted for approval.', 'success')
            elif action == 'draft':
                flash('Draft saved.', 'success')
            elif action == 'approve_publish' and scheduled_at:
                flash('Post approved and scheduled.', 'success')

            return redirect(url_for('social.dashboard'))

        finally:
            conn.close()

    @bp.route('/posts/<post_id>/submit', methods=['POST'])
    @login_required
    def submit_post(post_id):
        """Submit a draft for approval."""
        conn = get_social_db(_db_path)
        try:
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            if not current_user.can_post_without_approval and post['created_by'] != current_user.id:
                return jsonify({'error': 'Permission denied'}), 403
            if post['status'] not in ('draft', 'rejected'):
                return jsonify({'error': 'Post cannot be submitted from current status'}), 400

            conn.execute("UPDATE posts SET status='pending_approval', updated_at=? WHERE id=?",
                          (time.time(), post_id))
            conn.commit()
            return jsonify({'status': 'pending_approval'})
        finally:
            conn.close()

    @bp.route('/posts/<post_id>/approve', methods=['POST'])
    @approver_required
    def approve_post(post_id):
        """Approve a post for publishing."""
        conn = get_social_db(_db_path)
        try:
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            if post['status'] != 'pending_approval':
                return jsonify({'error': 'Post is not pending approval'}), 400

            conn.execute(
                "UPDATE posts SET status='approved', approved_by=?, updated_at=? WHERE id=?",
                (current_user.id, time.time(), post_id)
            )
            conn.commit()
            return jsonify({'status': 'approved'})
        finally:
            conn.close()

    @bp.route('/posts/<post_id>/reject', methods=['POST'])
    @approver_required
    def reject_post(post_id):
        """Reject a post."""
        reason = request.json.get('reason', '') if request.is_json else request.form.get('reason', '')
        conn = get_social_db(_db_path)
        try:
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            if post['status'] != 'pending_approval':
                return jsonify({'error': 'Post is not pending approval'}), 400

            conn.execute(
                "UPDATE posts SET status='rejected', rejection_reason=?, updated_at=? WHERE id=?",
                (reason, time.time(), post_id)
            )
            conn.commit()
            return jsonify({'status': 'rejected'})
        finally:
            conn.close()

    @bp.route('/posts/<post_id>/publish', methods=['POST'])
    @approver_required
    def publish_post(post_id):
        """Immediately publish an approved post."""
        conn = get_social_db(_db_path)
        try:
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            if post['status'] not in ('approved', 'failed'):
                return jsonify({'error': 'Post must be approved or failed to publish'}), 400

            # Reset failed platform statuses for retry
            if post['status'] == 'failed':
                conn.execute(
                    "UPDATE post_platforms SET status='pending', error_message=NULL WHERE post_id=? AND status='failed'",
                    (post_id,)
                )
                conn.commit()

            publish_post_now(post_id, _db_path, current_app.secret_key)
            return jsonify({'status': 'publishing'})
        finally:
            conn.close()

    @bp.route('/posts/<post_id>', methods=['GET'])
    @login_required
    def get_post(post_id):
        """Get post details as JSON."""
        conn = get_social_db(_db_path)
        try:
            post = conn.execute(
                "SELECT p.*, u.display_name as creator_name FROM posts p "
                "LEFT JOIN users u ON p.created_by = u.id WHERE p.id = ?",
                (post_id,)
            ).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404

            post_dict = dict(post)
            post_dict['media'] = [dict(m) for m in conn.execute(
                "SELECT pm.*, f.name, f.type FROM post_media pm "
                "LEFT JOIN files f ON pm.file_id = f.id "
                "WHERE pm.post_id = ? ORDER BY pm.sort_order",
                (post_id,)
            ).fetchall()]
            post_dict['platforms'] = [dict(p) for p in conn.execute(
                "SELECT pp.*, sa.platform, sa.account_name FROM post_platforms pp "
                "LEFT JOIN social_accounts sa ON pp.social_account_id = sa.id "
                "WHERE pp.post_id = ?",
                (post_id,)
            ).fetchall()]
            return jsonify(post_dict)
        finally:
            conn.close()

    @bp.route('/posts/<post_id>', methods=['DELETE'])
    @login_required
    def delete_post(post_id):
        """Delete a post."""
        conn = get_social_db(_db_path)
        try:
            post = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                return jsonify({'error': 'Post not found'}), 404
            if not current_user.can_post_without_approval and post['created_by'] != current_user.id:
                return jsonify({'error': 'Permission denied'}), 403

            conn.execute("DELETE FROM post_platforms WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM post_media WHERE post_id = ?", (post_id,))
            conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    # =========================================================================
    # OAUTH ROUTES
    # =========================================================================

    @bp.route('/oauth/<platform>/authorize')
    @admin_required
    def oauth_authorize(platform):
        """Start OAuth flow for a platform."""
        if platform == 'facebook':
            if not facebook_available():
                flash('Facebook credentials not configured.', 'error')
                return redirect(url_for('social.settings'))
            redirect_uri = _get_external_url('social.oauth_callback', platform='facebook')
            return redirect(get_facebook_authorize_url(redirect_uri))

        elif platform == 'linkedin':
            if not linkedin_available():
                flash('LinkedIn credentials not configured.', 'error')
                return redirect(url_for('social.settings'))
            state = str(uuid.uuid4())
            session['oauth_state'] = state
            redirect_uri = _get_external_url('social.oauth_callback', platform='linkedin')
            return redirect(get_linkedin_authorize_url(redirect_uri, state))

        flash(f'Unknown platform: {platform}', 'error')
        return redirect(url_for('social.settings'))

    @bp.route('/oauth/<platform>/callback')
    @admin_required
    def oauth_callback(platform):
        """Handle OAuth callback from a platform."""
        code = request.args.get('code')
        error = request.args.get('error')

        if error:
            flash(f'OAuth error: {error}', 'error')
            return redirect(url_for('social.settings'))
        if not code:
            flash('No authorization code received.', 'error')
            return redirect(url_for('social.settings'))

        try:
            if platform == 'facebook':
                redirect_uri = _get_external_url('social.oauth_callback', platform='facebook')
                accounts = exchange_facebook_code(code, redirect_uri, current_app.secret_key)
                for account_data in accounts:
                    save_social_account(_db_path, current_user.id, account_data)
                flash(f'{len(accounts)} account(s) connected.', 'success')

            elif platform == 'linkedin':
                # Verify state
                expected_state = session.pop('oauth_state', None)
                received_state = request.args.get('state')
                if expected_state and received_state != expected_state:
                    flash('OAuth state mismatch. Please try again.', 'error')
                    return redirect(url_for('social.settings'))

                redirect_uri = _get_external_url('social.oauth_callback', platform='linkedin')
                account_data = exchange_linkedin_code(code, redirect_uri, current_app.secret_key)
                save_social_account(_db_path, current_user.id, account_data)
                flash('LinkedIn account connected.', 'success')

            else:
                flash(f'Unknown platform: {platform}', 'error')

        except Exception as e:
            flash(f'Error connecting account: {str(e)}', 'error')

        return redirect(url_for('social.settings'))

    # =========================================================================
    # SOCIAL ACCOUNTS
    # =========================================================================

    @bp.route('/settings')
    @admin_required
    def settings():
        """Settings page for connected accounts and user management."""
        conn = get_social_db(_db_path)
        try:
            accounts = conn.execute(
                "SELECT sa.*, u.display_name as connected_by FROM social_accounts sa "
                "LEFT JOIN users u ON sa.user_id = u.id "
                "ORDER BY sa.platform, sa.account_name"
            ).fetchall()

            users = conn.execute(
                "SELECT * FROM users ORDER BY created_at"
            ).fetchall()

            # SharePoint status
            from social.sharepoint import (
                sharepoint_available, SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID,
                SHAREPOINT_SITE_URL, SHAREPOINT_LIBRARY_NAME, SHAREPOINT_SYNC_INTERVAL
            )
            sp_available = sharepoint_available()
            sp_cache_dir = os.environ.get(
                'SHAREPOINT_LOCAL_CACHE_DIR', '')
            if not sp_cache_dir:
                from smartgallery import BASE_SMARTGALLERY_PATH
                sp_cache_dir = os.path.join(BASE_SMARTGALLERY_PATH, '.sharepoint_cache')
            sp_cache_count = 0
            if os.path.isdir(sp_cache_dir):
                for _root, _dirs, _files in os.walk(sp_cache_dir):
                    sp_cache_count += len(_files)

            return render_template('social/settings.html',
                                   accounts=[dict(a) for a in accounts],
                                   users=[dict(u) for u in users],
                                   fb_available=facebook_available(),
                                   li_available=linkedin_available(),
                                   sp_available=sp_available,
                                   sp_has_tenant=bool(SHAREPOINT_TENANT_ID),
                                   sp_has_client=bool(SHAREPOINT_CLIENT_ID),
                                   sp_site_url=SHAREPOINT_SITE_URL or '',
                                   sp_library=SHAREPOINT_LIBRARY_NAME or 'Documents',
                                   sp_sync_interval=SHAREPOINT_SYNC_INTERVAL,
                                   sp_cache_dir=sp_cache_dir,
                                   sp_cache_count=sp_cache_count)
        finally:
            conn.close()

    @bp.route('/accounts/<account_id>', methods=['DELETE'])
    @admin_required
    def delete_account(account_id):
        """Disconnect a social account."""
        conn = get_social_db(_db_path)
        try:
            conn.execute("UPDATE social_accounts SET is_active=0, updated_at=? WHERE id=?",
                          (time.time(), account_id))
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

    @bp.route('/users', methods=['POST'])
    @admin_required
    def create_user():
        """Create a new user."""
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        display_name = data.get('display_name', '').strip() or username
        role = data.get('role', 'employee')

        if role not in User.VALID_ROLES:
            role = 'employee'

        errors = []
        if not username or len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if not password or len(password) < 8:
            errors.append('Password must be at least 8 characters.')

        if errors:
            if request.is_json:
                return jsonify({'errors': errors}), 400
            flash(' '.join(errors), 'error')
            return redirect(url_for('social.settings'))

        try:
            User.create(username, password, display_name, role, _db_path)
            if request.is_json:
                return jsonify({'success': True})
            flash(f'User "{username}" created.', 'success')
        except sqlite3.IntegrityError:
            if request.is_json:
                return jsonify({'errors': ['Username already exists.']}), 400
            flash('Username already exists.', 'error')

        return redirect(url_for('social.settings'))

    @bp.route('/users/<user_id>', methods=['PUT'])
    @admin_required
    def update_user(user_id):
        """Update a user's role or active status."""
        data = request.get_json() if request.is_json else request.form
        conn = get_social_db(_db_path)
        try:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return jsonify({'error': 'User not found'}), 404

            updates = []
            params = []

            if 'role' in data:
                role = data['role']
                if role in User.VALID_ROLES:
                    updates.append('role = ?')
                    params.append(role)

            if 'is_active' in data:
                updates.append('is_active = ?')
                params.append(1 if data['is_active'] else 0)

            if 'display_name' in data:
                updates.append('display_name = ?')
                params.append(data['display_name'])

            if 'password' in data and data['password']:
                updates.append('password_hash = ?')
                params.append(User.hash_password(data['password']))

            if updates:
                params.append(user_id)
                conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
                conn.commit()

            return jsonify({'success': True})
        finally:
            conn.close()

    @bp.route('/users/<user_id>', methods=['DELETE'])
    @admin_required
    def delete_user(user_id):
        """Delete a user."""
        if user_id == current_user.id:
            return jsonify({'error': 'Cannot delete your own account'}), 400

        conn = get_social_db(_db_path)
        try:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()


    # =========================================================================
    # SHAREPOINT ROUTES
    # =========================================================================

    @bp.route('/sharepoint/status')
    @admin_required
    def sharepoint_status():
        """Get SharePoint integration status."""
        from social.sharepoint import sharepoint_available, SHAREPOINT_SITE_URL, SHAREPOINT_LIBRARY_NAME
        return jsonify({
            'available': sharepoint_available(),
            'site_url': SHAREPOINT_SITE_URL,
            'library': SHAREPOINT_LIBRARY_NAME,
        })

    @bp.route('/sharepoint/folders')
    @admin_required
    def sharepoint_folders():
        """List SharePoint document library folders."""
        from social.sharepoint import sharepoint_available, list_sharepoint_folders
        if not sharepoint_available():
            return jsonify({'error': 'SharePoint not configured'}), 400
        try:
            folders = list_sharepoint_folders()
            return jsonify({'folders': folders})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/sharepoint/folder-children')
    @admin_required
    def sharepoint_folder_children():
        """List immediate children of a SharePoint folder (for lazy-loading tree).

        Query params:
            path: Folder path (empty/omitted for root)

        Returns: JSON with 'folders' list containing name, path, has_children, child_count
        """
        from social.sharepoint import sharepoint_available, list_folder_children
        if not sharepoint_available():
            return jsonify({'error': 'SharePoint not configured'}), 400
        folder_path = request.args.get('path', '')
        try:
            folders = list_folder_children(folder_path)
            return jsonify({'folders': folders, 'parent_path': folder_path})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/sharepoint/files')
    @admin_required
    def sharepoint_files():
        """List SharePoint files (optionally in a sub-folder)."""
        from social.sharepoint import sharepoint_available, list_sharepoint_files
        if not sharepoint_available():
            return jsonify({'error': 'SharePoint not configured'}), 400
        folder = request.args.get('folder', '')
        try:
            files = list_sharepoint_files(folder_path=folder)
            return jsonify({'files': files, 'folder': folder})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/sharepoint/test')
    @admin_required
    def sharepoint_test():
        """Test SharePoint connection by resolving site and listing drives."""
        from social.sharepoint import (
            sharepoint_available, _get_access_token, _get_site_id, _get_drive_id,
            SHAREPOINT_SITE_URL, SHAREPOINT_LIBRARY_NAME
        )
        if not sharepoint_available():
            return jsonify({'success': False, 'error': 'SharePoint credentials not configured.'}), 400
        try:
            token = _get_access_token()
            if not token:
                return jsonify({'success': False, 'step': 'auth', 'error': 'Authentication failed. Check Tenant ID, Client ID, and Client Secret.'})
            site_id = _get_site_id()
            if not site_id:
                return jsonify({'success': False, 'step': 'site', 'error': f'Could not resolve site: {SHAREPOINT_SITE_URL}'})
            drive_id = _get_drive_id(site_id)
            if not drive_id:
                return jsonify({'success': False, 'step': 'library', 'error': f'Could not find document library: {SHAREPOINT_LIBRARY_NAME}'})
            # List drives to return available libraries
            import requests as _requests
            resp = _requests.get(
                f'https://graph.microsoft.com/v1.0/sites/{site_id}/drives',
                headers={'Authorization': f'Bearer {token}'},
                timeout=15,
            )
            libraries = []
            if resp.ok:
                libraries = [{'name': d.get('name', ''), 'id': d['id']} for d in resp.json().get('value', [])]
            return jsonify({'success': True, 'site_id': site_id, 'drive_id': drive_id, 'libraries': libraries})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @bp.route('/sharepoint/sync', methods=['POST'])
    @admin_required
    def sharepoint_sync():
        """Trigger a manual SharePoint sync of configured folders."""
        try:
            from social.sharepoint import sharepoint_available, sync_configured_folders
            from smartgallery import BASE_OUTPUT_PATH, DB_PATH
            if not sharepoint_available():
                return jsonify({'error': 'SharePoint not configured'}), 400
            results = sync_configured_folders(BASE_OUTPUT_PATH, _db_path, DB_PATH)
            if 'error' in results:
                return jsonify({'error': results['error']}), 500
            total_synced = sum(r.get('synced', 0) for r in results.values())
            return jsonify({'synced_count': total_synced, 'folders': results})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @bp.route('/sharepoint/sync-folders', methods=['GET'])
    @admin_required
    def list_sync_folders():
        """List configured SharePoint sync folders."""
        conn = get_social_db(_db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM sharepoint_sync_folders ORDER BY local_folder_name"
            ).fetchall()
            folders = []
            for row in rows:
                folders.append({
                    'id': row['id'],
                    'sp_folder_path': row['sp_folder_path'],
                    'sp_folder_name': row['sp_folder_name'],
                    'local_folder_name': row['local_folder_name'],
                    'include_subfolders': bool(row['include_subfolders']),
                    'is_enabled': bool(row['is_enabled']),
                    'last_sync_at': row['last_sync_at'],
                    'last_sync_count': row['last_sync_count'],
                })
            return jsonify({'folders': folders})
        finally:
            conn.close()

    @bp.route('/sharepoint/sync-folders', methods=['POST'])
    @admin_required
    def add_sync_folder():
        """Add a SharePoint folder to sync."""
        import uuid
        import time

        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        sp_folder_path = data.get('sp_folder_path', '').strip()
        sp_folder_name = data.get('sp_folder_name', '').strip()
        local_folder_name = data.get('local_folder_name', '').strip()
        include_subfolders = data.get('include_subfolders', True)

        if not sp_folder_name:
            return jsonify({'error': 'Folder name is required'}), 400
        if not local_folder_name:
            # Default to SharePoint folder name with SP prefix
            local_folder_name = f"SP-{sp_folder_name}"

        conn = get_social_db(_db_path)
        try:
            # Check if already configured
            existing = conn.execute(
                "SELECT id FROM sharepoint_sync_folders WHERE sp_folder_path = ?",
                (sp_folder_path,)
            ).fetchone()
            if existing:
                return jsonify({'error': 'This SharePoint folder is already configured'}), 400

            folder_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO sharepoint_sync_folders
                (id, sp_folder_path, sp_folder_name, local_folder_name, include_subfolders, is_enabled, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """, (folder_id, sp_folder_path, sp_folder_name, local_folder_name,
                  1 if include_subfolders else 0, time.time(), current_user.id))
            conn.commit()

            return jsonify({
                'success': True,
                'id': folder_id,
                'local_folder_name': local_folder_name
            })
        finally:
            conn.close()

    @bp.route('/sharepoint/sync-folders/<folder_id>', methods=['DELETE'])
    @admin_required
    def remove_sync_folder(folder_id):
        """Remove a SharePoint sync folder configuration."""
        conn = get_social_db(_db_path)
        try:
            result = conn.execute(
                "DELETE FROM sharepoint_sync_folders WHERE id = ?",
                (folder_id,)
            )
            conn.commit()
            if result.rowcount == 0:
                return jsonify({'error': 'Folder not found'}), 404
            return jsonify({'success': True})
        finally:
            conn.close()

    @bp.route('/sharepoint/sync-folders/<folder_id>', methods=['PATCH'])
    @admin_required
    def update_sync_folder(folder_id):
        """Update a SharePoint sync folder configuration."""
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        conn = get_social_db(_db_path)
        try:
            updates = []
            params = []

            if 'local_folder_name' in data:
                updates.append("local_folder_name = ?")
                params.append(data['local_folder_name'])
            if 'include_subfolders' in data:
                updates.append("include_subfolders = ?")
                params.append(1 if data['include_subfolders'] else 0)
            if 'is_enabled' in data:
                updates.append("is_enabled = ?")
                params.append(1 if data['is_enabled'] else 0)

            if not updates:
                return jsonify({'error': 'No valid fields to update'}), 400

            params.append(folder_id)
            conn.execute(
                f"UPDATE sharepoint_sync_folders SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    # =========================================================================
    # USER PREFERENCES ROUTES
    # =========================================================================

    @bp.route('/preferences', methods=['GET'])
    @login_required
    def get_preferences():
        """Get current user's preferences."""
        conn = get_social_db(_db_path)
        try:
            row = conn.execute(
                "SELECT * FROM user_preferences WHERE user_id = ?",
                (current_user.id,)
            ).fetchone()

            if row:
                import json
                return jsonify({
                    'favorite_folders': json.loads(row['favorite_folders'] or '[]'),
                    'favorite_files': json.loads(row['favorite_files'] or '[]'),
                    'starting_folder': row['starting_folder']
                })
            else:
                return jsonify({
                    'favorite_folders': [],
                    'favorite_files': [],
                    'starting_folder': None
                })
        finally:
            conn.close()

    @bp.route('/preferences', methods=['PUT'])
    @login_required
    def update_preferences():
        """Update current user's preferences."""
        data = request.get_json() if request.is_json else {}
        conn = get_social_db(_db_path)
        try:
            import json

            # Check if preferences exist
            existing = conn.execute(
                "SELECT id FROM user_preferences WHERE user_id = ?",
                (current_user.id,)
            ).fetchone()

            now = time.time()

            if existing:
                # Update existing preferences
                updates = []
                params = []

                if 'favorite_folders' in data:
                    updates.append('favorite_folders = ?')
                    params.append(json.dumps(data['favorite_folders']))

                if 'favorite_files' in data:
                    updates.append('favorite_files = ?')
                    params.append(json.dumps(data['favorite_files']))

                if 'starting_folder' in data:
                    updates.append('starting_folder = ?')
                    params.append(data['starting_folder'])

                if updates:
                    updates.append('updated_at = ?')
                    params.append(now)
                    params.append(current_user.id)
                    conn.execute(
                        f"UPDATE user_preferences SET {', '.join(updates)} WHERE user_id = ?",
                        params
                    )
            else:
                # Create new preferences
                pref_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO user_preferences (id, user_id, favorite_folders, favorite_files, starting_folder, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        pref_id,
                        current_user.id,
                        json.dumps(data.get('favorite_folders', [])),
                        json.dumps(data.get('favorite_files', [])),
                        data.get('starting_folder'),
                        now
                    )
                )

            conn.commit()
            return jsonify({'success': True})
        finally:
            conn.close()

    @bp.route('/preferences/toggle_folder_favorite', methods=['POST'])
    @login_required
    def toggle_folder_favorite():
        """Toggle a folder's favorite status for the current user."""
        data = request.get_json() if request.is_json else {}
        folder_key = data.get('folder_key')

        if not folder_key:
            return jsonify({'error': 'folder_key is required'}), 400

        conn = get_social_db(_db_path)
        try:
            import json

            row = conn.execute(
                "SELECT id, favorite_folders FROM user_preferences WHERE user_id = ?",
                (current_user.id,)
            ).fetchone()

            now = time.time()

            if row:
                favorites = json.loads(row['favorite_folders'] or '[]')
                if folder_key in favorites:
                    favorites.remove(folder_key)
                    is_favorite = False
                else:
                    favorites.append(folder_key)
                    is_favorite = True

                conn.execute(
                    "UPDATE user_preferences SET favorite_folders = ?, updated_at = ? WHERE user_id = ?",
                    (json.dumps(favorites), now, current_user.id)
                )
            else:
                # Create new preferences with this folder as favorite
                pref_id = str(uuid.uuid4())
                favorites = [folder_key]
                is_favorite = True
                conn.execute(
                    "INSERT INTO user_preferences (id, user_id, favorite_folders, updated_at) VALUES (?, ?, ?, ?)",
                    (pref_id, current_user.id, json.dumps(favorites), now)
                )

            conn.commit()
            return jsonify({'success': True, 'is_favorite': is_favorite, 'favorites': favorites})
        finally:
            conn.close()

    @bp.route('/preferences/toggle_file_favorite', methods=['POST'])
    @login_required
    def toggle_file_favorite():
        """Toggle a file's favorite status for the current user."""
        data = request.get_json() if request.is_json else {}
        file_id = data.get('file_id')

        if not file_id:
            return jsonify({'error': 'file_id is required'}), 400

        conn = get_social_db(_db_path)
        try:
            import json

            row = conn.execute(
                "SELECT id, favorite_files FROM user_preferences WHERE user_id = ?",
                (current_user.id,)
            ).fetchone()

            now = time.time()

            if row:
                favorites = json.loads(row['favorite_files'] or '[]')
                if file_id in favorites:
                    favorites.remove(file_id)
                    is_favorite = False
                else:
                    favorites.append(file_id)
                    is_favorite = True

                conn.execute(
                    "UPDATE user_preferences SET favorite_files = ?, updated_at = ? WHERE user_id = ?",
                    (json.dumps(favorites), now, current_user.id)
                )
            else:
                # Create new preferences with this file as favorite
                pref_id = str(uuid.uuid4())
                favorites = [file_id]
                is_favorite = True
                conn.execute(
                    "INSERT INTO user_preferences (id, user_id, favorite_files, updated_at) VALUES (?, ?, ?, ?)",
                    (pref_id, current_user.id, json.dumps(favorites), now)
                )

            conn.commit()
            return jsonify({'success': True, 'is_favorite': is_favorite, 'favorites': favorites})
        finally:
            conn.close()

    @bp.route('/preferences/starting_folder', methods=['POST'])
    @login_required
    def set_starting_folder():
        """Set the starting folder for the current user."""
        data = request.get_json() if request.is_json else {}
        folder_key = data.get('folder_key')  # Can be None to clear

        conn = get_social_db(_db_path)
        try:
            now = time.time()

            existing = conn.execute(
                "SELECT id FROM user_preferences WHERE user_id = ?",
                (current_user.id,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE user_preferences SET starting_folder = ?, updated_at = ? WHERE user_id = ?",
                    (folder_key, now, current_user.id)
                )
            else:
                pref_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO user_preferences (id, user_id, starting_folder, updated_at) VALUES (?, ?, ?, ?)",
                    (pref_id, current_user.id, folder_key, now)
                )

            conn.commit()
            return jsonify({'success': True, 'starting_folder': folder_key})
        finally:
            conn.close()


def _determine_status(action, current_status):
    """Determine new post status based on action."""
    if action == 'draft':
        return 'draft'
    elif action == 'submit':
        return 'pending_approval'
    elif action == 'approve_publish':
        return 'approved'
    return current_status or 'draft'


def _parse_schedule(scheduled_at_str):
    """Parse a datetime string to Unix timestamp."""
    if not scheduled_at_str:
        return None
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(scheduled_at_str)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None
