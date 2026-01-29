"""
Social media posting feature for Smart Asset Gallery.

Provides a Flask Blueprint with user authentication, post composition,
approval workflows, and publishing to Facebook, Instagram, and LinkedIn.
"""

import os
from datetime import datetime
from flask import Blueprint

SOCIAL_FEATURES_ENABLED = os.environ.get('SOCIAL_FEATURES_ENABLED', 'true').lower() == 'true'

social_bp = Blueprint('social', __name__, url_prefix='/galleryout/social',
                       template_folder='../templates/social')


def init_social(app, db_path):
    """Initialize the social features: register blueprint, set up login manager, create tables."""
    if not SOCIAL_FEATURES_ENABLED:
        return False

    from social.auth import init_login_manager
    from social.models import create_social_tables
    from social.routes import register_routes

    init_login_manager(app, db_path)
    create_social_tables(db_path)
    register_routes(social_bp, db_path)
    app.register_blueprint(social_bp)

    # Add template filters
    @app.template_filter('timestamp_to_date')
    def timestamp_to_date(timestamp):
        """Convert Unix timestamp to readable date."""
        try:
            return datetime.fromtimestamp(timestamp).strftime('%b %d, %Y')
        except (ValueError, TypeError):
            return 'Unknown'

    @app.template_filter('timestamp_to_datetime')
    def timestamp_to_datetime(timestamp):
        """Convert Unix timestamp to readable datetime."""
        try:
            return datetime.fromtimestamp(timestamp).strftime('%b %d, %Y %I:%M %p')
        except (ValueError, TypeError):
            return 'Unknown'

    return True
