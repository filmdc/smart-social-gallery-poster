"""
Email utility module for the social media posting feature.

Provides email sending functionality for:
- Registration request notifications to admins
- Password reset emails
- Registration approval/denial notifications
"""

import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email configuration from environment variables
SMTP_HOST = os.environ.get('SMTP_HOST', '')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
SMTP_FROM_EMAIL = os.environ.get('SMTP_FROM_EMAIL', SMTP_USER)
# Use SITE_NAME from environment for email sender name (with fallback to SMTP_FROM_NAME)
_DEFAULT_SITE_NAME = os.environ.get('SITE_NAME', 'Smart Asset Gallery')
SMTP_FROM_NAME = os.environ.get('SMTP_FROM_NAME', _DEFAULT_SITE_NAME)
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').lower() == 'true'

# App URL for links in emails
APP_URL = os.environ.get('APP_URL', 'http://localhost:8189')


def get_site_name():
    """Get the configured site name for email content."""
    return os.environ.get('SITE_NAME', 'Smart Asset Gallery')


def email_configured():
    """Check if email is properly configured."""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def send_email(to_email, subject, html_body, text_body=None):
    """
    Send an email using SMTP.

    Args:
        to_email: Recipient email address (or list of addresses)
        subject: Email subject line
        html_body: HTML content of the email
        text_body: Plain text fallback (optional, will strip HTML if not provided)

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    if not email_configured():
        return False, "Email not configured. Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD environment variables."

    if text_body is None:
        # Simple HTML to text conversion
        import re
        text_body = re.sub(r'<[^>]+>', '', html_body)
        text_body = re.sub(r'\s+', ' ', text_body).strip()

    # Handle single email or list
    if isinstance(to_email, str):
        to_email = [to_email]

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg['To'] = ', '.join(to_email)

    # Attach both plain text and HTML versions
    part1 = MIMEText(text_body, 'plain')
    part2 = MIMEText(html_body, 'html')
    msg.attach(part1)
    msg.attach(part2)

    try:
        if SMTP_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. Check SMTP_USER and SMTP_PASSWORD."
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        return False, f"Email error: {str(e)}"


def send_registration_request_notification(admin_emails, requester_name, requester_email, request_reason):
    """
    Send notification to admins about a new registration request.

    Args:
        admin_emails: List of admin email addresses
        requester_name: Name of the person requesting access
        requester_email: Email of the person requesting access
        request_reason: Reason provided for the access request
    """
    site_name = get_site_name()
    subject = f"[{site_name}] New Access Request from {requester_name}"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2c5282;">New Access Request</h2>
        <p>A new user has requested access to {site_name}:</p>

        <table style="border-collapse: collapse; margin: 20px 0;">
            <tr>
                <td style="padding: 8px 16px; background: #f7fafc; font-weight: bold;">Name:</td>
                <td style="padding: 8px 16px;">{requester_name}</td>
            </tr>
            <tr>
                <td style="padding: 8px 16px; background: #f7fafc; font-weight: bold;">Email:</td>
                <td style="padding: 8px 16px;">{requester_email}</td>
            </tr>
            <tr>
                <td style="padding: 8px 16px; background: #f7fafc; font-weight: bold;">Reason:</td>
                <td style="padding: 8px 16px;">{request_reason or 'No reason provided'}</td>
            </tr>
        </table>

        <p>
            <a href="{APP_URL}/galleryout/social/settings"
               style="display: inline-block; padding: 12px 24px; background: #3182ce; color: white; text-decoration: none; border-radius: 4px;">
                Review Pending Requests
            </a>
        </p>

        <p style="color: #718096; font-size: 0.9em; margin-top: 30px;">
            This request will expire in 30 days if not reviewed.
        </p>
    </body>
    </html>
    """

    return send_email(admin_emails, subject, html_body)


def send_registration_approved(user_email, user_name, login_url=None):
    """
    Send notification to user that their registration was approved.
    """
    if login_url is None:
        login_url = f"{APP_URL}/galleryout/social/login"

    site_name = get_site_name()
    subject = f"[{site_name}] Your Access Request Has Been Approved"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #38a169;">Access Approved!</h2>
        <p>Hi {user_name},</p>

        <p>Great news! Your request for access to {site_name} has been approved.</p>

        <p>You can now log in using your email address and the password you created during registration.</p>

        <p>
            <a href="{login_url}"
               style="display: inline-block; padding: 12px 24px; background: #38a169; color: white; text-decoration: none; border-radius: 4px;">
                Log In Now
            </a>
        </p>

        <p style="color: #718096; font-size: 0.9em; margin-top: 30px;">
            If you did not request this account, please ignore this email.
        </p>
    </body>
    </html>
    """

    return send_email(user_email, subject, html_body)


def send_registration_denied(user_email, user_name, reason=None):
    """
    Send notification to user that their registration was denied.
    """
    site_name = get_site_name()
    subject = f"[{site_name}] Your Access Request"

    reason_text = f"<p><strong>Reason:</strong> {reason}</p>" if reason else ""

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #e53e3e;">Access Request Not Approved</h2>
        <p>Hi {user_name},</p>

        <p>Unfortunately, your request for access to {site_name} was not approved at this time.</p>

        {reason_text}

        <p>If you believe this was a mistake or would like more information, please contact your administrator.</p>

        <p style="color: #718096; font-size: 0.9em; margin-top: 30px;">
            If you did not request an account, please ignore this email.
        </p>
    </body>
    </html>
    """

    return send_email(user_email, subject, html_body)


def send_password_reset(user_email, user_name, reset_token, expires_hours=24):
    """
    Send password reset email with token link.
    """
    reset_url = f"{APP_URL}/galleryout/social/reset-password?token={reset_token}"

    site_name = get_site_name()
    subject = f"[{site_name}] Password Reset Request"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2c5282;">Password Reset</h2>
        <p>Hi {user_name},</p>

        <p>We received a request to reset your password for {site_name}.</p>

        <p>Click the button below to set a new password:</p>

        <p>
            <a href="{reset_url}"
               style="display: inline-block; padding: 12px 24px; background: #3182ce; color: white; text-decoration: none; border-radius: 4px;">
                Reset Password
            </a>
        </p>

        <p style="color: #718096; font-size: 0.9em;">
            This link will expire in {expires_hours} hours.
        </p>

        <p style="color: #718096; font-size: 0.9em; margin-top: 30px;">
            If you didn't request this password reset, you can safely ignore this email.
            Your password will remain unchanged.
        </p>
    </body>
    </html>
    """

    return send_email(user_email, subject, html_body)
