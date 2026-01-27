"""
Per-platform publish functions for Facebook, Instagram, and LinkedIn.

Each function takes a post dict, media file paths, and the decrypted access token,
then publishes and returns a result dict with platform_post_id, platform_url, and status.
"""

import os
import time
import json
import mimetypes

import requests

from social.oauth import FB_GRAPH_BASE, LI_API_BASE, decrypt_token

# Maximum file sizes per platform (approximate)
FB_IMAGE_MAX = 10 * 1024 * 1024    # 10 MB
FB_VIDEO_MAX = 1024 * 1024 * 1024  # 1 GB
IG_IMAGE_MAX = 8 * 1024 * 1024     # 8 MB
LI_IMAGE_MAX = 10 * 1024 * 1024    # 10 MB


def _get_mime_type(filepath):
    mime, _ = mimetypes.guess_type(filepath)
    return mime or 'application/octet-stream'


def _is_video(filepath):
    mime = _get_mime_type(filepath)
    return mime.startswith('video/')


def _is_image(filepath):
    mime = _get_mime_type(filepath)
    return mime.startswith('image/')


def publish_to_facebook(account, post, media_paths, app_secret_key=None):
    """
    Publish a post to a Facebook Page.

    Args:
        account: social_accounts row dict
        post: posts row dict with caption, hashtags
        media_paths: list of absolute file paths
        app_secret_key: Flask SECRET_KEY for token decryption

    Returns:
        dict with keys: platform_post_id, platform_url, status, error_message
    """
    access_token = decrypt_token(account['access_token'], app_secret_key)
    page_id = account['platform_account_id']
    caption = _build_caption(post)

    try:
        if not media_paths:
            # Text-only post
            resp = requests.post(
                f'{FB_GRAPH_BASE}/{page_id}/feed',
                data={'message': caption, 'access_token': access_token},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            post_id = data.get('id', '')
            return {
                'platform_post_id': post_id,
                'platform_url': f'https://www.facebook.com/{post_id}' if post_id else '',
                'status': 'published',
                'error_message': None,
            }

        # Single image
        if len(media_paths) == 1 and _is_image(media_paths[0]):
            with open(media_paths[0], 'rb') as f:
                resp = requests.post(
                    f'{FB_GRAPH_BASE}/{page_id}/photos',
                    data={'message': caption, 'access_token': access_token},
                    files={'source': (os.path.basename(media_paths[0]), f, _get_mime_type(media_paths[0]))},
                    timeout=60,
                )
            resp.raise_for_status()
            data = resp.json()
            post_id = data.get('post_id') or data.get('id', '')
            return {
                'platform_post_id': post_id,
                'platform_url': f'https://www.facebook.com/{post_id}' if post_id else '',
                'status': 'published',
                'error_message': None,
            }

        # Single video
        if len(media_paths) == 1 and _is_video(media_paths[0]):
            with open(media_paths[0], 'rb') as f:
                resp = requests.post(
                    f'{FB_GRAPH_BASE}/{page_id}/videos',
                    data={'description': caption, 'access_token': access_token},
                    files={'source': (os.path.basename(media_paths[0]), f, _get_mime_type(media_paths[0]))},
                    timeout=300,
                )
            resp.raise_for_status()
            data = resp.json()
            video_id = data.get('id', '')
            return {
                'platform_post_id': video_id,
                'platform_url': f'https://www.facebook.com/{page_id}/videos/{video_id}' if video_id else '',
                'status': 'published',
                'error_message': None,
            }

        # Multiple images - upload as unpublished, then create multi-photo post
        attached_media = []
        for path in media_paths:
            if not _is_image(path):
                continue
            with open(path, 'rb') as f:
                resp = requests.post(
                    f'{FB_GRAPH_BASE}/{page_id}/photos',
                    data={'published': 'false', 'access_token': access_token},
                    files={'source': (os.path.basename(path), f, _get_mime_type(path))},
                    timeout=60,
                )
            resp.raise_for_status()
            photo_id = resp.json().get('id')
            if photo_id:
                attached_media.append({'media_fbid': photo_id})

        # Create the multi-photo post
        post_data = {'message': caption, 'access_token': access_token}
        for i, media in enumerate(attached_media):
            post_data[f'attached_media[{i}]'] = json.dumps(media)

        resp = requests.post(
            f'{FB_GRAPH_BASE}/{page_id}/feed',
            data=post_data,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        post_id = data.get('id', '')
        return {
            'platform_post_id': post_id,
            'platform_url': f'https://www.facebook.com/{post_id}' if post_id else '',
            'status': 'published',
            'error_message': None,
        }

    except requests.RequestException as e:
        error_msg = str(e)
        try:
            error_msg = e.response.json().get('error', {}).get('message', error_msg)
        except Exception:
            pass
        return {
            'platform_post_id': None,
            'platform_url': None,
            'status': 'failed',
            'error_message': error_msg,
        }


def publish_to_instagram(account, post, media_paths, app_secret_key=None, media_urls=None):
    """
    Publish a post to Instagram Business account via the Content Publishing API.

    Instagram requires media to be accessible via a public URL for the container API.
    `media_urls` should be a list of publicly accessible URLs corresponding to media_paths.
    If not provided, falls back to attempting direct upload (which may not work without public URLs).

    Args:
        account: social_accounts row dict
        post: posts row dict
        media_paths: list of absolute file paths (for metadata/validation)
        app_secret_key: Flask SECRET_KEY for token decryption
        media_urls: list of public URLs for the media

    Returns:
        dict with keys: platform_post_id, platform_url, status, error_message
    """
    access_token = decrypt_token(account['access_token'], app_secret_key)
    ig_user_id = account['platform_account_id']
    caption = _build_caption(post)

    try:
        if not media_urls or not media_paths:
            return {
                'platform_post_id': None,
                'platform_url': None,
                'status': 'failed',
                'error_message': 'Instagram requires publicly accessible media URLs. '
                                 'Configure a public URL for your gallery or use Facebook/LinkedIn instead.',
            }

        if len(media_urls) == 1:
            # Single media container
            container_data = {
                'access_token': access_token,
                'caption': caption,
            }

            if _is_video(media_paths[0]):
                container_data['media_type'] = 'VIDEO'
                container_data['video_url'] = media_urls[0]
            else:
                container_data['image_url'] = media_urls[0]

            resp = requests.post(
                f'{FB_GRAPH_BASE}/{ig_user_id}/media',
                data=container_data,
                timeout=30,
            )
            resp.raise_for_status()
            container_id = resp.json().get('id')

            # Wait for container to be ready (Instagram processes async)
            _wait_for_ig_container(container_id, access_token)

            # Publish
            resp = requests.post(
                f'{FB_GRAPH_BASE}/{ig_user_id}/media_publish',
                data={'creation_id': container_id, 'access_token': access_token},
                timeout=30,
            )
            resp.raise_for_status()
            media_id = resp.json().get('id', '')

            # Get permalink
            permalink = _get_ig_permalink(media_id, access_token)

            return {
                'platform_post_id': media_id,
                'platform_url': permalink,
                'status': 'published',
                'error_message': None,
            }

        # Carousel (multiple media)
        children_ids = []
        for i, url in enumerate(media_urls):
            child_data = {'access_token': access_token, 'is_carousel_item': 'true'}
            if _is_video(media_paths[i]):
                child_data['media_type'] = 'VIDEO'
                child_data['video_url'] = url
            else:
                child_data['image_url'] = url

            resp = requests.post(
                f'{FB_GRAPH_BASE}/{ig_user_id}/media',
                data=child_data,
                timeout=30,
            )
            resp.raise_for_status()
            children_ids.append(resp.json().get('id'))

        # Wait for all children
        for cid in children_ids:
            _wait_for_ig_container(cid, access_token)

        # Create carousel container
        resp = requests.post(
            f'{FB_GRAPH_BASE}/{ig_user_id}/media',
            data={
                'media_type': 'CAROUSEL',
                'caption': caption,
                'children': ','.join(children_ids),
                'access_token': access_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        carousel_id = resp.json().get('id')

        _wait_for_ig_container(carousel_id, access_token)

        # Publish carousel
        resp = requests.post(
            f'{FB_GRAPH_BASE}/{ig_user_id}/media_publish',
            data={'creation_id': carousel_id, 'access_token': access_token},
            timeout=30,
        )
        resp.raise_for_status()
        media_id = resp.json().get('id', '')
        permalink = _get_ig_permalink(media_id, access_token)

        return {
            'platform_post_id': media_id,
            'platform_url': permalink,
            'status': 'published',
            'error_message': None,
        }

    except requests.RequestException as e:
        error_msg = str(e)
        try:
            error_msg = e.response.json().get('error', {}).get('message', error_msg)
        except Exception:
            pass
        return {
            'platform_post_id': None,
            'platform_url': None,
            'status': 'failed',
            'error_message': error_msg,
        }


def _wait_for_ig_container(container_id, access_token, max_attempts=30, interval=5):
    """Wait for an Instagram media container to finish processing."""
    for _ in range(max_attempts):
        resp = requests.get(
            f'{FB_GRAPH_BASE}/{container_id}',
            params={'fields': 'status_code', 'access_token': access_token},
            timeout=15,
        )
        if resp.ok:
            status = resp.json().get('status_code')
            if status == 'FINISHED':
                return
            if status == 'ERROR':
                raise requests.RequestException(f"Instagram container {container_id} failed processing")
        time.sleep(interval)
    raise requests.RequestException(f"Instagram container {container_id} timed out")


def _get_ig_permalink(media_id, access_token):
    """Get the permalink for a published Instagram post."""
    resp = requests.get(
        f'{FB_GRAPH_BASE}/{media_id}',
        params={'fields': 'permalink', 'access_token': access_token},
        timeout=15,
    )
    if resp.ok:
        return resp.json().get('permalink', '')
    return ''


def publish_to_linkedin(account, post, media_paths, app_secret_key=None):
    """
    Publish a post to LinkedIn profile or organization.

    Args:
        account: social_accounts row dict
        post: posts row dict
        media_paths: list of absolute file paths
        app_secret_key: Flask SECRET_KEY for token decryption

    Returns:
        dict with keys: platform_post_id, platform_url, status, error_message
    """
    access_token = decrypt_token(account['access_token'], app_secret_key)
    author_id = account['platform_account_id']
    author_urn = f"urn:li:person:{author_id}"
    caption = _build_caption(post)
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
    }

    try:
        if not media_paths:
            # Text-only post
            body = {
                'author': author_urn,
                'lifecycleState': 'PUBLISHED',
                'specificContent': {
                    'com.linkedin.ugc.ShareContent': {
                        'shareCommentary': {'text': caption},
                        'shareMediaCategory': 'NONE',
                    }
                },
                'visibility': {
                    'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
                },
            }
            resp = requests.post(
                f'{LI_API_BASE}/ugcPosts',
                json=body,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            post_id = resp.json().get('id', '')
            return {
                'platform_post_id': post_id,
                'platform_url': '',
                'status': 'published',
                'error_message': None,
            }

        # Media post - register upload, upload binary, create post
        media_assets = []
        for path in media_paths:
            is_vid = _is_video(path)
            recipe = 'urn:li:digitalmediaRecipe:feedshare-video' if is_vid else 'urn:li:digitalmediaRecipe:feedshare-image'

            # Register upload
            register_body = {
                'registerUploadRequest': {
                    'recipes': [recipe],
                    'owner': author_urn,
                    'serviceRelationships': [{
                        'relationshipType': 'OWNER',
                        'identifier': 'urn:li:userGeneratedContent',
                    }],
                }
            }
            reg_resp = requests.post(
                f'{LI_API_BASE}/assets?action=registerUpload',
                json=register_body,
                headers=headers,
                timeout=30,
            )
            reg_resp.raise_for_status()
            reg_data = reg_resp.json()['value']
            upload_url = reg_data['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
            asset = reg_data['asset']

            # Binary upload
            with open(path, 'rb') as f:
                up_resp = requests.put(
                    upload_url,
                    data=f,
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': _get_mime_type(path),
                    },
                    timeout=300,
                )
            up_resp.raise_for_status()

            media_assets.append({
                'status': 'READY',
                'description': {'text': ''},
                'media': asset,
                'title': {'text': os.path.basename(path)},
            })

        # Create the post with media
        media_category = 'VIDEO' if any(_is_video(p) for p in media_paths) else 'IMAGE'
        body = {
            'author': author_urn,
            'lifecycleState': 'PUBLISHED',
            'specificContent': {
                'com.linkedin.ugc.ShareContent': {
                    'shareCommentary': {'text': caption},
                    'shareMediaCategory': media_category,
                    'media': media_assets,
                }
            },
            'visibility': {
                'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
            },
        }
        resp = requests.post(
            f'{LI_API_BASE}/ugcPosts',
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        post_id = resp.json().get('id', '')

        return {
            'platform_post_id': post_id,
            'platform_url': '',
            'status': 'published',
            'error_message': None,
        }

    except requests.RequestException as e:
        error_msg = str(e)
        try:
            error_msg = e.response.json().get('message', error_msg)
        except Exception:
            pass
        return {
            'platform_post_id': None,
            'platform_url': None,
            'status': 'failed',
            'error_message': error_msg,
        }


def _build_caption(post):
    """Build the full caption text from post caption and hashtags."""
    caption = post.get('caption', '') or ''
    hashtags = post.get('hashtags', '') or ''
    if hashtags:
        # Ensure hashtags have # prefix
        tags = []
        for tag in hashtags.split(','):
            tag = tag.strip()
            if tag and not tag.startswith('#'):
                tag = f'#{tag}'
            if tag:
                tags.append(tag)
        if tags:
            caption = f"{caption}\n\n{' '.join(tags)}" if caption else ' '.join(tags)
    return caption.strip()


# Platform dispatch
PLATFORM_PUBLISHERS = {
    'facebook': publish_to_facebook,
    'instagram': publish_to_instagram,
    'linkedin': publish_to_linkedin,
}
