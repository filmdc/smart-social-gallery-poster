# Smart Asset Gallery
# Asset management, viewer, and social media posting platform
# for Community Action Lehigh Valley's marketing team.
#
# Based on Smart Gallery for ComfyUI by Biagio Maffettone (MIT License)
# GitHub: https://github.com/filmdc/smart-social-gallery-poster

import os
import hashlib
import cv2
import json
import shutil
import re
import sqlite3
import time
import glob
import sys
import subprocess
import base64
import zipfile
import io
from flask import Flask, render_template, send_from_directory, abort, send_file, url_for, redirect, request, jsonify, Response
from PIL import Image, ImageSequence
import colorsys
from werkzeug.utils import secure_filename
import concurrent.futures
from tqdm import tqdm
import threading
import uuid
# Try to import tkinter for GUI dialogs, but make it optional for Docker/headless environments
try:
    import tkinter as tk
    from tkinter import messagebox
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    # tkinter not available (e.g., in Docker containers) - will fall back to console output
import urllib.request 
import secrets


# ============================================================================
# CONFIGURATION GUIDE
# ============================================================================
#
# All settings check for environment variables first. If an environment
# variable is set, its value is used. Otherwise, the fallback default is used.
#
# Example: os.environ.get('BASE_OUTPUT_PATH', '/path/to/assets')
#
# Set environment variables before running:
#   export BASE_OUTPUT_PATH="/path/to/your/assets"
#   export BASE_INPUT_PATH="/path/to/source/media"
#   python smartgallery.py
#
# Or use start_gallery.sh which exports them for you.
#
# ============================================================================


# ============================================================================
# USER CONFIGURATION
# ============================================================================
# Adjust the parameters below to customize the gallery.
# Remember: environment variables take priority over these default values.
# ============================================================================

# Path to the main media/assets folder.
BASE_OUTPUT_PATH = os.environ.get('BASE_OUTPUT_PATH', '/app/data')

# Path to source/input media folder (for reference lookups).
BASE_INPUT_PATH = os.environ.get('BASE_INPUT_PATH', '/app/data')

# Path for service folders (database, cache, zip files).
# If not specified, the assets output path will be used.
# These sub-folders won't appear in the gallery.
BASE_SMARTGALLERY_PATH = os.environ.get('BASE_SMARTGALLERY_PATH', BASE_OUTPUT_PATH)

# Path to ffprobe executable (part of ffmpeg).
# Required for video metadata extraction.
FFPROBE_MANUAL_PATH = os.environ.get('FFPROBE_MANUAL_PATH', "/usr/bin/ffprobe")

# Path to ffmpeg executable (for video processing).
FFMPEG_MANUAL_PATH = os.environ.get('FFMPEG_MANUAL_PATH', "/usr/bin/ffmpeg")

# Port on which the gallery web server will run.
SERVER_PORT = int(os.environ.get('PORT', os.environ.get('SERVER_PORT', 8189)))

# Width (in pixels) of the generated thumbnails.
THUMBNAIL_WIDTH = int(os.environ.get('THUMBNAIL_WIDTH', 300))

# Thumbnail format: 'webp' (smaller files) or 'jpeg' (faster, more compatible)
THUMBNAIL_FORMAT = os.environ.get('THUMBNAIL_FORMAT', 'webp').lower()
if THUMBNAIL_FORMAT not in ('webp', 'jpeg', 'jpg'):
    THUMBNAIL_FORMAT = 'webp'

# Thumbnail quality (1-100). Lower = smaller files, less quality.
# Recommended: 60-75 for webp, 70-85 for jpeg
THUMBNAIL_QUALITY = int(os.environ.get('THUMBNAIL_QUALITY', 70 if THUMBNAIL_FORMAT == 'webp' else 80))

# Assumed frame rate for animated WebP files.
WEBP_ANIMATED_FPS = float(os.environ.get('WEBP_ANIMATED_FPS', 16.0))

# ZIP compression level (0-9). Higher = smaller files but slower.
# 0 = store only (no compression), 6 = default, 9 = maximum compression
ZIP_COMPRESSION_LEVEL = int(os.environ.get('ZIP_COMPRESSION_LEVEL', 6))
if ZIP_COMPRESSION_LEVEL < 0:
    ZIP_COMPRESSION_LEVEL = 0
elif ZIP_COMPRESSION_LEVEL > 9:
    ZIP_COMPRESSION_LEVEL = 9

# Maximum number of files to load initially before showing a "Load more" button.  
# Use a very large number (e.g., 9999999) for "infinite" loading.
PAGE_SIZE = int(os.environ.get('PAGE_SIZE', 100))

# Names of special folders (e.g., 'video', 'audio').  
# These folders will appear in the menu only if they exist inside BASE_OUTPUT_PATH.  
# Leave as-is if unsure.
SPECIAL_FOLDERS = ['video', 'audio']

# Number of files to process at once during database sync. 
# Higher values use more memory but may be faster. 
# Lower this if you run out of memory.
BATCH_SIZE = int(os.environ.get('BATCH_SIZE', 500))

# Number of parallel processes to use for thumbnail and metadata generation.
# - None or empty string: use all available CPU cores (fastest, recommended)
# - 1: disable parallel processing (slowest, like in previous versions)
# - Specific number (e.g., 4): limit CPU usage on multi-core machines
MAX_PARALLEL_WORKERS = os.environ.get('MAX_PARALLEL_WORKERS', None)
if MAX_PARALLEL_WORKERS is not None and MAX_PARALLEL_WORKERS != "":
    MAX_PARALLEL_WORKERS = int(MAX_PARALLEL_WORKERS)
else:
    MAX_PARALLEL_WORKERS = None

# Flask secret key
# You can set it in the environment variable SECRET_KEY
# If not set, it will be generated randomly
SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Optional path where deleted files will be moved instead of being permanently deleted.
# If set, files will be moved to DELETE_TO/SmartAssetGallery/<timestamp>_<filename>
# If not set (None or empty string), files will be permanently deleted as before.
# The path MUST exist and be writable, or the application will exit with an error.
# Example: /path/to/trash or C:/Trash
DELETE_TO = os.environ.get('DELETE_TO', None)

# External workflow tool URL (e.g., ComfyUI). Used for "send to" feature.
COMFYUI_URL = os.environ.get('COMFYUI_URL', 'http://127.0.0.1:8188')
if DELETE_TO and DELETE_TO.strip():
    DELETE_TO = DELETE_TO.strip()
    TRASH_FOLDER = os.path.join(DELETE_TO, 'SmartAssetGallery')
    
    # Validate that DELETE_TO path exists
    if not os.path.exists(DELETE_TO):
        print(f"{Colors.RED}{Colors.BOLD}CRITICAL ERROR: DELETE_TO path does not exist: {DELETE_TO}{Colors.RESET}")
        print(f"{Colors.RED}Please create the directory or unset the DELETE_TO environment variable.{Colors.RESET}")
        sys.exit(1)
    
    # Validate that DELETE_TO is writable
    if not os.access(DELETE_TO, os.W_OK):
        print(f"{Colors.RED}{Colors.BOLD}CRITICAL ERROR: DELETE_TO path is not writable: {DELETE_TO}{Colors.RESET}")
        print(f"{Colors.RED}Please check permissions or unset the DELETE_TO environment variable.{Colors.RESET}")
        sys.exit(1)
    
    # Validate that trash subfolder exists or can be created
    if not os.path.exists(TRASH_FOLDER):
        try:
            os.makedirs(TRASH_FOLDER)
            print(f"{Colors.GREEN}Created trash folder: {TRASH_FOLDER}{Colors.RESET}")
        except OSError as e:
            print(f"{Colors.RED}{Colors.BOLD}CRITICAL ERROR: Cannot create trash folder: {TRASH_FOLDER}{Colors.RESET}")
            print(f"{Colors.RED}Error: {e}{Colors.RESET}")
            sys.exit(1)
else:
    DELETE_TO = None
    TRASH_FOLDER = None


# ============================================================================
# END OF USER CONFIGURATION
# ============================================================================


# --- CACHE AND FOLDER NAMES ---
THUMBNAIL_CACHE_FOLDER_NAME = '.thumbnails_cache'
SQLITE_CACHE_FOLDER_NAME = '.sqlite_cache'
DATABASE_FILENAME = 'gallery_cache.sqlite'
ZIP_CACHE_FOLDER_NAME = '.zip_downloads'
SMASHCUT_FOLDER_NAME = '.smashcut_output'  

# --- APP INFO ---
APP_VERSION = 2.0
APP_VERSION_DATE = "January 2026"
GITHUB_REPO_URL = "https://github.com/filmdc/smart-social-gallery-poster"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/filmdc/smart-social-gallery-poster/main/smartgallery.py"


# --- HELPER FUNCTIONS (DEFINED FIRST) ---
def path_to_key(relative_path):
    if not relative_path: return '_root_'
    return base64.urlsafe_b64encode(relative_path.replace(os.sep, '/').encode()).decode()

def key_to_path(key):
    if key == '_root_': return ''
    try:
        return base64.urlsafe_b64decode(key.encode()).decode().replace('/', os.sep)
    except Exception: return None

# --- DERIVED SETTINGS ---
DB_SCHEMA_VERSION = 28
THUMBNAIL_CACHE_DIR = os.path.join(BASE_SMARTGALLERY_PATH, THUMBNAIL_CACHE_FOLDER_NAME)
SQLITE_CACHE_DIR = os.path.join(BASE_SMARTGALLERY_PATH, SQLITE_CACHE_FOLDER_NAME)
DATABASE_FILE = os.path.join(SQLITE_CACHE_DIR, DATABASE_FILENAME)
ZIP_CACHE_DIR = os.path.join(BASE_SMARTGALLERY_PATH, ZIP_CACHE_FOLDER_NAME)
SMASHCUT_OUTPUT_DIR = os.path.join(BASE_SMARTGALLERY_PATH, SMASHCUT_FOLDER_NAME)
PROTECTED_FOLDER_KEYS = {path_to_key(f) for f in SPECIAL_FOLDERS}
PROTECTED_FOLDER_KEYS.add('_root_')


# --- CONSOLE STYLING ---
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_configuration():
    """Prints the current configuration in a neat, aligned table."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}--- CURRENT CONFIGURATION ---{Colors.RESET}")
    
    # Helper for aligned printing
    def print_row(key, value, is_path=False):
        color = Colors.CYAN if is_path else Colors.GREEN
        print(f" {Colors.BOLD}{key:<25}{Colors.RESET} : {color}{value}{Colors.RESET}")

    print_row("Server Port", SERVER_PORT)
    print_row("Base Output Path", BASE_OUTPUT_PATH, True)
    print_row("Base Input Path", BASE_INPUT_PATH, True)
    print_row("Cache/Data Path", BASE_SMARTGALLERY_PATH, True)
    print_row("FFprobe Path", FFPROBE_MANUAL_PATH, True)
    print_row("Delete To (Trash)", DELETE_TO if DELETE_TO else "Disabled (Permanent Delete)", DELETE_TO is not None)
    print_row("Workflow Tool URL", COMFYUI_URL, True)
    print_row("Thumbnail Width", f"{THUMBNAIL_WIDTH}px")
    print_row("WebP Animated FPS", WEBP_ANIMATED_FPS)
    print_row("Page Size", PAGE_SIZE)
    print_row("Batch Size", BATCH_SIZE)
    print_row("Max Parallel Workers", MAX_PARALLEL_WORKERS if MAX_PARALLEL_WORKERS else "All Cores")
    print(f"{Colors.HEADER}-----------------------------{Colors.RESET}\n")

# --- SOCIAL FEATURES ---
SOCIAL_FEATURES_ENABLED = os.environ.get('SOCIAL_FEATURES_ENABLED', 'true').lower() == 'true'

# --- FLASK APP INITIALIZATION ---
app = Flask(__name__)
app.secret_key = SECRET_KEY
gallery_view_cache = []
folder_config_cache = None
FFPROBE_EXECUTABLE_PATH = None


# Data structures for node categorization and analysis
NODE_CATEGORIES_ORDER = ["input", "model", "processing", "output", "others"]
NODE_CATEGORIES = {
    "Load Checkpoint": "input", "CheckpointLoaderSimple": "input", "Empty Latent Image": "input",
    "CLIPTextEncode": "input", "Load Image": "input",
    "ModelMerger": "model",
    "KSampler": "processing", "KSamplerAdvanced": "processing", "VAEDecode": "processing",
    "VAEEncode": "processing", "LatentUpscale": "processing", "ConditioningCombine": "processing",
    "PreviewImage": "output", "SaveImage": "output",
     "LoadImageOutput": "input"
}
NODE_PARAM_NAMES = {
    "CLIPTextEncode": ["text"],
    "KSampler": ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "KSamplerAdvanced": ["add_noise", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise"],
    "Load Checkpoint": ["ckpt_name"],
    "CheckpointLoaderSimple": ["ckpt_name"],
    "Empty Latent Image": ["width", "height", "batch_size"],
    "LatentUpscale": ["upscale_method", "width", "height"],
    "SaveImage": ["filename_prefix"],
    "ModelMerger": ["ckpt_name1", "ckpt_name2", "ratio"],
    "Load Image": ["image"],
    "LoadImageMask": ["image"],
    "VHS_LoadVideo": ["video"],
    "LoadAudio": ["audio"],
    "AudioLoader": ["audio"],
    "LoadImageOutput": ["image"],
    # LoRA loader nodes
    "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
    "LoraLoaderModelOnly": ["lora_name", "strength_model"],
    "Load LoRA": ["lora_name"],
}

# Cache for node colors
_node_colors_cache = {}

def get_node_color(node_type):
    """Generates a unique and consistent color for a node type."""
    if node_type not in _node_colors_cache:
        # Use a hash to get a consistent color for the same node type
        hue = (hash(node_type + "a_salt_string") % 360) / 360.0
        rgb = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.7, 0.85)]
        _node_colors_cache[node_type] = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return _node_colors_cache[node_type]

def filter_enabled_nodes(workflow_data):
    """Filters and returns only active nodes and links (mode=0) from a workflow."""
    if not isinstance(workflow_data, dict): return {'nodes': [], 'links': []}
    
    active_nodes = [n for n in workflow_data.get("nodes", []) if n.get("mode", 0) == 0]
    active_node_ids = {str(n["id"]) for n in active_nodes}
    
    active_links = [
        l for l in workflow_data.get("links", [])
        if str(l[1]) in active_node_ids and str(l[3]) in active_node_ids
    ]
    return {"nodes": active_nodes, "links": active_links}

def generate_node_summary(workflow_json_string):
    """
    Analyzes a workflow JSON, extracts active nodes, and identifies input media.
    Robust version: handles workflow tool suffixes like ' [output]'.
    """
    try:
        workflow_data = json.loads(workflow_json_string)
    except json.JSONDecodeError:
        return None

    nodes = []
    is_api_format = False

    if 'nodes' in workflow_data and isinstance(workflow_data['nodes'], list):
        active_workflow = filter_enabled_nodes(workflow_data)
        nodes = active_workflow.get('nodes', [])
    else:
        is_api_format = True
        for node_id, node_data in workflow_data.items():
            if isinstance(node_data, dict) and 'class_type' in node_data:
                node_entry = node_data.copy()
                node_entry['id'] = node_id
                node_entry['type'] = node_data['class_type']
                node_entry['inputs'] = node_data.get('inputs', {})
                nodes.append(node_entry)

    if not nodes:
        return []

    def get_id_safe(n):
        try: return int(n.get('id', 0))
        except: return str(n.get('id', 0))

    sorted_nodes = sorted(nodes, key=lambda n: (
        NODE_CATEGORIES_ORDER.index(NODE_CATEGORIES.get(n.get('type'), 'others')),
        get_id_safe(n)
    ))
    
    summary_list = []
    
    valid_media_exts = {
        '.png', '.jpg', '.jpeg', '.webp', '.gif', '.jfif', '.bmp', '.tiff',
        '.mp4', '.mov', '.webm', '.mkv', '.avi',
        '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'
    }

    base_input_norm = os.path.normpath(BASE_INPUT_PATH)

    for node in sorted_nodes:
        node_type = node.get('type', 'Unknown')
        params_list = []
        
        raw_params = {}
        if is_api_format:
            raw_params = node.get('inputs', {})
        else:
            widgets_values = node.get('widgets_values', [])
            param_names_list = NODE_PARAM_NAMES.get(node_type, [])
            for i, value in enumerate(widgets_values):
                name = param_names_list[i] if i < len(param_names_list) else f"param_{i+1}"
                raw_params[name] = value

        for name, value in raw_params.items():
            display_value = value
            is_input_file = False
            input_url = None
            
            if isinstance(value, list):
                if len(value) == 2 and isinstance(value[0], str):
                     display_value = f"(Link to {value[0]})"
                else:
                     display_value = str(value)
            
            if isinstance(value, str) and value.strip():
                # 1. Pulizia aggressiva per rimuovere suffissi tipo " [output]" o " [input]"
                clean_value = value.replace('\\', '/').strip()
                # Rimuovi suffissi comuni tra parentesi quadre alla fine della stringa
                clean_value = re.sub(r'\s*\[.*?\]$', '', clean_value)
                
                _, ext = os.path.splitext(clean_value)
                
                if ext.lower() in valid_media_exts:
                    filename_only = os.path.basename(clean_value)
                    
                    candidates = [
                        os.path.join(BASE_INPUT_PATH, clean_value),
                        os.path.join(BASE_INPUT_PATH, filename_only),
                        os.path.normpath(os.path.join(BASE_INPUT_PATH, clean_value))
                    ]

                    for candidate_path in candidates:
                        try:
                            if os.path.isfile(candidate_path):
                                abs_candidate = os.path.abspath(candidate_path)
                                abs_base = os.path.abspath(BASE_INPUT_PATH)
                                
                                if abs_candidate.startswith(abs_base):
                                    is_input_file = True
                                    rel_path = os.path.relpath(abs_candidate, abs_base).replace('\\', '/')
                                    input_url = f"/galleryout/input_file/{rel_path}"
                                    # Aggiorniamo anche il valore mostrato a video per pulirlo
                                    display_value = clean_value 
                                    break 
                        except Exception:
                            continue

            params_list.append({
                "name": name, 
                "value": display_value,
                "is_input_file": is_input_file,
                "input_url": input_url
            })

        summary_list.append({
            "id": node.get('id', 'N/A'),
            "type": node_type,
            "category": NODE_CATEGORIES.get(node_type, 'others'),
            "color": get_node_color(node_type),
            "params": params_list
        })
        
    return summary_list

def extract_models_and_loras(workflow_json_string):
    """
    Extracts checkpoint model names and LoRA names from a workflow JSON.
    Returns a tuple of (models_list, loras_list) with deduplicated, sorted entries.
    """
    models = set()
    loras = set()

    # Node types that contain model/checkpoint names
    CHECKPOINT_NODES = {
        "CheckpointLoaderSimple": ["ckpt_name"],
        "Load Checkpoint": ["ckpt_name"],
        "CheckpointLoader": ["ckpt_name"],
        "ModelMerger": ["ckpt_name1", "ckpt_name2"],
        "UNETLoader": ["unet_name"],
        "DiffusersLoader": ["model_path"],
    }

    # Node types that contain LoRA names
    LORA_NODES = {
        "LoraLoader": ["lora_name"],
        "LoraLoaderModelOnly": ["lora_name"],
        "Load LoRA": ["lora_name"],
        "Lora Loader": ["lora_name"],
        "LoRALoader": ["lora_name"],
        "LoraLoaderBlockWeight": ["lora_name"],
    }

    try:
        workflow_data = json.loads(workflow_json_string)
    except (json.JSONDecodeError, TypeError):
        return ([], [])

    nodes = []
    is_api_format = False

    # Handle UI format (has 'nodes' array)
    if 'nodes' in workflow_data and isinstance(workflow_data['nodes'], list):
        active_workflow = filter_enabled_nodes(workflow_data)
        nodes = active_workflow.get('nodes', [])
    else:
        # API format (dict of node_id -> node_data)
        is_api_format = True
        for node_id, node_data in workflow_data.items():
            if isinstance(node_data, dict) and 'class_type' in node_data:
                node_entry = node_data.copy()
                node_entry['type'] = node_data['class_type']
                nodes.append(node_entry)

    for node in nodes:
        node_type = node.get('type', node.get('class_type', ''))

        # Get inputs based on format
        if is_api_format:
            inputs = node.get('inputs', {})
        else:
            # UI format - need to map widgets_values to param names
            widgets_values = node.get('widgets_values', [])
            param_names = CHECKPOINT_NODES.get(node_type, []) or LORA_NODES.get(node_type, [])
            inputs = {}
            for i, value in enumerate(widgets_values):
                if i < len(param_names):
                    inputs[param_names[i]] = value

        # Extract checkpoint models
        if node_type in CHECKPOINT_NODES:
            for param in CHECKPOINT_NODES[node_type]:
                value = inputs.get(param)
                if value and isinstance(value, str) and value.strip():
                    # Get just the filename, handling path separators
                    model_name = value.replace('\\', '/').split('/')[-1].strip()
                    if model_name:
                        models.add(model_name)

        # Extract LoRAs
        if node_type in LORA_NODES:
            for param in LORA_NODES[node_type]:
                value = inputs.get(param)
                if value and isinstance(value, str) and value.strip():
                    lora_name = value.replace('\\', '/').split('/')[-1].strip()
                    if lora_name:
                        loras.add(lora_name)

    return (sorted(list(models)), sorted(list(loras)))

def extract_input_files_from_workflow(workflow_json_string):
    """
    Extracts input media file references from workflow JSON.
    Scans ALL parameters from ALL nodes for media file references.
    Returns list of unique input file names.
    """
    input_files = set()

    # Valid media extensions to look for
    valid_media_exts = {
        '.png', '.jpg', '.jpeg', '.webp', '.gif', '.jfif', '.bmp', '.tiff',
        '.mp4', '.mov', '.webm', '.mkv', '.avi',
        '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'
    }

    try:
        workflow_data = json.loads(workflow_json_string)
    except (json.JSONDecodeError, TypeError):
        return []

    nodes = []
    is_api_format = False

    # Handle UI format (has 'nodes' array)
    if 'nodes' in workflow_data and isinstance(workflow_data['nodes'], list):
        active_workflow = filter_enabled_nodes(workflow_data)
        nodes = active_workflow.get('nodes', [])
    else:
        # API format (dict of node_id -> node_data)
        is_api_format = True
        for node_id, node_data in workflow_data.items():
            if isinstance(node_data, dict) and 'class_type' in node_data:
                node_entry = node_data.copy()
                node_entry['type'] = node_data['class_type']
                node_entry['inputs'] = node_data.get('inputs', {})
                nodes.append(node_entry)

    for node in nodes:
        node_type = node.get('type', node.get('class_type', ''))

        # Get all parameters based on format
        raw_params = {}
        if is_api_format:
            raw_params = node.get('inputs', {})
        else:
            # UI format - get from widgets_values
            widgets_values = node.get('widgets_values', [])
            param_names_list = NODE_PARAM_NAMES.get(node_type, [])
            for i, value in enumerate(widgets_values):
                name = param_names_list[i] if i < len(param_names_list) else f"param_{i+1}"
                raw_params[name] = value

        # Scan all string parameters for media file references
        for name, value in raw_params.items():
            if isinstance(value, str) and value.strip():
                # Clean up value (remove [output] suffixes, normalize path)
                clean_value = value.replace('\\', '/').strip()
                clean_value = re.sub(r'\s*\[.*?\]$', '', clean_value)

                _, ext = os.path.splitext(clean_value)

                if ext.lower() in valid_media_exts:
                    filename = os.path.basename(clean_value)
                    if filename:
                        input_files.add(filename)

    return sorted(list(input_files))

# --- ALL UTILITY AND HELPER FUNCTIONS ARE DEFINED HERE, BEFORE ANY ROUTES ---

def safe_delete_file(filepath):
    """
    Safely delete a file by either moving it to trash (if DELETE_TO is configured)
    or permanently deleting it.
    
    Args:
        filepath: Path to the file to delete
        
    Raises:
        OSError: If deletion/move fails
    """
    if DELETE_TO and TRASH_FOLDER:
        # Move to trash (folder already validated at startup)
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        filename = os.path.basename(filepath)
        trash_filename = f"{timestamp}_{filename}"
        trash_path = os.path.join(TRASH_FOLDER, trash_filename)
        
        # Handle duplicate filenames in trash
        counter = 1
        while os.path.exists(trash_path):
            name_without_ext, ext = os.path.splitext(filename)
            trash_filename = f"{timestamp}_{name_without_ext}_{counter}{ext}"
            trash_path = os.path.join(TRASH_FOLDER, trash_filename)
            counter += 1
        
        shutil.move(filepath, trash_path)
        print(f"INFO: Moved file to trash: {trash_path}")
    else:
        # Permanently delete
        os.remove(filepath)

def find_ffprobe_path():
    if FFPROBE_MANUAL_PATH and os.path.isfile(FFPROBE_MANUAL_PATH):
        try:
            subprocess.run([FFPROBE_MANUAL_PATH, "-version"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            return FFPROBE_MANUAL_PATH
        except Exception: pass
    base_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    try:
        subprocess.run([base_name, "-version"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return base_name
    except Exception: pass
    print("WARNING: ffprobe not found. Video metadata analysis will be disabled.")
    return None

def find_ffmpeg_path():
    """Finds the ffmpeg executable path for video concatenation."""
    if FFMPEG_MANUAL_PATH and os.path.isfile(FFMPEG_MANUAL_PATH):
        try:
            subprocess.run([FFMPEG_MANUAL_PATH, "-version"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            return FFMPEG_MANUAL_PATH
        except Exception: pass
    base_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    try:
        subprocess.run([base_name, "-version"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return base_name
    except Exception: pass
    return None

def _validate_and_get_workflow(json_string):
    try:
        data = json.loads(json_string)

        # Try to get workflow data, handling nested string JSON
        workflow_data = None

        # First try 'workflow' key (UI format is often here)
        if 'workflow' in data:
            wf = data['workflow']
            if isinstance(wf, str):
                try:
                    wf = json.loads(wf)
                except:
                    pass
            if isinstance(wf, dict):
                workflow_data = wf

        # If no workflow yet, try 'prompt' key (API format is often here)
        if workflow_data is None and 'prompt' in data:
            prompt = data['prompt']
            if isinstance(prompt, str):
                try:
                    prompt = json.loads(prompt)
                except:
                    pass
            if isinstance(prompt, dict):
                workflow_data = prompt

        # Fallback to the data itself
        if workflow_data is None:
            workflow_data = data

        if isinstance(workflow_data, dict):
            if 'nodes' in workflow_data:
                return json.dumps(workflow_data), 'ui'

            # Check for API format (keys are IDs, values have class_type)
            # Heuristic: Check if it looks like a dict of nodes
            is_api = False
            for k, v in workflow_data.items():
                if isinstance(v, dict) and 'class_type' in v:
                    is_api = True
                    break
            if is_api:
                return json.dumps(workflow_data), 'api'

    except Exception:
        pass

    return None, None

def _scan_bytes_for_workflow(content_bytes):
    """
    Generator that yields all valid JSON objects found in the byte stream.
    Searches for matching curly braces.
    """
    try:
        stream_str = content_bytes.decode('utf-8', errors='ignore')
    except Exception:
        return

    start_pos = 0
    while True:
        first_brace = stream_str.find('{', start_pos)
        if first_brace == -1:
            break
        
        open_braces = 0
        start_index = first_brace
        
        for i in range(start_index, len(stream_str)):
            char = stream_str[i]
            if char == '{':
                open_braces += 1
            elif char == '}':
                open_braces -= 1
            
            if open_braces == 0:
                candidate = stream_str[start_index : i + 1]
                try:
                    # Verify it's valid JSON
                    json.loads(candidate)
                    yield candidate
                except json.JSONDecodeError:
                    pass
                
                # Move start_pos to after this candidate to find the next one
                start_pos = i + 1
                break
        else:
            # If loop finishes without open_braces hitting 0, no more valid JSON here
            break

def extract_workflow(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    video_exts = ['.mp4', '.mkv', '.webm', '.mov', '.avi']
    
    best_workflow = None
    
    def update_best(wf, wf_type):
        nonlocal best_workflow
        if wf_type == 'ui':
            best_workflow = wf
            return True # Found best, stop searching
        if wf_type == 'api' and best_workflow is None:
            best_workflow = wf
        return False

    if ext in video_exts:
        # --- FIX: Risoluzione del path anche nei processi Worker ---
        # Se la variabile globale è vuota (succede nel multiprocessing), la cerchiamo ora.
        current_ffprobe_path = FFPROBE_EXECUTABLE_PATH
        if not current_ffprobe_path:
             current_ffprobe_path = find_ffprobe_path()
        # -----------------------------------------------------------

        if current_ffprobe_path:
            try:
                # Usiamo current_ffprobe_path invece della globale
                cmd = [current_ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore', check=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                data = json.loads(result.stdout)
                if 'format' in data and 'tags' in data['format']:
                    for value in data['format']['tags'].values():
                        if isinstance(value, str) and value.strip().startswith('{'):
                            wf, wf_type = _validate_and_get_workflow(value)
                            if wf:
                                if update_best(wf, wf_type): return best_workflow
            except Exception: pass
    else:
        try:
            with Image.open(filepath) as img:
                # Check standard keys first
                for key in ['workflow', 'prompt']:
                    val = img.info.get(key)
                    if val:
                        wf, wf_type = _validate_and_get_workflow(val)
                        if wf:
                            if update_best(wf, wf_type): return best_workflow

                exif_data = img.info.get('exif')
                if exif_data and isinstance(exif_data, bytes):
                    # Check for "workflow:" prefix which some tools use
                    try:
                        exif_str = exif_data.decode('utf-8', errors='ignore')
                        if 'workflow:{' in exif_str:
                            # Extract the JSON part after "workflow:"
                            start = exif_str.find('workflow:{') + len('workflow:')
                            # Try to parse this specific part first
                            for json_candidate in _scan_bytes_for_workflow(exif_str[start:].encode('utf-8')):
                                wf, wf_type = _validate_and_get_workflow(json_candidate)
                                if wf:
                                    if update_best(wf, wf_type): return best_workflow
                                    break 
                    except Exception: pass
                    
                    # Fallback to standard scan of the entire exif_data if not already returned
                    if best_workflow is None:
                        for json_str in _scan_bytes_for_workflow(exif_data):
                            wf, wf_type = _validate_and_get_workflow(json_str)
                            if wf:
                                if update_best(wf, wf_type): return best_workflow
        except Exception: pass

    # Raw byte scan (fallback for any file type)
    try:
        with open(filepath, 'rb') as f:
            content = f.read()
        for json_str in _scan_bytes_for_workflow(content):
            wf, wf_type = _validate_and_get_workflow(json_str)
            if wf:
                if update_best(wf, wf_type): return best_workflow
    except Exception: pass
                
    return best_workflow
    
def is_webp_animated(filepath):
    try:
        with Image.open(filepath) as img: return getattr(img, 'is_animated', False)
    except: return False

def format_duration(seconds):
    if not seconds or seconds < 0: return ""
    m, s = divmod(int(seconds), 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def extract_media_created_date(filepath, file_type):
    """
    Extract the original creation date from media files.
    For images: reads EXIF DateTimeOriginal or DateTimeDigitized
    For videos: reads creation_time from ffprobe metadata
    Returns: Unix timestamp (float) or None if not found
    """
    from datetime import datetime
    ext_lower = os.path.splitext(filepath)[1].lower()

    # For images, try to get EXIF data
    if file_type in ['image', 'animated_image']:
        try:
            with Image.open(filepath) as img:
                exif = img._getexif() if hasattr(img, '_getexif') else None
                if exif:
                    # EXIF tags: 36867 = DateTimeOriginal, 36868 = DateTimeDigitized, 306 = DateTime
                    for tag_id in [36867, 36868, 306]:
                        if tag_id in exif:
                            date_str = exif[tag_id]
                            if date_str:
                                try:
                                    # EXIF format: "YYYY:MM:DD HH:MM:SS"
                                    dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                                    return dt.timestamp()
                                except ValueError:
                                    pass
        except Exception:
            pass

    # For videos, use ffprobe to get creation_time
    elif file_type == 'video':
        current_ffprobe_path = FFPROBE_EXECUTABLE_PATH
        if not current_ffprobe_path:
            current_ffprobe_path = find_ffprobe_path()

        if current_ffprobe_path:
            try:
                cmd = [current_ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore', check=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                data = json.loads(result.stdout)
                if 'format' in data and 'tags' in data['format']:
                    tags = data['format']['tags']
                    # Try common creation time tags
                    for key in ['creation_time', 'date', 'com.apple.quicktime.creationdate']:
                        if key in tags:
                            date_str = tags[key]
                            # Try various date formats
                            for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                                try:
                                    dt = datetime.strptime(date_str.split('+')[0].split('.')[0] + ('' if 'T' not in fmt else ''),
                                                          fmt.replace('.%f', '').replace('Z', ''))
                                    return dt.timestamp()
                                except ValueError:
                                    continue
                            # Try parsing ISO format directly
                            try:
                                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                return dt.timestamp()
                            except ValueError:
                                pass
            except Exception:
                pass

    return None


def analyze_file_metadata(filepath):
    details = {'type': 'unknown', 'duration': '', 'dimensions': '', 'has_workflow': 0, 'media_created_at': None}
    ext_lower = os.path.splitext(filepath)[1].lower()
    type_map = {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'animated_image', '.mp4': 'video', '.webm': 'video', '.mov': 'video', '.mp3': 'audio', '.wav': 'audio', '.ogg': 'audio', '.flac': 'audio'}
    details['type'] = type_map.get(ext_lower, 'unknown')
    if details['type'] == 'unknown' and ext_lower == '.webp': details['type'] = 'animated_image' if is_webp_animated(filepath) else 'image'
    if 'image' in details['type']:
        try:
            with Image.open(filepath) as img: details['dimensions'] = f"{img.width}x{img.height}"
        except Exception: pass
    if extract_workflow(filepath): details['has_workflow'] = 1
    total_duration_sec = 0
    if details['type'] == 'video':
        try:
            cap = cv2.VideoCapture(filepath)
            if cap.isOpened():
                fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if fps > 0 and count > 0: total_duration_sec = count / fps
                details['dimensions'] = f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
                cap.release()
        except Exception: pass
    elif details['type'] == 'animated_image':
        try:
            with Image.open(filepath) as img:
                if getattr(img, 'is_animated', False):
                    if ext_lower == '.gif': total_duration_sec = sum(frame.info.get('duration', 100) for frame in ImageSequence.Iterator(img)) / 1000
                    elif ext_lower == '.webp': total_duration_sec = getattr(img, 'n_frames', 1) / WEBP_ANIMATED_FPS
        except Exception: pass
    if total_duration_sec > 0: details['duration'] = format_duration(total_duration_sec)
    # Extract original media creation date
    details['media_created_at'] = extract_media_created_date(filepath, details['type'])
    return details

def create_thumbnail(filepath, file_hash, file_type):
    """
    Create a thumbnail for an image or video file.
    Uses THUMBNAIL_FORMAT (webp/jpeg) and THUMBNAIL_QUALITY settings for compression.
    WebP typically produces 25-35% smaller files than JPEG at similar visual quality.
    """
    # Determine output format and extension
    thumb_fmt = THUMBNAIL_FORMAT if THUMBNAIL_FORMAT in ('webp', 'jpeg') else 'webp'
    thumb_ext = 'webp' if thumb_fmt == 'webp' else 'jpeg'
    thumb_quality = THUMBNAIL_QUALITY

    if file_type in ['image', 'animated_image']:
        try:
            with Image.open(filepath) as img:
                # For animated images, preserve format for animation
                if file_type == 'animated_image' and getattr(img, 'is_animated', False):
                    anim_fmt = 'gif' if img.format == 'GIF' else 'webp'
                    cache_path = os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}.{anim_fmt}")
                    frames = [fr.copy() for fr in ImageSequence.Iterator(img)]
                    if frames:
                        for frame in frames:
                            frame.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2), Image.Resampling.LANCZOS)
                        processed_frames = [frame.convert('RGBA').convert('RGB') for frame in frames]
                        if processed_frames:
                            processed_frames[0].save(
                                cache_path, save_all=True, append_images=processed_frames[1:],
                                duration=img.info.get('duration', 100), loop=img.info.get('loop', 0),
                                optimize=True, quality=thumb_quality if anim_fmt == 'webp' else None
                            )
                    return cache_path
                else:
                    # Static image thumbnail
                    cache_path = os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}.{thumb_ext}")
                    img.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2), Image.Resampling.LANCZOS)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    if thumb_fmt == 'webp':
                        img.save(cache_path, 'WEBP', quality=thumb_quality, method=4)  # method 4 = good compression/speed balance
                    else:
                        img.save(cache_path, 'JPEG', quality=thumb_quality, optimize=True)
                    return cache_path
        except Exception as e:
            print(f"ERROR (Pillow): Could not create thumbnail for {os.path.basename(filepath)}: {e}")
    elif file_type == 'video':
        try:
            cap = cv2.VideoCapture(filepath)
            # Set timeout-like properties to prevent hanging on problematic files
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                cap.release()
                return None
            success, frame = cap.read()
            cap.release()
            if success and frame is not None:
                cache_path = os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}.{thumb_ext}")
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                img.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2), Image.Resampling.LANCZOS)
                if thumb_fmt == 'webp':
                    img.save(cache_path, 'WEBP', quality=thumb_quality, method=4)
                else:
                    img.save(cache_path, 'JPEG', quality=thumb_quality, optimize=True)
                return cache_path
        except Exception as e:
            print(f"ERROR (OpenCV): Could not create thumbnail for {os.path.basename(filepath)}: {e}")
    return None

def process_single_file(filepath):
    """
    Worker function to perform all heavy processing for a single file.
    Designed to be run in a parallel process pool.
    """
    try:
        mtime = os.path.getmtime(filepath)
        metadata = analyze_file_metadata(filepath)
        file_hash_for_thumbnail = hashlib.md5((filepath + str(mtime)).encode()).hexdigest()

        if not glob.glob(os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash_for_thumbnail}.*")):
            create_thumbnail(filepath, file_hash_for_thumbnail, metadata['type'])

        # Extract models, LoRAs, and input files from workflow if present
        models_list = []
        loras_list = []
        input_files_list = []
        if metadata['has_workflow']:
            workflow_json = extract_workflow(filepath)
            if workflow_json:
                models_list, loras_list = extract_models_and_loras(workflow_json)
                input_files_list = extract_input_files_from_workflow(workflow_json)

        file_id = hashlib.md5(filepath.encode()).hexdigest()
        file_size = os.path.getsize(filepath)

        return (
            file_id, filepath, mtime, os.path.basename(filepath),
            metadata['type'], metadata['duration'], metadata['dimensions'], metadata['has_workflow'], file_size, time.time(),
            json.dumps(models_list), json.dumps(loras_list), json.dumps(input_files_list),
            metadata['media_created_at']
        )
    except Exception as e:
        print(f"ERROR: Failed to process file {os.path.basename(filepath)} in worker: {e}")
        return None

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn=None):
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, mtime REAL NOT NULL,
            name TEXT NOT NULL, type TEXT, duration TEXT, dimensions TEXT,
            has_workflow INTEGER, is_favorite INTEGER DEFAULT 0, size INTEGER DEFAULT 0,
            last_scanned REAL DEFAULT 0,
            models TEXT DEFAULT '[]',
            loras TEXT DEFAULT '[]',
            input_files TEXT DEFAULT '[]',
            source_type TEXT DEFAULT 'local',
            sp_item_id TEXT,
            sp_drive_id TEXT,
            sp_original_path TEXT,
            sp_sync_timestamp REAL,
            original_path TEXT,
            media_created_at REAL
        )
    ''')
    # Create move history table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS file_move_history (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            from_path TEXT NOT NULL,
            to_path TEXT NOT NULL,
            moved_at REAL NOT NULL,
            moved_by TEXT,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
        )
    ''')
    # Create index for efficient lookups
    conn.execute('CREATE INDEX IF NOT EXISTS idx_move_history_file_id ON file_move_history(file_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_files_sp_item_id ON files(sp_item_id)')
    conn.commit()
    if close_conn: conn.close()
    
def get_dynamic_folder_config(force_refresh=False):
    global folder_config_cache
    if folder_config_cache is not None and not force_refresh:
        return folder_config_cache

    print("INFO: Refreshing folder configuration by scanning directory tree...")

    base_path_normalized = os.path.normpath(BASE_OUTPUT_PATH).replace('\\', '/')
    
    try:
        root_mtime = os.path.getmtime(BASE_OUTPUT_PATH)
    except OSError:
        root_mtime = time.time()

    dynamic_config = {
        '_root_': {
            'display_name': 'Main',
            'path': base_path_normalized,
            'relative_path': '',
            'parent': None,
            'children': [],
            'mtime': root_mtime 
        }
    }

    try:
        all_folders = {}
        for dirpath, dirnames, _ in os.walk(BASE_OUTPUT_PATH):
            dirnames[:] = [d for d in dirnames if d not in [THUMBNAIL_CACHE_FOLDER_NAME, SQLITE_CACHE_FOLDER_NAME, ZIP_CACHE_FOLDER_NAME]]
            for dirname in dirnames:
                full_path = os.path.normpath(os.path.join(dirpath, dirname)).replace('\\', '/')
                relative_path = os.path.relpath(full_path, BASE_OUTPUT_PATH).replace('\\', '/')
                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    mtime = time.time()
                
                all_folders[relative_path] = {
                    'full_path': full_path,
                    'display_name': dirname,
                    'mtime': mtime
                }

        sorted_paths = sorted(all_folders.keys(), key=lambda x: x.count('/'))

        for rel_path in sorted_paths:
            folder_data = all_folders[rel_path]
            key = path_to_key(rel_path)
            parent_rel_path = os.path.dirname(rel_path).replace('\\', '/')
            parent_key = '_root_' if parent_rel_path == '.' or parent_rel_path == '' else path_to_key(parent_rel_path)

            if parent_key in dynamic_config:
                dynamic_config[parent_key]['children'].append(key)

            dynamic_config[key] = {
                'display_name': folder_data['display_name'],
                'path': folder_data['full_path'],
                'relative_path': rel_path,
                'parent': parent_key,
                'children': [],
                'mtime': folder_data['mtime']
            }
    except FileNotFoundError:
        print(f"WARNING: The base directory '{BASE_OUTPUT_PATH}' was not found.")
    
    folder_config_cache = dynamic_config
    return dynamic_config
    
def full_sync_database(conn):
    print("INFO: Starting full file scan...")
    start_time = time.time()

    all_folders = get_dynamic_folder_config(force_refresh=True)
    db_files = {row['path']: row['mtime'] for row in conn.execute('SELECT path, mtime FROM files').fetchall()}
    
    disk_files = {}
    print("INFO: Scanning directories on disk...")
    for folder_data in all_folders.values():
        folder_path = folder_data['path']
        if not os.path.isdir(folder_path): continue
        try:
            for name in os.listdir(folder_path):
                filepath = os.path.join(folder_path, name)
                if os.path.isfile(filepath) and os.path.splitext(name)[1].lower() not in ['.json', '.sqlite']:
                    disk_files[filepath] = os.path.getmtime(filepath)
        except OSError as e:
            print(f"WARNING: Could not access folder {folder_path}: {e}")
            
    db_paths = set(db_files.keys())
    disk_paths = set(disk_files.keys())
    
    to_delete = db_paths - disk_paths
    to_add = disk_paths - db_paths
    to_check = disk_paths & db_paths
    to_update = {path for path in to_check if int(disk_files.get(path, 0)) > int(db_files.get(path, 0))}
    
    files_to_process = list(to_add.union(to_update))
    
    if files_to_process:
        print(f"INFO: Processing {len(files_to_process)} files in parallel using up to {MAX_PARALLEL_WORKERS or 'all'} CPU cores...")
        
        results = []
        # --- CORRECT BLOCK FOR PROGRESS BAR ---
        with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
            # Submit all jobs to the pool and get future objects
            futures = {executor.submit(process_single_file, path): path for path in files_to_process}
            
            # Create the progress bar with the correct total
            with tqdm(total=len(files_to_process), desc="Processing files") as pbar:
                # Iterate over the jobs as they are COMPLETED
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)
                    # Update the bar by 1 step for each completed job
                    pbar.update(1)

        if results:
            print(f"INFO: Inserting {len(results)} processed records into the database...")
            for i in range(0, len(results), BATCH_SIZE):
                batch = results[i:i + BATCH_SIZE]
                conn.executemany(
                    "INSERT OR REPLACE INTO files (id, path, mtime, name, type, duration, dimensions, has_workflow, size, last_scanned, models, loras, input_files, media_created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch
                )
                conn.commit()

    if to_delete:
        print(f"INFO: Removing {len(to_delete)} obsolete file entries from the database...")
        conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in to_delete])
        conn.commit()

    print(f"INFO: Full scan completed in {time.time() - start_time:.2f} seconds.")
    
def sync_folder_on_demand(folder_path):
    yield f"data: {json.dumps({'message': 'Checking folder for changes...', 'current': 0, 'total': 1})}\n\n"
    
    try:
        with get_db_connection() as conn:
            disk_files, valid_extensions = {}, {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.mkv', '.webm', '.mov', '.avi', '.mp3', '.wav', '.ogg', '.flac'}
            if os.path.isdir(folder_path):
                for name in os.listdir(folder_path):
                    filepath = os.path.join(folder_path, name)
                    if os.path.isfile(filepath) and os.path.splitext(name)[1].lower() in valid_extensions:
                        disk_files[filepath] = os.path.getmtime(filepath)
            
            db_files_query = conn.execute("SELECT path, mtime FROM files WHERE path LIKE ?", (folder_path + os.sep + '%',)).fetchall()
            db_files = {row['path']: row['mtime'] for row in db_files_query if os.path.normpath(os.path.dirname(row['path'])) == os.path.normpath(folder_path)}
            
            disk_filepaths, db_filepaths = set(disk_files.keys()), set(db_files.keys())
            files_to_add = disk_filepaths - db_filepaths
            files_to_delete = db_filepaths - disk_filepaths
            files_to_update = {path for path in (disk_filepaths & db_filepaths) if int(disk_files[path]) > int(db_files[path])}
            
            if not files_to_add and not files_to_update and not files_to_delete:
                yield f"data: {json.dumps({'message': 'Folder is up-to-date.', 'status': 'no_changes', 'current': 1, 'total': 1})}\n\n"
                return

            files_to_process = list(files_to_add.union(files_to_update))
            total_files = len(files_to_process)
            
            if total_files > 0:
                yield f"data: {json.dumps({'message': f'Found {total_files} new/modified files. Processing...', 'current': 0, 'total': total_files})}\n\n"
                
                data_to_upsert = []
                processed_count = 0

                with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
                    futures = {executor.submit(process_single_file, path): path for path in files_to_process}
                    
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        if result:
                            data_to_upsert.append(result)
                        
                        processed_count += 1
                        path = futures[future]
                        progress_data = {
                            'message': f'Processing: {os.path.basename(path)}',
                            'current': processed_count,
                            'total': total_files
                        }
                        yield f"data: {json.dumps(progress_data)}\n\n"

                if data_to_upsert:
                    conn.executemany("INSERT OR REPLACE INTO files (id, path, mtime, name, type, duration, dimensions, has_workflow, size, last_scanned, models, loras, input_files, media_created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", data_to_upsert)

            if files_to_delete:
                conn.executemany("DELETE FROM files WHERE path IN (?)", [(p,) for p in files_to_delete])

            conn.commit()
            yield f"data: {json.dumps({'message': 'Sync complete. Reloading...', 'status': 'reloading', 'current': total_files, 'total': total_files})}\n\n"

    except Exception as e:
        error_message = f"Error during sync: {e}"
        print(f"ERROR: {error_message}")
        yield f"data: {json.dumps({'message': error_message, 'current': 1, 'total': 1, 'error': True})}\n\n"
        
def scan_folder_and_extract_options(folder_path):
    extensions, prefixes = set(), set()
    file_count = 0
    try:
        if not os.path.isdir(folder_path): return 0, [], []
        for filename in os.listdir(folder_path):
            if os.path.isfile(os.path.join(folder_path, filename)):
                ext = os.path.splitext(filename)[1]
                if ext and ext.lower() not in ['.json', '.sqlite']: 
                    extensions.add(ext.lstrip('.').lower())
                    file_count += 1
                if '_' in filename: prefixes.add(filename.split('_')[0])
    except Exception as e: print(f"ERROR: Could not scan folder '{folder_path}': {e}")
    return file_count, sorted(list(extensions)), sorted(list(prefixes))

def initialize_gallery():
    print("INFO: Initializing gallery...")
    global FFPROBE_EXECUTABLE_PATH, THUMBNAIL_CACHE_DIR, SQLITE_CACHE_DIR, DATABASE_FILE, ZIP_CACHE_DIR, SMASHCUT_OUTPUT_DIR
    FFPROBE_EXECUTABLE_PATH = find_ffprobe_path()

    # Try to create cache directories, fall back to /tmp if permission denied
    try:
        os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)
        os.makedirs(SQLITE_CACHE_DIR, exist_ok=True)
    except PermissionError:
        print(f"{Colors.YELLOW}WARNING: Cannot create cache directories in {BASE_SMARTGALLERY_PATH}{Colors.RESET}")
        print(f"{Colors.YELLOW}         Falling back to /tmp for cache storage{Colors.RESET}")
        # Update paths to use /tmp
        THUMBNAIL_CACHE_DIR = os.path.join('/tmp', THUMBNAIL_CACHE_FOLDER_NAME)
        SQLITE_CACHE_DIR = os.path.join('/tmp', SQLITE_CACHE_FOLDER_NAME)
        DATABASE_FILE = os.path.join(SQLITE_CACHE_DIR, DATABASE_FILENAME)
        ZIP_CACHE_DIR = os.path.join('/tmp', ZIP_CACHE_FOLDER_NAME)
        SMASHCUT_OUTPUT_DIR = os.path.join('/tmp', SMASHCUT_FOLDER_NAME)
        os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)
        os.makedirs(SQLITE_CACHE_DIR, exist_ok=True)

    with get_db_connection() as conn:
        try:
            # Check if last_scanned column exists
            cursor = conn.execute("PRAGMA table_info(files)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'last_scanned' not in columns:
                print("INFO: Adding 'last_scanned' column to database...")
                conn.execute("ALTER TABLE files ADD COLUMN last_scanned REAL DEFAULT 0")
                conn.commit()

            stored_version = conn.execute('PRAGMA user_version').fetchone()[0]
        except sqlite3.DatabaseError: stored_version = 0

        # Run incremental migrations instead of full rebuild where possible
        if stored_version < DB_SCHEMA_VERSION:
            print(f"INFO: DB version outdated ({stored_version} < {DB_SCHEMA_VERSION}). Running migrations...")

            # Migration to version 27: Add file origin tracking columns
            if stored_version < 27:
                cursor = conn.execute("PRAGMA table_info(files)")
                columns = [row[1] for row in cursor.fetchall()]

                new_columns = [
                    ('source_type', "TEXT DEFAULT 'local'"),
                    ('sp_item_id', 'TEXT'),
                    ('sp_drive_id', 'TEXT'),
                    ('sp_original_path', 'TEXT'),
                    ('sp_sync_timestamp', 'REAL'),
                    ('original_path', 'TEXT'),
                ]
                for col_name, col_type in new_columns:
                    if col_name not in columns:
                        print(f"INFO: Adding '{col_name}' column to files table...")
                        conn.execute(f"ALTER TABLE files ADD COLUMN {col_name} {col_type}")

                # Create move history table
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS file_move_history (
                        id TEXT PRIMARY KEY,
                        file_id TEXT NOT NULL,
                        from_path TEXT NOT NULL,
                        to_path TEXT NOT NULL,
                        moved_at REAL NOT NULL,
                        moved_by TEXT,
                        FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
                    )
                ''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_move_history_file_id ON file_move_history(file_id)')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_files_sp_item_id ON files(sp_item_id)')
                conn.commit()
                print("INFO: Migration to v27 complete (file origin tracking).")

            # Migration to version 28: Add media_created_at column for original creation date
            if stored_version < 28:
                cursor = conn.execute("PRAGMA table_info(files)")
                columns = [row[1] for row in cursor.fetchall()]

                if 'media_created_at' not in columns:
                    print("INFO: Adding 'media_created_at' column to files table...")
                    conn.execute("ALTER TABLE files ADD COLUMN media_created_at REAL")
                    conn.commit()
                    print("INFO: Migration to v28 complete (media creation date tracking).")

            conn.execute(f'PRAGMA user_version = {DB_SCHEMA_VERSION}')
            conn.commit()
            print("INFO: Database migrations complete.")
        else:
            print(f"INFO: DB version ({stored_version}) is up to date. Starting normally.")

    # Initialize social features
    if SOCIAL_FEATURES_ENABLED:
        try:
            from social import init_social
            social_ok = init_social(app, DATABASE_FILE)
            if social_ok:
                print(f"{Colors.GREEN}INFO: Social features enabled.{Colors.RESET}")
                # Initialize scheduler with maintenance support
                from social.scheduler import init_scheduler
                init_scheduler(DATABASE_FILE, SECRET_KEY, BASE_SMARTGALLERY_PATH)
            else:
                print(f"{Colors.YELLOW}INFO: Social features disabled (init returned False).{Colors.RESET}")
        except ImportError as e:
            print(f"{Colors.YELLOW}INFO: Social features unavailable (missing dependencies: {e}).{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}WARNING: Social features failed to initialize: {e}{Colors.RESET}")
    else:
        print(f"INFO: Social features disabled via SOCIAL_FEATURES_ENABLED.")

    # Initialize SharePoint integration
    try:
        from social.sharepoint import sharepoint_available, start_background_sync
        if sharepoint_available():
            # Use new selective folder sync (syncs directly to BASE_OUTPUT_PATH)
            start_background_sync(
                base_output_path=BASE_OUTPUT_PATH,
                gallery_db_path=DATABASE_FILE,
                social_db_path=DATABASE_FILE if SOCIAL_FEATURES_ENABLED else None
            )
            print(f"{Colors.GREEN}INFO: SharePoint integration enabled (syncs to gallery folders).{Colors.RESET}")
        else:
            print(f"INFO: SharePoint not configured (set SHAREPOINT_* env vars to enable).")
    except ImportError:
        pass
    except Exception as e:
        print(f"{Colors.YELLOW}WARNING: SharePoint init failed: {e}{Colors.RESET}")

    # Run startup maintenance if enabled (for system recovery after disk issues)
    try:
        from social.maintenance import STARTUP_MAINTENANCE, run_startup_maintenance
        if STARTUP_MAINTENANCE:
            print(f"{Colors.YELLOW}INFO: Running startup maintenance (STARTUP_MAINTENANCE=true)...{Colors.RESET}")
            results = run_startup_maintenance(BASE_SMARTGALLERY_PATH, DATABASE_FILE)
            if results and not results.get('skipped'):
                freed_mb = results.get('summary', {}).get('total_freed_mb', 0)
                print(f"{Colors.GREEN}INFO: Startup maintenance freed {freed_mb:.1f}MB{Colors.RESET}")
    except ImportError:
        pass
    except Exception as e:
        print(f"{Colors.YELLOW}WARNING: Startup maintenance failed: {e}{Colors.RESET}")


# --- AUTHENTICATION PROTECTION ---
# Protect all gallery routes - requires login when social features are enabled
@app.before_request
def require_authentication():
    """Require authentication for all gallery routes when social features are enabled."""
    if not SOCIAL_FEATURES_ENABLED:
        return None  # No authentication required if social features disabled

    # Import here to avoid circular imports
    from flask_login import current_user

    # Define public paths that don't require authentication
    public_paths = [
        '/galleryout/social/login',
        '/galleryout/social/setup',
        '/galleryout/social/logout',
        '/galleryout/social/request-access',
        '/galleryout/social/forgot-password',
        '/galleryout/social/reset-password',
        '/favicon.ico',
        '/static/',
    ]

    # Check if current path is public
    for public_path in public_paths:
        if request.path.startswith(public_path) or request.path == public_path:
            return None

    # All other routes require authentication
    if not current_user.is_authenticated:
        # Redirect to login for HTML requests, return 401 for API requests
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('social.login', next=request.url))

    return None


# --- FLASK ROUTES ---
@app.route('/galleryout/')
@app.route('/')
def gallery_redirect_base():
    return redirect(url_for('gallery_view', folder_key='_root_'))


@app.route('/galleryout/api/folders')
def api_get_folders():
    """API endpoint to get all folders for the file browser."""
    folders = get_dynamic_folder_config()

    # Get folders that actually contain files (not just subfolders)
    folders_with_files = set()
    with get_db_connection() as conn:
        file_paths = conn.execute('SELECT DISTINCT path FROM files').fetchall()
        for row in file_paths:
            parent_dir = os.path.dirname(row['path'])
            folders_with_files.add(os.path.normpath(parent_dir))

    folder_list = []
    for key, info in folders.items():
        folder_path_norm = os.path.normpath(info['path'])
        # Only include folders that have files directly in them
        if folder_path_norm not in folders_with_files:
            continue

        # Use relative path for display to distinguish folders with same name
        display_name = info.get('relative_path', info['display_name'])
        if not display_name or display_name == '':
            display_name = info['display_name']

        folder_list.append({
            'key': key,
            'name': display_name,
            'path': info['path'],
            'parent': info.get('parent')
        })

    # Sort by name
    folder_list.sort(key=lambda x: x['name'].lower())
    return jsonify(folder_list)


@app.route('/galleryout/api/files/<string:folder_key>')
def api_get_files(folder_key):
    """API endpoint to get files in a folder for the file browser."""
    folders = get_dynamic_folder_config()
    if folder_key not in folders:
        return jsonify({'error': 'Folder not found'}), 404

    folder_path = folders[folder_key]['path']

    with get_db_connection() as conn:
        query = """
            SELECT id, name, type, path, dimensions, size, mtime, is_favorite
            FROM files
            WHERE path LIKE ?
            ORDER BY mtime DESC
        """
        all_files_raw = conn.execute(query, (folder_path + os.sep + '%',)).fetchall()

    # Filter to only files directly in this folder
    folder_path_norm = os.path.normpath(folder_path)
    files = []
    for row in all_files_raw:
        if os.path.normpath(os.path.dirname(row['path'])) == folder_path_norm:
            files.append({
                'id': row['id'],
                'name': row['name'],
                'type': row['type'],
                'dimensions': row['dimensions'],
                'size': row['size'],
                'mtime': row['mtime'],
                'is_favorite': bool(row['is_favorite'])
            })

    return jsonify({'files': files, 'count': len(files)})


@app.route('/galleryout/sync_status/<string:folder_key>')
def sync_status(folder_key):
    folders = get_dynamic_folder_config()
    if folder_key not in folders:
        abort(404)
    folder_path = folders[folder_key]['path']
    return Response(sync_folder_on_demand(folder_path), mimetype='text/event-stream')

@app.route('/galleryout/view/<string:folder_key>')
def gallery_view(folder_key):
    global gallery_view_cache
    folders = get_dynamic_folder_config(force_refresh=True)
    if folder_key not in folders:
        return redirect(url_for('gallery_view', folder_key='_root_'))

    current_folder_info = folders[folder_key]
    folder_path = current_folder_info['path']

    with get_db_connection() as conn:
        conditions, params = [], []
        conditions.append("path LIKE ?")
        params.append(folder_path + os.sep + '%')

        sort_by = 'name' if request.args.get('sort_by') == 'name' else 'mtime'
        sort_order = 'asc' if request.args.get('sort_order', 'desc').lower() == 'asc' else 'desc'

        search_term = request.args.get('search', '').strip()
        if search_term:
            conditions.append("name LIKE ?")
            params.append(f"%{search_term}%")
        if request.args.get('favorites', 'false').lower() == 'true':
            conditions.append("is_favorite = 1")

        # Media type filter
        selected_media_types = request.args.getlist('media_type')
        if selected_media_types:
            type_conditions = []
            for mt in selected_media_types:
                if mt == 'image':
                    type_conditions.append("type IN ('image', 'animated_image')")
                elif mt == 'video':
                    type_conditions.append("type = 'video'")
                elif mt == 'audio':
                    type_conditions.append("type = 'audio'")
                elif mt == 'document':
                    type_conditions.append("type = 'document'")
            if type_conditions:
                conditions.append(f"({' OR '.join(type_conditions)})")

        selected_prefixes = request.args.getlist('prefix')
        if selected_prefixes:
            prefix_conditions = [f"name LIKE ?" for p in selected_prefixes if p.strip()]
            params.extend([f"{p.strip()}_%" for p in selected_prefixes if p.strip()])
            if prefix_conditions: conditions.append(f"({' OR '.join(prefix_conditions)})")

        selected_extensions = request.args.getlist('extension')
        if selected_extensions:
            ext_conditions = [f"name LIKE ?" for ext in selected_extensions if ext.strip()]
            params.extend([f"%.{ext.lstrip('.').lower()}" for ext in selected_extensions if ext.strip()])
            if ext_conditions: conditions.append(f"({' OR '.join(ext_conditions)})")

        sort_direction = "ASC" if sort_order == 'asc' else "DESC"
        query = f"SELECT * FROM files WHERE {' AND '.join(conditions)} ORDER BY {sort_by} {sort_direction}"

        all_files_raw = conn.execute(query, params).fetchall()

    folder_path_norm = os.path.normpath(folder_path)
    all_files_filtered = [dict(row) for row in all_files_raw if os.path.normpath(os.path.dirname(row['path'])) == folder_path_norm]

    # Filter by programs and campaigns (requires social features)
    selected_programs = request.args.getlist('program')
    selected_campaigns = request.args.getlist('campaign')
    available_programs = []
    available_campaigns = []

    if SOCIAL_FEATURES_ENABLED:
        try:
            from social.models import get_social_db
            social_conn = get_social_db(DATABASE_FILE)
            try:
                # Fetch available programs and campaigns for dropdowns
                available_programs = [
                    {'id': row['id'], 'name': row['name']}
                    for row in social_conn.execute(
                        "SELECT id, name FROM programs WHERE is_active = 1 ORDER BY sort_order, name"
                    ).fetchall()
                ]
                available_campaigns = [
                    {'id': row['id'], 'name': row['name']}
                    for row in social_conn.execute(
                        "SELECT id, name FROM campaigns WHERE is_active = 1 ORDER BY sort_order, name"
                    ).fetchall()
                ]

                # Filter by programs if selected
                if selected_programs:
                    program_file_ids = set()
                    placeholders = ','.join(['?' for _ in selected_programs])
                    rows = social_conn.execute(
                        f"SELECT DISTINCT file_id FROM file_programs WHERE program_id IN ({placeholders})",
                        selected_programs
                    ).fetchall()
                    program_file_ids = {row['file_id'] for row in rows}
                    all_files_filtered = [f for f in all_files_filtered if f['id'] in program_file_ids]

                # Filter by campaigns if selected
                if selected_campaigns:
                    campaign_file_ids = set()
                    placeholders = ','.join(['?' for _ in selected_campaigns])
                    rows = social_conn.execute(
                        f"SELECT DISTINCT file_id FROM file_campaigns WHERE campaign_id IN ({placeholders})",
                        selected_campaigns
                    ).fetchall()
                    campaign_file_ids = {row['file_id'] for row in rows}
                    all_files_filtered = [f for f in all_files_filtered if f['id'] in campaign_file_ids]

            finally:
                social_conn.close()
        except Exception as e:
            print(f"Warning: Could not load programs/campaigns for filtering: {e}")

    gallery_view_cache = all_files_filtered
    initial_files = gallery_view_cache[:PAGE_SIZE]
    total_folder_files, extensions, prefixes = scan_folder_and_extract_options(folder_path)
    breadcrumbs, ancestor_keys = [], set()
    curr_key = folder_key
    while curr_key is not None and curr_key in folders:
        folder_info = folders[curr_key]
        breadcrumbs.append({'key': curr_key, 'display_name': folder_info['display_name']})
        ancestor_keys.add(curr_key)
        curr_key = folder_info.get('parent')
    breadcrumbs.reverse()

    # Get SharePoint sync folder names to mark in the UI
    sharepoint_folders = set()
    if SOCIAL_FEATURES_ENABLED:
        try:
            with get_db_connection() as conn:
                sp_rows = conn.execute(
                    "SELECT local_folder_name FROM sharepoint_sync_folders WHERE is_enabled = 1"
                ).fetchall()
                sharepoint_folders = {row['local_folder_name'] for row in sp_rows}
        except Exception:
            pass  # Table might not exist yet

    return render_template('index.html',
                           files=initial_files,
                           total_files=len(gallery_view_cache),
                           total_folder_files=total_folder_files,
                           folders=folders,
                           current_folder_key=folder_key,
                           current_folder_info=current_folder_info,
                           breadcrumbs=breadcrumbs,
                           ancestor_keys=list(ancestor_keys),
                           available_extensions=extensions,
                           available_prefixes=prefixes,
                           selected_extensions=request.args.getlist('extension'),
                           selected_prefixes=request.args.getlist('prefix'),
                           selected_media_types=request.args.getlist('media_type'),
                           selected_programs=selected_programs,
                           selected_campaigns=selected_campaigns,
                           available_programs=available_programs,
                           available_campaigns=available_campaigns,
                           show_favorites=request.args.get('favorites', 'false').lower() == 'true',
                           protected_folder_keys=list(PROTECTED_FOLDER_KEYS),
                           social_enabled=SOCIAL_FEATURES_ENABLED,
                           sharepoint_folders=list(sharepoint_folders))

@app.route('/galleryout/upload', methods=['POST'])
def upload_files():
    folder_key = request.form.get('folder_key')
    if not folder_key: return jsonify({'status': 'error', 'message': 'No destination folder provided.'}), 400
    folders = get_dynamic_folder_config()
    if folder_key not in folders: return jsonify({'status': 'error', 'message': 'Destination folder not found.'}), 404
    destination_path = folders[folder_key]['path']
    if 'files' not in request.files: return jsonify({'status': 'error', 'message': 'No files were uploaded.'}), 400

    uploaded_files = request.files.getlist('files')
    relative_paths = request.form.getlist('relativePaths')  # For folder uploads
    errors, success_count = {}, 0
    created_folders = set()

    for idx, file in enumerate(uploaded_files):
        if not file or not file.filename:
            continue

        original_filename = file.filename

        # Check if this is a ZIP file that should be extracted
        if original_filename.lower().endswith('.zip'):
            try:
                zip_name = os.path.splitext(secure_filename(original_filename))[0]
                extract_dir = os.path.join(destination_path, zip_name)

                # Create unique folder name if it already exists
                base_extract_dir = extract_dir
                counter = 1
                while os.path.exists(extract_dir):
                    extract_dir = f"{base_extract_dir}_{counter}"
                    counter += 1

                os.makedirs(extract_dir, exist_ok=True)
                created_folders.add(extract_dir)

                # Extract ZIP contents
                with zipfile.ZipFile(io.BytesIO(file.read())) as zf:
                    for zip_info in zf.infolist():
                        if zip_info.is_dir():
                            continue
                        # Sanitize and extract file
                        extracted_name = os.path.basename(zip_info.filename)
                        if not extracted_name:
                            continue
                        # Preserve folder structure within the ZIP
                        zip_dir = os.path.dirname(zip_info.filename)
                        if zip_dir:
                            target_dir = os.path.join(extract_dir, *[secure_filename(p) for p in zip_dir.split('/')])
                            os.makedirs(target_dir, exist_ok=True)
                            created_folders.add(target_dir)
                        else:
                            target_dir = extract_dir

                        safe_name = secure_filename(extracted_name)
                        if safe_name:
                            target_path = os.path.join(target_dir, safe_name)
                            with zf.open(zip_info.filename) as src, open(target_path, 'wb') as dst:
                                dst.write(src.read())
                            success_count += 1

            except Exception as e:
                errors[original_filename] = f"ZIP extraction error: {str(e)}"
            continue

        # Handle folder uploads with relative paths
        if relative_paths and idx < len(relative_paths) and relative_paths[idx]:
            rel_path = relative_paths[idx]
            # Get the folder structure from the relative path
            path_parts = rel_path.split('/')
            if len(path_parts) > 1:
                # Create the folder structure
                folder_parts = path_parts[:-1]
                target_dir = destination_path
                for part in folder_parts:
                    safe_part = secure_filename(part)
                    if safe_part:
                        target_dir = os.path.join(target_dir, safe_part)
                os.makedirs(target_dir, exist_ok=True)
                created_folders.add(target_dir)
                filename = secure_filename(path_parts[-1])
            else:
                target_dir = destination_path
                filename = secure_filename(original_filename)
        else:
            target_dir = destination_path
            filename = secure_filename(original_filename)

        if not filename:
            continue

        try:
            file.save(os.path.join(target_dir, filename))
            success_count += 1
        except Exception as e:
            errors[original_filename] = str(e)

    # Sync all affected folders
    if success_count > 0:
        sync_folder_on_demand(destination_path)
        for folder in created_folders:
            sync_folder_on_demand(folder)

    if errors:
        return jsonify({
            'status': 'partial_success',
            'message': f'Successfully uploaded {success_count} files. Errors: {", ".join(errors.keys())}',
            'created_folders': list(created_folders)
        }), 207
    return jsonify({
        'status': 'success',
        'message': f'Successfully uploaded {success_count} files.',
        'created_folders': list(created_folders)
    })
                           
@app.route('/galleryout/rescan_folder', methods=['POST'])
def rescan_folder():
    data = request.json
    folder_key = data.get('folder_key')
    mode = data.get('mode', 'all') # 'all' or 'recent'
    
    if not folder_key: return jsonify({'status': 'error', 'message': 'No folder provided.'}), 400
    folders = get_dynamic_folder_config()
    if folder_key not in folders: return jsonify({'status': 'error', 'message': 'Folder not found.'}), 404
    
    folder_path = folders[folder_key]['path']
    
    try:
        with get_db_connection() as conn:
            # Get all files in this folder
            query = "SELECT path, last_scanned FROM files WHERE path LIKE ?"
            params = (folder_path + os.sep + '%',)
            rows = conn.execute(query, params).fetchall()
            
            # Filter files strictly within this folder (not subfolders)
            folder_path_norm = os.path.normpath(folder_path)
            files_in_folder = [
                {'path': row['path'], 'last_scanned': row['last_scanned']} 
                for row in rows 
                if os.path.normpath(os.path.dirname(row['path'])) == folder_path_norm
            ]
            
            files_to_process = []
            current_time = time.time()
            
            if mode == 'recent':
                # Process files not scanned in the last 60 minutes (3600 seconds)
                cutoff_time = current_time - 3600
                files_to_process = [f['path'] for f in files_in_folder if (f['last_scanned'] or 0) < cutoff_time]
            else:
                # Process all files
                files_to_process = [f['path'] for f in files_in_folder]
            
            if not files_to_process:
                return jsonify({'status': 'success', 'message': 'No files needed rescanning.', 'count': 0})
            
            print(f"INFO: Rescanning {len(files_to_process)} files in '{folder_path}' (Mode: {mode})...")
            
            processed_count = 0
            results = []
            
            with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
                futures = {executor.submit(process_single_file, path): path for path in files_to_process}
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)
                    processed_count += 1
            
            if results:
                # Upsert results
                conn.executemany(
                    "INSERT OR REPLACE INTO files (id, path, mtime, name, type, duration, dimensions, has_workflow, size, last_scanned, models, loras, input_files, media_created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    results
                )
                conn.commit()

        return jsonify({'status': 'success', 'message': f'Successfully rescanned {len(results)} files.', 'count': len(results)})
        
    except Exception as e:
        print(f"ERROR: Rescan failed: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/galleryout/rescan_all_folders', methods=['POST'])
def rescan_all_folders():
    """Rescan all folders including subfolders recursively."""
    data = request.json or {}
    mode = data.get('mode', 'all')  # 'all', 'recent', or 'missing'

    # Supported media extensions
    media_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif',
                        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv', '.flv',
                        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a'}

    try:
        folders = get_dynamic_folder_config()
        total_rescanned = 0
        folder_results = {}

        with get_db_connection() as conn:
            # Get existing file scan times from database
            existing_files = {}
            rows = conn.execute("SELECT path, last_scanned FROM files").fetchall()
            for row in rows:
                existing_files[row['path']] = row['last_scanned']

            for folder_key, folder_info in folders.items():
                folder_path = folder_info['path']
                if not os.path.exists(folder_path):
                    continue

                # Walk directory tree recursively to find all media files
                all_files = []
                for root, dirs, files in os.walk(folder_path):
                    # Skip hidden/cache directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for filename in files:
                        ext = os.path.splitext(filename)[1].lower()
                        if ext in media_extensions:
                            all_files.append(os.path.join(root, filename))

                # Filter based on mode
                files_to_process = []
                current_time = time.time()

                if mode == 'recent':
                    # Only files not scanned in the last hour
                    cutoff_time = current_time - 3600
                    files_to_process = [
                        f for f in all_files
                        if existing_files.get(f, 0) < cutoff_time
                    ]
                elif mode == 'missing':
                    # Only files not in database or missing thumbnails
                    for f in all_files:
                        if f not in existing_files:
                            files_to_process.append(f)
                        else:
                            # Check if thumbnail exists
                            mtime = os.path.getmtime(f)
                            file_hash = hashlib.md5((f + str(mtime)).encode()).hexdigest()
                            if not glob.glob(os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}.*")):
                                files_to_process.append(f)
                else:
                    # 'all' mode - process everything
                    files_to_process = all_files

                if not files_to_process:
                    continue

                display_name = folder_info.get('display_name', folder_key)
                print(f"INFO: Rescanning {len(files_to_process)} files in '{display_name}' (including subfolders)...")

                results = []
                with concurrent.futures.ProcessPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
                    futures = {executor.submit(process_single_file, fp): fp for fp in files_to_process}
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        if result:
                            results.append(result)

                if results:
                    conn.executemany(
                        "INSERT OR REPLACE INTO files (id, path, mtime, name, type, duration, dimensions, has_workflow, size, last_scanned, models, loras, input_files, media_created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        results
                    )
                    conn.commit()
                    total_rescanned += len(results)
                    folder_results[display_name] = len(results)

        return jsonify({
            'status': 'success',
            'message': f'Rescanned {total_rescanned} files across {len(folder_results)} folders (including subfolders).',
            'count': total_rescanned,
            'folders': folder_results
        })

    except Exception as e:
        print(f"ERROR: Rescan all folders failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/galleryout/create_folder', methods=['POST'])
def create_folder():
    data = request.json
    parent_key = data.get('parent_key', '_root_')
    folder_name = re.sub(r'[^a-zA-Z0-9_-]', '', data.get('folder_name', '')).strip()
    if not folder_name: return jsonify({'status': 'error', 'message': 'Invalid folder name provided.'}), 400
    folders = get_dynamic_folder_config()
    if parent_key not in folders: return jsonify({'status': 'error', 'message': 'Parent folder not found.'}), 404
    parent_path = folders[parent_key]['path']
    new_folder_path = os.path.join(parent_path, folder_name)
    try:
        os.makedirs(new_folder_path, exist_ok=False)
        sync_folder_on_demand(parent_path)
        return jsonify({'status': 'success', 'message': f'Folder "{folder_name}" created successfully.'})
    except FileExistsError: return jsonify({'status': 'error', 'message': 'Folder already exists.'}), 400
    except Exception as e: return jsonify({'status': 'error', 'message': str(e)}), 500

# --- ZIP BACKGROUND JOB MANAGEMENT ---
zip_jobs = {}
def background_zip_task(job_id, file_ids):
    try:
        if not os.path.exists(ZIP_CACHE_DIR):
            try:
                os.makedirs(ZIP_CACHE_DIR, exist_ok=True)
            except Exception as e:
                print(f"ERROR: Could not create zip directory: {e}")
                zip_jobs[job_id] = {'status': 'error', 'message': f'Server permission error: {e}'}
                return
        
        zip_filename = f"gallery_{job_id}.zip"
        zip_filepath = os.path.join(ZIP_CACHE_DIR, zip_filename)
        
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(file_ids))
            query = f"SELECT path, name FROM files WHERE id IN ({placeholders})"
            files_to_zip = conn.execute(query, file_ids).fetchall()

        if not files_to_zip:
            zip_jobs[job_id] = {'status': 'error', 'message': 'No valid files found.'}
            return

        # Use configurable compression level (compresslevel requires Python 3.7+)
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSION_LEVEL) as zf:
            for file_row in files_to_zip:
                file_path = file_row['path']
                file_name = file_row['name']
                # Check the file exists
                if os.path.exists(file_path):
                    # Add file to zip
                    zf.write(file_path, file_name)
        
        # Job completed succesfully
        zip_jobs[job_id] = {
            'status': 'ready', 
            'filename': zip_filename
        }
        
        # Clean automatic: delete zip older than 24 hours
        try:
            now = time.time()
            for f in os.listdir(ZIP_CACHE_DIR):
                fp = os.path.join(ZIP_CACHE_DIR, f)
                if os.path.isfile(fp) and os.stat(fp).st_mtime < now - 86400:
                    os.remove(fp)
        except Exception: 
            pass

    except Exception as e:
        print(f"Zip Error: {e}")
        zip_jobs[job_id] = {'status': 'error', 'message': str(e)}
        
@app.route('/galleryout/prepare_batch_zip', methods=['POST'])
def prepare_batch_zip():
    data = request.json
    file_ids = data.get('file_ids', [])
    if not file_ids:
        return jsonify({'status': 'error', 'message': 'No files specified.'}), 400

    job_id = str(uuid.uuid4())
    zip_jobs[job_id] = {'status': 'processing'}
    
    thread = threading.Thread(target=background_zip_task, args=(job_id, file_ids))
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'success', 'job_id': job_id, 'message': 'Zip generation started.'})

@app.route('/galleryout/check_zip_status/<job_id>')
def check_zip_status(job_id):
    job = zip_jobs.get(job_id)
    if not job:
        return jsonify({'status': 'error', 'message': 'Job not found'}), 404
    response_data = job.copy()
    if job['status'] == 'ready' and 'filename' in job:
        response_data['download_url'] = url_for('serve_zip_file', filename=job['filename'])
        
    return jsonify(response_data)
    
@app.route('/galleryout/serve_zip/<filename>')
def serve_zip_file(filename):
    return send_from_directory(ZIP_CACHE_DIR, filename, as_attachment=True)
    

@app.route('/galleryout/rename_folder/<string:folder_key>', methods=['POST'])
def rename_folder(folder_key):
    if folder_key in PROTECTED_FOLDER_KEYS: return jsonify({'status': 'error', 'message': 'This folder cannot be renamed.'}), 403
    new_name = re.sub(r'[^a-zA-Z0-9_-]', '', request.json.get('new_name', '')).strip()
    if not new_name: return jsonify({'status': 'error', 'message': 'Invalid name.'}), 400
    folders = get_dynamic_folder_config()
    if folder_key not in folders: return jsonify({'status': 'error', 'message': 'Folder not found.'}), 400
    old_path = folders[folder_key]['path']
    new_path = os.path.join(os.path.dirname(old_path), new_name)
    if os.path.exists(new_path): return jsonify({'status': 'error', 'message': 'A folder with this name already exists.'}), 400
    try:
        with get_db_connection() as conn:
            old_path_like = old_path + os.sep + '%'
            files_to_update = conn.execute("SELECT id, path FROM files WHERE path LIKE ?", (old_path_like,)).fetchall()
            update_data = []
            for row in files_to_update:
                new_file_path = row['path'].replace(old_path, new_path, 1)
                new_id = hashlib.md5(new_file_path.encode()).hexdigest()
                update_data.append((new_id, new_file_path, row['id']))
            os.rename(old_path, new_path)
            if update_data: conn.executemany("UPDATE files SET id = ?, path = ? WHERE id = ?", update_data)
            conn.commit()
        get_dynamic_folder_config(force_refresh=True)
        return jsonify({'status': 'success', 'message': 'Folder renamed.'})
    except Exception as e: return jsonify({'status': 'error', 'message': f'Error: {e}'}), 500

@app.route('/galleryout/delete_folder/<string:folder_key>', methods=['POST'])
def delete_folder(folder_key):
    if folder_key in PROTECTED_FOLDER_KEYS: return jsonify({'status': 'error', 'message': 'This folder cannot be deleted.'}), 403
    folders = get_dynamic_folder_config()
    if folder_key not in folders: return jsonify({'status': 'error', 'message': 'Folder not found.'}), 404
    try:
        folder_path = folders[folder_key]['path']
        with get_db_connection() as conn:
            conn.execute("DELETE FROM files WHERE path LIKE ?", (folder_path + os.sep + '%',))
            conn.commit()
        shutil.rmtree(folder_path)
        get_dynamic_folder_config(force_refresh=True)
        return jsonify({'status': 'success', 'message': 'Folder deleted.'})
    except Exception as e: return jsonify({'status': 'error', 'message': f'Error: {e}'}), 500

@app.route('/galleryout/move_folder/<string:folder_key>', methods=['POST'])
def move_folder(folder_key):
    """Move a folder to a different parent folder."""
    if folder_key in PROTECTED_FOLDER_KEYS:
        return jsonify({'status': 'error', 'message': 'This folder cannot be moved.'}), 403

    data = request.json or {}
    dest_key = data.get('destination_folder')

    folders = get_dynamic_folder_config()
    if folder_key not in folders:
        return jsonify({'status': 'error', 'message': 'Folder not found.'}), 404
    if not dest_key or dest_key not in folders:
        return jsonify({'status': 'error', 'message': 'Invalid destination folder.'}), 400

    source_path = folders[folder_key]['path']
    dest_parent_path = folders[dest_key]['path']
    folder_name = os.path.basename(source_path)
    new_path = os.path.join(dest_parent_path, folder_name)

    # Prevent moving folder into itself or its children
    if new_path.startswith(source_path + os.sep) or new_path == source_path:
        return jsonify({'status': 'error', 'message': 'Cannot move folder into itself.'}), 400

    # Check if a folder with the same name exists in destination
    if os.path.exists(new_path):
        # Try to create a unique name
        base_path = new_path
        counter = 1
        while os.path.exists(new_path):
            new_path = f"{base_path}_{counter}"
            counter += 1
        folder_name = os.path.basename(new_path)

    try:
        with get_db_connection() as conn:
            # Update all file paths in the database
            old_path_like = source_path + os.sep + '%'
            files_to_update = conn.execute("SELECT id, path FROM files WHERE path LIKE ?", (old_path_like,)).fetchall()
            update_data = []
            for row in files_to_update:
                new_file_path = row['path'].replace(source_path, new_path, 1)
                new_id = hashlib.md5(new_file_path.encode()).hexdigest()
                update_data.append((new_id, new_file_path, row['id']))

            # Move the folder on disk
            shutil.move(source_path, new_path)

            # Update database records
            if update_data:
                conn.executemany("UPDATE files SET id = ?, path = ? WHERE id = ?", update_data)
            conn.commit()

        get_dynamic_folder_config(force_refresh=True)
        return jsonify({
            'status': 'success',
            'message': f'Folder moved successfully.',
            'new_name': folder_name
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Error moving folder: {e}'}), 500

@app.route('/galleryout/load_more')
def load_more():
    offset = request.args.get('offset', 0, type=int)
    if offset >= len(gallery_view_cache): return jsonify(files=[])
    return jsonify(files=gallery_view_cache[offset:offset + PAGE_SIZE])

def get_file_info_from_db(file_id, column='*'):
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT {column} FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row: abort(404)
    return dict(row) if column == '*' else row[0]

def _get_unique_filepath(destination_folder, filename):
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filepath = os.path.join(destination_folder, filename)
    while os.path.exists(new_filepath):
        new_filename = f"{base}({counter}){ext}"
        new_filepath = os.path.join(destination_folder, new_filename)
        counter += 1
    return new_filepath

@app.route('/galleryout/move_batch', methods=['POST'])
def move_batch():
    data = request.json
    file_ids, dest_key = data.get('file_ids', []), data.get('destination_folder')
    folders = get_dynamic_folder_config()
    if not all([file_ids, dest_key, dest_key in folders]):
        return jsonify({'status': 'error', 'message': 'Invalid data provided.'}), 400
    moved_count, renamed_count, failed_files, dest_path_folder = 0, 0, [], folders[dest_key]['path']

    # Get current user ID if authenticated (for move history)
    moved_by_user_id = None
    if SOCIAL_FEATURES_ENABLED:
        try:
            from flask_login import current_user
            if current_user.is_authenticated:
                moved_by_user_id = current_user.id
        except Exception:
            pass

    with get_db_connection() as conn:
        for file_id in file_ids:
            source_path = None
            try:
                file_info = conn.execute(
                    "SELECT path, name, original_path, source_type, sp_item_id, sp_drive_id, sp_original_path, sp_sync_timestamp FROM files WHERE id = ?",
                    (file_id,)
                ).fetchone()
                if not file_info:
                    failed_files.append(f"ID {file_id} not found in DB")
                    continue
                source_path, source_filename = file_info['path'], file_info['name']
                if not os.path.exists(source_path):
                    failed_files.append(f"{source_filename} (not found on disk)")
                    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
                    continue
                final_dest_path = _get_unique_filepath(dest_path_folder, source_filename)
                final_filename = os.path.basename(final_dest_path)
                if final_filename != source_filename: renamed_count += 1
                shutil.move(source_path, final_dest_path)
                new_id = hashlib.md5(final_dest_path.encode()).hexdigest()

                # Preserve origin metadata and set original_path if not already set
                original_path = file_info['original_path'] or source_path
                conn.execute("""
                    UPDATE files SET
                        id = ?,
                        path = ?,
                        name = ?,
                        original_path = ?
                    WHERE id = ?
                """, (new_id, final_dest_path, final_filename, original_path, file_id))

                # Log move history
                move_history_id = str(uuid.uuid4())
                now = time.time()
                conn.execute("""
                    INSERT INTO file_move_history (id, file_id, from_path, to_path, moved_at, moved_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (move_history_id, new_id, source_path, final_dest_path, now, moved_by_user_id))

                moved_count += 1
            except Exception as e:
                filename_for_error = os.path.basename(source_path) if source_path else f"ID {file_id}"
                failed_files.append(filename_for_error)
                print(f"ERROR: Failed to move file {filename_for_error}. Reason: {e}")
                continue
        conn.commit()
    message = f"Successfully moved {moved_count} file(s)."
    if renamed_count > 0: message += f" {renamed_count} were renamed to avoid conflicts."
    if failed_files: message += f" Failed to move {len(failed_files)} file(s)."
    return jsonify({'status': 'partial_success' if failed_files else 'success', 'message': message})

@app.route('/galleryout/delete_batch', methods=['POST'])
def delete_batch():
    file_ids = request.json.get('file_ids', [])
    if not file_ids: return jsonify({'status': 'error', 'message': 'No files selected.'}), 400
    deleted_count, failed_files = 0, []
    with get_db_connection() as conn:
        placeholders = ','.join('?' * len(file_ids))
        files_to_delete = conn.execute(f"SELECT id, path FROM files WHERE id IN ({placeholders})", file_ids).fetchall()
        ids_to_remove_from_db = []
        for row in files_to_delete:
            try:
                if os.path.exists(row['path']): safe_delete_file(row['path'])
                ids_to_remove_from_db.append(row['id'])
                deleted_count += 1
            except Exception as e: 
                failed_files.append(os.path.basename(row['path']))
                print(f"ERROR: Could not delete {row['path']}: {e}")
        if ids_to_remove_from_db:
            db_placeholders = ','.join('?' * len(ids_to_remove_from_db))
            conn.execute(f"DELETE FROM files WHERE id IN ({db_placeholders})", ids_to_remove_from_db)
            conn.commit()
    action = "moved to trash" if DELETE_TO else "deleted"
    message = f'Successfully {action} {deleted_count} files.'
    if failed_files: message += f" Failed to delete {len(failed_files)} files."
    return jsonify({'status': 'partial_success' if failed_files else 'success', 'message': message})

@app.route('/galleryout/favorite_batch', methods=['POST'])
def favorite_batch():
    data = request.json
    file_ids, status = data.get('file_ids', []), data.get('status', False)
    if not file_ids: return jsonify({'status': 'error', 'message': 'No files selected'}), 400
    with get_db_connection() as conn:
        placeholders = ','.join('?' * len(file_ids))
        conn.execute(f"UPDATE files SET is_favorite = ? WHERE id IN ({placeholders})", [1 if status else 0] + file_ids)
        conn.commit()
    return jsonify({'status': 'success', 'message': f"Updated favorites for {len(file_ids)} files."})

@app.route('/galleryout/toggle_favorite/<string:file_id>', methods=['POST'])
def toggle_favorite(file_id):
    with get_db_connection() as conn:
        current = conn.execute("SELECT is_favorite FROM files WHERE id = ?", (file_id,)).fetchone()
        if not current: abort(404)
        new_status = 1 - current['is_favorite']
        conn.execute("UPDATE files SET is_favorite = ? WHERE id = ?", (new_status, file_id))
        conn.commit()
        return jsonify({'status': 'success', 'is_favorite': bool(new_status)})

# --- FIX: ROBUST DELETE ROUTE ---
@app.route('/galleryout/delete/<string:file_id>', methods=['POST'])
def delete_file(file_id):
    with get_db_connection() as conn:
        file_info = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
        if not file_info:
            return jsonify({'status': 'success', 'message': 'File already deleted from database.'})
        
        filepath = file_info['path']
        
        try:
            if os.path.exists(filepath):
                safe_delete_file(filepath)
            # If file doesn't exist on disk, we still proceed to remove the DB entry, which is the desired state.
        except OSError as e:
            # A real OS error occurred (e.g., permissions).
            print(f"ERROR: Could not delete file {filepath} from disk: {e}")
            return jsonify({'status': 'error', 'message': f'Could not delete file from disk: {e}'}), 500

        # Whether the file was deleted now or was already gone, we clean up the DB.
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        action = "moved to trash" if DELETE_TO else "deleted"
        return jsonify({'status': 'success', 'message': f'File {action} successfully.'})

# --- NEW FEATURE: RENAME FILE ---
@app.route('/galleryout/rename_file/<string:file_id>', methods=['POST'])
def rename_file(file_id):
    data = request.json
    new_name = data.get('new_name', '').strip()

    # Basic validation for the new name
    if not new_name or len(new_name) > 250:
        return jsonify({'status': 'error', 'message': 'The provided filename is invalid or too long.'}), 400
    if re.search(r'[\\/:"*?<>|]', new_name):
        return jsonify({'status': 'error', 'message': 'Filename contains invalid characters.'}), 400

    try:
        with get_db_connection() as conn:
            file_info = conn.execute("SELECT path, name FROM files WHERE id = ?", (file_id,)).fetchone()
            if not file_info:
                return jsonify({'status': 'error', 'message': 'File not found in the database.'}), 404

            old_path = file_info['path']
            old_name = file_info['name']
            
            # Preserve the original extension
            _, old_ext = os.path.splitext(old_name)
            new_name_base, new_ext = os.path.splitext(new_name)
            if not new_ext: # If user didn't provide an extension, use the old one
                final_new_name = new_name + old_ext
            else:
                final_new_name = new_name

            if final_new_name == old_name:
                return jsonify({'status': 'error', 'message': 'The new name is the same as the old one.'}), 400

            file_dir = os.path.dirname(old_path)
            new_path = os.path.join(file_dir, final_new_name)

            if os.path.exists(new_path):
                return jsonify({'status': 'error', 'message': f'A file named "{final_new_name}" already exists in this folder.'}), 409

            # Perform the rename and database update
            os.rename(old_path, new_path)
            new_id = hashlib.md5(new_path.encode()).hexdigest()
            conn.execute("UPDATE files SET id = ?, path = ?, name = ? WHERE id = ?", (new_id, new_path, final_new_name, file_id))
            conn.commit()

            return jsonify({
                'status': 'success',
                'message': 'File renamed successfully.',
                'new_name': final_new_name,
                'new_id': new_id
            })

    except OSError as e:
        print(f"ERROR: OS error during file rename for {file_id}: {e}")
        return jsonify({'status': 'error', 'message': f'A system error occurred during rename: {e}'}), 500
    except Exception as e:
        print(f"ERROR: Generic error during file rename for {file_id}: {e}")
        return jsonify({'status': 'error', 'message': f'An unexpected error occurred: {e}'}), 500

def _get_video_mimetype(filepath):
    """Get mimetype for video files."""
    ext = os.path.splitext(filepath)[1].lower()
    mimetypes = {
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.mkv': 'video/x-matroska',
        '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo'
    }
    return mimetypes.get(ext, 'video/mp4')

def _stream_video(filepath, start, chunk_size=8192):
    """Generator to stream video file in chunks."""
    with open(filepath, 'rb') as f:
        f.seek(start)
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk

@app.route('/galleryout/file/<string:file_id>')
def serve_file(file_id):
    filepath = get_file_info_from_db(file_id, 'path')
    ext = os.path.splitext(filepath)[1].lower()

    # Handle video files with Range request support for streaming
    video_exts = ['.mp4', '.mkv', '.webm', '.mov', '.avi']
    if ext in video_exts:
        file_size = os.path.getsize(filepath)
        range_header = request.headers.get('Range')

        if range_header:
            # Parse Range header (e.g., "bytes=0-1023")
            byte_start = 0
            byte_end = file_size - 1

            match = re.match(r'bytes=(\d*)-(\d*)', range_header)
            if match:
                start_str, end_str = match.groups()
                if start_str:
                    byte_start = int(start_str)
                if end_str:
                    byte_end = min(int(end_str), file_size - 1)

            content_length = byte_end - byte_start + 1

            # Stream the requested range
            def generate():
                with open(filepath, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            response = Response(
                generate(),
                status=206,
                mimetype=_get_video_mimetype(filepath),
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = content_length
            return response
        else:
            # No Range header - stream entire file
            response = Response(
                _stream_video(filepath, 0),
                mimetype=_get_video_mimetype(filepath),
                direct_passthrough=True
            )
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = file_size
            return response

    # Handle images (non-video files)
    if filepath.lower().endswith('.webp'):
        return send_file(filepath, mimetype='image/webp', conditional=True)
    return send_file(filepath, conditional=True)

@app.route('/galleryout/download/<string:file_id>')
def download_file(file_id):
    filepath = get_file_info_from_db(file_id, 'path')
    return send_file(filepath, as_attachment=True)

@app.route('/galleryout/workflow/<string:file_id>')
def download_workflow(file_id):
    info = get_file_info_from_db(file_id)
    filepath = info['path']
    original_filename = info['name']
    workflow_json = extract_workflow(filepath)
    if workflow_json:
        base_name, _ = os.path.splitext(original_filename)
        new_filename = f"{base_name}.json"
        headers = {'Content-Disposition': f'attachment;filename="{new_filename}"'}
        return Response(workflow_json, mimetype='application/json', headers=headers)
    abort(404)

def _extract_api_workflow(filepath):
    """
    Extracts API format workflow from a file, preferring 'prompt' over 'workflow'.
    Returns (api_workflow_dict, ui_workflow_dict) - either can be None.
    """
    ext = os.path.splitext(filepath)[1].lower()
    video_exts = ['.mp4', '.mkv', '.webm', '.mov', '.avi']

    api_workflow = None
    ui_workflow = None

    def is_api_format(data):
        """Check if data is an API format workflow (dict with node IDs as keys, each having class_type)"""
        if not isinstance(data, dict):
            return False
        for v in data.values():
            if isinstance(v, dict) and 'class_type' in v:
                return True
        return False

    def is_ui_format(data):
        """Check if data is a UI format workflow (has 'nodes' array)"""
        return isinstance(data, dict) and 'nodes' in data

    def check_data(data, tag_name=None):
        nonlocal api_workflow, ui_workflow
        if not isinstance(data, dict):
            return

        # Direct check: is this data itself a workflow?
        if is_api_format(data) and not api_workflow:
            api_workflow = data
        elif is_ui_format(data) and not ui_workflow:
            ui_workflow = data

        # Also check nested keys for wrapped formats
        for key in ['prompt', 'workflow']:
            nested = data.get(key)
            if nested:
                if is_api_format(nested) and not api_workflow:
                    api_workflow = nested
                elif is_ui_format(nested) and not ui_workflow:
                    ui_workflow = nested

    if ext in video_exts:
        ffprobe_path = FFPROBE_EXECUTABLE_PATH or find_ffprobe_path()
        if ffprobe_path:
            try:
                cmd = [ffprobe_path, '-v', 'quiet', '-print_format', 'json', '-show_format', filepath]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                        errors='ignore', check=True,
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                data = json.loads(result.stdout)
                if 'format' in data and 'tags' in data['format']:
                    for tag_name, value in data['format']['tags'].items():
                        if isinstance(value, str) and value.strip().startswith('{'):
                            try:
                                parsed = json.loads(value)
                                check_data(parsed, tag_name)
                            except:
                                continue
            except Exception:
                pass
    else:
        try:
            with Image.open(filepath) as img:
                for key in ['prompt', 'workflow']:
                    val = img.info.get(key)
                    if val:
                        try:
                            parsed = json.loads(val)
                            check_data(parsed, key)
                        except:
                            continue
        except Exception:
            pass

    return api_workflow, ui_workflow

def _convert_ui_workflow_to_api(ui_workflow):
    """
    Attempts to convert a UI format workflow (with 'nodes' array) to API format.
    Returns the API format workflow dict or None if conversion fails.

    Note: This is a best-effort conversion. Complex workflows with custom widgets
    may not convert perfectly.
    """
    try:
        if isinstance(ui_workflow, str):
            ui_workflow = json.loads(ui_workflow)

        if 'nodes' not in ui_workflow:
            return None

        api_workflow = {}
        nodes = ui_workflow.get('nodes', [])
        links = ui_workflow.get('links', [])

        # Build a link lookup: link_id -> (source_node_id, source_slot)
        link_map = {}
        for link in links:
            if len(link) >= 4:
                link_id, source_node, source_slot, target_node = link[0], link[1], link[2], link[3]
                link_map[link_id] = (source_node, source_slot)

        for node in nodes:
            node_id = str(node.get('id'))
            node_type = node.get('type')

            if not node_type:
                continue

            # Build inputs from widgets_values and connected links
            inputs = {}

            # First, add widget values
            widgets_values = node.get('widgets_values', [])
            param_names = NODE_PARAM_NAMES.get(node_type, [])

            for i, value in enumerate(widgets_values):
                if i < len(param_names):
                    inputs[param_names[i]] = value
                else:
                    # Use generic parameter names for unknown widgets
                    inputs[f'widget_{i}'] = value

            # Then handle input connections
            node_inputs = node.get('inputs', [])
            for inp in node_inputs:
                inp_name = inp.get('name')
                link_id = inp.get('link')
                if inp_name and link_id and link_id in link_map:
                    source_node, source_slot = link_map[link_id]
                    inputs[inp_name] = [str(source_node), source_slot]

            api_workflow[node_id] = {
                'class_type': node_type,
                'inputs': inputs
            }

            # Preserve _meta if present
            if 'title' in node:
                api_workflow[node_id]['_meta'] = {'title': node['title']}

        return api_workflow

    except Exception as e:
        print(f"ERROR: Failed to convert UI workflow to API format: {e}")
        return None

@app.route('/galleryout/send_to_comfyui/<string:file_id>', methods=['POST'])
def send_to_comfyui(file_id):
    """
    Sends a workflow to the configured external workflow tool.

    Request body (optional):
    {
        "action": "queue" | "load",  # default: "queue"
        "client_id": "optional_client_id"
    }

    Actions:
    - "queue": Queues the workflow for immediate execution
    - "load": Returns the workflow data for loading in the workflow tool UI
    """
    try:
        filepath = get_file_info_from_db(file_id, 'path')

        # Get request parameters
        data = request.json or {}
        action = data.get('action', 'queue')
        client_id = data.get('client_id', '')

        # Extract both API and UI format workflows
        api_workflow, ui_workflow = _extract_api_workflow(filepath)

        if not api_workflow and not ui_workflow:
            return jsonify({
                'status': 'error',
                'message': 'No workflow found in this file.'
            }), 404

        if action == 'load':
            # Return workflow for loading in workflow tool UI (prefer UI format)
            workflow_to_load = ui_workflow or api_workflow
            return jsonify({
                'status': 'success',
                'action': 'load',
                'workflow': workflow_to_load,
                'format': 'ui' if ui_workflow else 'api',
                'comfyui_url': COMFYUI_URL,
                'message': 'Workflow ready to load.'
            })

        # Action: queue - Send workflow for execution (need API format)
        if not api_workflow and ui_workflow:
            # Try to convert UI format to API format
            api_workflow = _convert_ui_workflow_to_api(ui_workflow)

            if not api_workflow:
                return jsonify({
                    'status': 'error',
                    'message': 'This workflow is in UI format and could not be converted to API format. '
                               'Please use the "load" action to open it in the workflow tool instead.',
                    'format': 'ui',
                    'suggestion': 'load'
                }), 400

        # Prepare the prompt payload
        prompt_payload = {
            'prompt': api_workflow
        }

        if client_id:
            prompt_payload['client_id'] = client_id

        # Send to workflow tool
        comfyui_prompt_url = f"{COMFYUI_URL.rstrip('/')}/prompt"

        req = urllib.request.Request(
            comfyui_prompt_url,
            data=json.dumps(prompt_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                response_data = json.loads(response.read().decode('utf-8'))

                return jsonify({
                    'status': 'success',
                    'action': 'queue',
                    'message': 'Workflow sent successfully!',
                    'prompt_id': response_data.get('prompt_id'),
                    'number': response_data.get('number'),
                    'comfyui_url': COMFYUI_URL
                })

        except urllib.error.HTTPError as e:
            # HTTPError must come before URLError (it's a subclass)
            error_body = e.read().decode('utf-8', errors='ignore')
            return jsonify({
                'status': 'error',
                'message': f'Workflow tool returned an error: {e.code} - {error_body}'
            }), e.code

        except urllib.error.URLError as e:
            return jsonify({
                'status': 'error',
                'message': f'Could not connect to workflow tool at {COMFYUI_URL}. '
                           f'Is it running? Error: {str(e.reason)}'
            }), 503

    except json.JSONDecodeError:
        return jsonify({
            'status': 'error',
            'message': 'Invalid workflow JSON in the file.'
        }), 400
    except Exception as e:
        print(f"ERROR sending workflow for {file_id}: {e}")
        return jsonify({
            'status': 'error',
            'message': f'An unexpected error occurred: {str(e)}'
        }), 500

@app.route('/galleryout/comfyui_status')
def comfyui_status():
    """
    Checks if the workflow tool is reachable and returns its status.
    Useful for the frontend to show connection status.
    """
    try:
        status_url = f"{COMFYUI_URL.rstrip('/')}/system_stats"

        req = urllib.request.Request(status_url, method='GET')

        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))

            return jsonify({
                'status': 'online',
                'comfyui_url': COMFYUI_URL,
                'system_stats': data
            })

    except urllib.error.URLError:
        return jsonify({
            'status': 'offline',
            'comfyui_url': COMFYUI_URL,
            'message': 'Workflow tool is not reachable.'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'comfyui_url': COMFYUI_URL,
            'message': str(e)
        })

@app.route('/galleryout/node_summary/<string:file_id>')
def get_node_summary(file_id):
    try:
        filepath = get_file_info_from_db(file_id, 'path')
        workflow_json = extract_workflow(filepath)
        if not workflow_json:
            return jsonify({'status': 'error', 'message': 'Workflow not found for this file.'}), 404
        summary_data = generate_node_summary(workflow_json)
        if summary_data is None:
            return jsonify({'status': 'error', 'message': 'Failed to parse workflow JSON.'}), 400
        return jsonify({'status': 'success', 'summary': summary_data})
    except Exception as e:
        print(f"ERROR generating node summary for {file_id}: {e}")
        return jsonify({'status': 'error', 'message': f'An internal error occurred: {e}'}), 500

@app.route('/galleryout/compare_workflows', methods=['POST'])
def compare_workflows():
    """Compare node summaries for multiple files."""
    try:
        data = request.json
        file_ids = data.get('file_ids', [])
        if len(file_ids) < 2:
            return jsonify({'status': 'error', 'message': 'At least 2 files required for comparison.'}), 400

        results = []
        for file_id in file_ids:
            try:
                info = get_file_info_from_db(file_id)
                filepath = info['path']
                filename = info['name']
                workflow_json = extract_workflow(filepath)
                if workflow_json:
                    summary_data = generate_node_summary(workflow_json)
                    results.append({
                        'id': file_id,
                        'name': filename,
                        'summary': summary_data if summary_data else [],
                        'has_workflow': True
                    })
                else:
                    results.append({
                        'id': file_id,
                        'name': filename,
                        'summary': [],
                        'has_workflow': False
                    })
            except Exception as e:
                print(f"ERROR getting summary for {file_id}: {e}")
                results.append({
                    'id': file_id,
                    'name': f'Error: {file_id}',
                    'summary': [],
                    'has_workflow': False
                })

        return jsonify({'status': 'success', 'files': results})
    except Exception as e:
        print(f"ERROR in compare_workflows: {e}")
        return jsonify({'status': 'error', 'message': f'An internal error occurred: {e}'}), 500

@app.route('/galleryout/thumbnail/<string:file_id>')
def serve_thumbnail(file_id):
    info = get_file_info_from_db(file_id)
    filepath, mtime = info['path'], info['mtime']
    file_hash = hashlib.md5((filepath + str(mtime)).encode()).hexdigest()
    existing_thumbnails = glob.glob(os.path.join(THUMBNAIL_CACHE_DIR, f"{file_hash}.*"))
    if existing_thumbnails:
        response = send_file(existing_thumbnails[0], conditional=True)
        response.headers['Cache-Control'] = 'public, max-age=86400'  # Cache for 24 hours
        return response
    print(f"WARN: Thumbnail not found for {os.path.basename(filepath)}, generating...")
    cache_path = create_thumbnail(filepath, file_hash, info['type'])
    if cache_path and os.path.exists(cache_path):
        response = send_file(cache_path, conditional=True)
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return response
    return "Thumbnail generation failed", 404

@app.route('/favicon.ico')
def favicon():
    return send_file('static/galleryout/favicon.ico')

@app.route('/galleryout/input_file/<path:filename>')
def serve_input_file(filename):
    """Serves input files directly from the source media input folder."""
    try:
        # Prevent path traversal
        filename = secure_filename(filename)
        filepath = os.path.abspath(os.path.join(BASE_INPUT_PATH, filename))
        if not filepath.startswith(os.path.abspath(BASE_INPUT_PATH)):
            abort(403)
        
        # For webp, frocing the correct mimetype
        if filename.lower().endswith('.webp'):
            return send_from_directory(BASE_INPUT_PATH, filename, mimetype='image/webp', as_attachment=False)
        
        # For all the other files, I let Flask guessing the mimetype, but disable the attachment, just a lil trick
        return send_from_directory(BASE_INPUT_PATH, filename, as_attachment=False)
    except Exception as e:
        abort(404)

# --- SMASH CUT GENERATOR ---
smashcut_jobs = {}

def background_smashcut_task(job_id, video_ids, options):
    """
    Background task to generate smash cut video using ffmpeg.

    Options:
    - resolution: "original" | "720p" | "1080p" | {"width": N, "height": N}
    - fps: "original" | 24 | 30 | 60
    - quality: "low" | "medium" | "high"
    - output_filename: optional custom filename
    """
    try:
        ffmpeg_path = find_ffmpeg_path()
        if not ffmpeg_path:
            smashcut_jobs[job_id] = {'status': 'error', 'message': 'ffmpeg not found'}
            return

        os.makedirs(SMASHCUT_OUTPUT_DIR, exist_ok=True)

        # Get video file paths from database
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(video_ids))
            videos = conn.execute(
                f"SELECT id, path, name FROM files WHERE id IN ({placeholders})",
                video_ids
            ).fetchall()

        if not videos:
            smashcut_jobs[job_id] = {'status': 'error', 'message': 'No videos found'}
            return

        # Reorder videos to match the order in video_ids
        video_map = {v['id']: v for v in videos}
        ordered_videos = [video_map[vid] for vid in video_ids if vid in video_map]

        # Create concat file list
        concat_list_path = os.path.join(SMASHCUT_OUTPUT_DIR, f"{job_id}_concat.txt")
        output_filename = options.get('output_filename', f"smashcut_{job_id}.mp4")
        if not output_filename.endswith('.mp4'):
            output_filename += '.mp4'
        output_filename = secure_filename(output_filename)
        output_path = os.path.join(SMASHCUT_OUTPUT_DIR, output_filename)

        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for video in ordered_videos:
                # Escape single quotes in path for ffmpeg
                escaped_path = video['path'].replace("'", "'\\''")
                f.write(f"file '{escaped_path}'\n")

        # Build ffmpeg command
        cmd = [ffmpeg_path, '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path]

        # Resolution handling
        resolution = options.get('resolution', 'original')
        vf_filters = []
        if resolution != 'original':
            if resolution == '720p':
                vf_filters.append('scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2')
            elif resolution == '1080p':
                vf_filters.append('scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2')
            elif isinstance(resolution, dict):
                w = resolution.get('width', 1920)
                h = resolution.get('height', 1080)
                vf_filters.append(f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2')

        if vf_filters:
            cmd.extend(['-vf', ','.join(vf_filters)])

        # FPS handling
        fps = options.get('fps', 'original')
        if fps != 'original':
            cmd.extend(['-r', str(fps)])

        # Quality handling with compression presets
        # Quality levels balance visual quality vs file size:
        # - 'minimal': Very small files, lower quality (social media draft, previews)
        # - 'low': Small files, acceptable quality (quick sharing)
        # - 'medium': Balanced quality/size (default, general use)
        # - 'high': Good quality, larger files (final output)
        # - 'best': Maximum quality, largest files (archival)
        quality = options.get('quality', 'medium')
        compression_presets = {
            'minimal': {'crf': 32, 'preset': 'faster', 'audio_bitrate': '96k'},
            'low': {'crf': 28, 'preset': 'fast', 'audio_bitrate': '128k'},
            'medium': {'crf': 23, 'preset': 'medium', 'audio_bitrate': '192k'},
            'high': {'crf': 18, 'preset': 'slow', 'audio_bitrate': '256k'},
            'best': {'crf': 15, 'preset': 'slower', 'audio_bitrate': '320k'},
        }
        preset = compression_presets.get(quality, compression_presets['medium'])
        cmd.extend(['-c:v', 'libx264', '-crf', str(preset['crf']), '-preset', preset['preset']])

        # Audio settings (configurable based on quality)
        cmd.extend(['-c:a', 'aac', '-b:a', preset['audio_bitrate']])

        cmd.append(output_path)

        # Update job status
        smashcut_jobs[job_id] = {
            'status': 'processing',
            'message': f'Processing {len(ordered_videos)} videos...',
            'progress': 0,
            'total': len(ordered_videos)
        }

        # Run ffmpeg
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        stdout, stderr = process.communicate()

        # Cleanup concat file
        try:
            os.remove(concat_list_path)
        except:
            pass

        if process.returncode == 0:
            smashcut_jobs[job_id] = {
                'status': 'ready',
                'message': 'Smash cut ready for download',
                'filename': output_filename,
                'path': output_path,
                'size': os.path.getsize(output_path)
            }
        else:
            error_msg = stderr.decode('utf-8', errors='ignore')[-500:]
            smashcut_jobs[job_id] = {
                'status': 'error',
                'message': f'ffmpeg failed: {error_msg}'
            }

    except Exception as e:
        print(f"Smashcut Error: {e}")
        smashcut_jobs[job_id] = {'status': 'error', 'message': str(e)}


@app.route('/galleryout/smashcut')
def smashcut_page():
    """Serves the smash cut generator page."""
    folders = get_dynamic_folder_config(force_refresh=True)

    return render_template('smashcut.html',
                          folders=folders)


@app.route('/galleryout/smashcut/input_files')
def smashcut_get_input_files():
    """
    Returns list of unique input file references found across all video workflows.
    Used to populate the input file filter dropdown.
    Accepts optional folder_keys query parameter to filter by specific folders.
    """
    all_input_files = set()
    folder_keys = request.args.getlist('folder_keys')

    folders = get_dynamic_folder_config()

    with get_db_connection() as conn:
        conditions = ["type = 'video'", "input_files != '[]'"]
        params = []

        # Filter by folders if specified
        if folder_keys:
            folder_conditions = []
            for key in folder_keys:
                if key in folders:
                    folder_path = folders[key]['path']
                    folder_conditions.append("path LIKE ?")
                    params.append(folder_path + os.sep + '%')
            if folder_conditions:
                conditions.append(f"({' OR '.join(folder_conditions)})")

        query = f"SELECT DISTINCT input_files FROM files WHERE {' AND '.join(conditions)}"
        rows = conn.execute(query, params).fetchall()

    for row in rows:
        try:
            input_files = json.loads(row['input_files'] or '[]')
            all_input_files.update(input_files)
        except:
            pass

    return jsonify({
        'status': 'success',
        'input_files': sorted(list(all_input_files))
    })


@app.route('/galleryout/input_file_thumbnail/<path:filename>')
def serve_input_file_thumbnail(filename):
    """Serves thumbnails for input files from the source media folder."""
    try:
        # Normalize the path and prevent traversal
        # Decode URL-encoded characters and normalize path separators
        clean_filename = filename.replace('\\', '/')

        # Remove any leading slashes or dots that could be used for traversal
        while clean_filename.startswith('/') or clean_filename.startswith('./'):
            clean_filename = clean_filename.lstrip('/').lstrip('./')

        # Check for path traversal attempts
        if '..' in clean_filename:
            abort(403)

        # Build the full path
        filepath = os.path.abspath(os.path.join(BASE_INPUT_PATH, clean_filename))
        base_path = os.path.abspath(BASE_INPUT_PATH)

        # Ensure the path is within the input directory
        if not filepath.startswith(base_path + os.sep) and filepath != base_path:
            abort(403)

        if not os.path.isfile(filepath):
            abort(404)

        # Generate a cache key based on the file path and modification time
        mtime = os.path.getmtime(filepath)
        file_hash = hashlib.md5((filepath + str(mtime)).encode()).hexdigest()

        # Check for existing thumbnail
        existing_thumbnails = glob.glob(os.path.join(THUMBNAIL_CACHE_DIR, f"input_{file_hash}.*"))
        if existing_thumbnails:
            response = send_file(existing_thumbnails[0], conditional=True)
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response

        # Determine file type
        ext = os.path.splitext(filepath)[1].lower()
        if ext in ['.mp4', '.webm', '.mov', '.mkv', '.avi']:
            file_type = 'video'
        else:
            file_type = 'image'

        # Generate thumbnail
        cache_path = create_thumbnail(filepath, f"input_{file_hash}", file_type)
        if cache_path and os.path.exists(cache_path):
            response = send_file(cache_path, conditional=True)
            response.headers['Cache-Control'] = 'public, max-age=86400'
            return response

        # Fallback: serve the original file for images
        if file_type == 'image':
            return send_from_directory(os.path.dirname(filepath), os.path.basename(filepath), as_attachment=False)

        return "Thumbnail generation failed", 404
    except Exception as e:
        print(f"Error serving input file thumbnail: {e}")
        abort(404)


@app.route('/galleryout/smashcut/input_directory_files')
def smashcut_get_input_directory_files():
    """
    Returns list of all media files in the input directory and its subfolders.
    Used to allow browsing input files directly (not just those referenced in workflows).
    """
    valid_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.jfif',
                        '.mp4', '.webm', '.mov', '.mkv', '.avi'}
    all_files = []

    try:
        base_path = os.path.abspath(BASE_INPUT_PATH)
        for root, dirs, files in os.walk(BASE_INPUT_PATH):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for filename in files:
                if filename.startswith('.'):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext in valid_extensions:
                    full_path = os.path.join(root, filename)
                    # Get relative path from input directory
                    rel_path = os.path.relpath(full_path, BASE_INPUT_PATH)
                    # Normalize path separators
                    rel_path = rel_path.replace('\\', '/')
                    all_files.append(rel_path)

        # Sort files, with root directory files first, then by path
        all_files.sort(key=lambda x: (x.count('/'), x.lower()))

        return jsonify({
            'status': 'success',
            'files': all_files,
            'count': len(all_files)
        })
    except Exception as e:
        print(f"Error listing input directory files: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'files': []
        })


@app.route('/galleryout/smashcut/filter', methods=['POST'])
def smashcut_filter():
    """
    Filters videos based on smash cut criteria.
    """
    data = request.json or {}
    folder_keys = data.get('folder_keys', [])
    input_files_filter = data.get('input_files', [])
    text_pattern = data.get('text_pattern', '').strip()
    favorites_only = data.get('favorites_only', False)

    folders = get_dynamic_folder_config()

    with get_db_connection() as conn:
        conditions = ["type = 'video'"]
        params = []

        # Folder filtering
        if folder_keys:
            folder_conditions = []
            for key in folder_keys:
                if key in folders:
                    folder_path = folders[key]['path']
                    folder_conditions.append("path LIKE ?")
                    params.append(folder_path + os.sep + '%')
            if folder_conditions:
                conditions.append(f"({' OR '.join(folder_conditions)})")

        # Favorites filtering
        if favorites_only:
            conditions.append("is_favorite = 1")

        query = f"SELECT * FROM files WHERE {' AND '.join(conditions)} ORDER BY mtime DESC"
        videos = conn.execute(query, params).fetchall()

    # Post-filter by input files and text pattern
    filtered_videos = []
    total_duration_seconds = 0

    for video in videos:
        video_dict = dict(video)

        # Check input files filter (using cached data from database)
        if input_files_filter:
            try:
                video_input_files = json.loads(video_dict.get('input_files') or '[]')
            except:
                video_input_files = []
            if not any(inp in video_input_files for inp in input_files_filter):
                continue

        # Check text pattern filter (requires workflow inspection)
        if text_pattern:
            workflow_json = extract_workflow(video_dict['path'])
            if not workflow_json or text_pattern.lower() not in workflow_json.lower():
                continue

        # Parse duration
        duration_seconds = 0
        if video_dict['duration']:
            parts = video_dict['duration'].split(':')
            try:
                if len(parts) == 2:
                    duration_seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass

        filtered_videos.append({
            'id': video_dict['id'],
            'name': video_dict['name'],
            'path': video_dict['path'],
            'duration': video_dict['duration'],
            'duration_seconds': duration_seconds,
            'dimensions': video_dict['dimensions'],
            'thumbnail_url': f"/galleryout/thumbnail/{video_dict['id']}",
            'is_favorite': bool(video_dict.get('is_favorite', 0))
        })
        total_duration_seconds += duration_seconds

    return jsonify({
        'status': 'success',
        'videos': filtered_videos,
        'total_duration': format_duration(total_duration_seconds),
        'total_count': len(filtered_videos)
    })


@app.route('/galleryout/smashcut/generate', methods=['POST'])
def smashcut_generate():
    """
    Starts smash cut generation.
    """
    data = request.json or {}
    video_ids = data.get('video_ids', [])
    options = data.get('options', {})

    if not video_ids:
        return jsonify({'status': 'error', 'message': 'No videos specified'}), 400

    if not find_ffmpeg_path():
        return jsonify({'status': 'error', 'message': 'ffmpeg not found on system. Please install ffmpeg to use this feature.'}), 500

    job_id = str(uuid.uuid4())
    smashcut_jobs[job_id] = {'status': 'queued', 'message': 'Job queued...'}

    thread = threading.Thread(target=background_smashcut_task, args=(job_id, video_ids, options))
    thread.daemon = True
    thread.start()

    return jsonify({'status': 'success', 'job_id': job_id, 'message': 'Smash cut generation started.'})


@app.route('/galleryout/smashcut/status/<job_id>')
def smashcut_status(job_id):
    """Check status of smash cut generation job."""
    job = smashcut_jobs.get(job_id)
    if not job:
        return jsonify({'status': 'error', 'message': 'Job not found'}), 404

    response_data = job.copy()
    if job['status'] == 'ready' and 'filename' in job:
        response_data['download_url'] = url_for('smashcut_download', filename=job['filename'])

    return jsonify(response_data)


@app.route('/galleryout/smashcut/download/<filename>')
def smashcut_download(filename):
    """Serve generated smash cut file for download."""
    filename = secure_filename(filename)
    filepath = os.path.join(SMASHCUT_OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_from_directory(SMASHCUT_OUTPUT_DIR, filename, as_attachment=True)


def print_startup_banner():
    banner = rf"""
{Colors.GREEN}{Colors.BOLD}  ____                       _        _                _
 / ___| _ __ ___   __ _ _ __| |_     / \   ___ ___  ___| |_
 \___ \| '_ ` _ \ / _` | '__| __|   / _ \ / __/ __|/ _ \ __|
  ___) | | | | | | (_| | |  | |_   / ___ \\__ \__ \  __/ |_
 |____/|_| |_| |_|\__,_|_|   \__| /_/   \_\___/___/\___|\__|
   ____       _ _
  / ___| __ _| | | ___ _ __ _   _
 | |  _ / _` | | |/ _ \ '__| | | |
 | |_| | (_| | | |  __/ |  | |_| |
  \____|\__,_|_|_|\___|_|   \__, |
                             |___/ {Colors.RESET}
    """
    print(banner)
    print(f"   {Colors.BOLD}Smart Asset Gallery{Colors.RESET}")
    print(f"   Community Action Lehigh Valley - Marketing Team")
    print(f"   Version    : {Colors.YELLOW}{APP_VERSION}{Colors.RESET} ({APP_VERSION_DATE})")
    print(f"   GitHub     : {Colors.CYAN}{GITHUB_REPO_URL}{Colors.RESET}")
    print("")
    
def check_for_updates():
    """Checks the GitHub repo for a newer version without external libs."""
    print("Checking for updates...", end=" ", flush=True)
    try:
        # Timeout (3s) not blocking start if no internet connection
        with urllib.request.urlopen(GITHUB_RAW_URL, timeout=3) as response:
            content = response.read().decode('utf-8')
            # Finding string "Version: X.XX" 
            match = re.search(r'Version:\s*([0-9.]+)', content)
            
            if match:
                remote_version = float(match.group(1))
                if remote_version > APP_VERSION:
                    print(f"\n{Colors.YELLOW}{Colors.BOLD}NOTICE: A new version ({remote_version}) is available!{Colors.RESET}")
                    print(f"Please update from: {GITHUB_REPO_URL}\n")
                else:
                    print("You are up to date.")
            else:
                print("Could not parse remote version.")
                
    except Exception:
        print("Skipped (Offline or GitHub unreachable).")

# --- STARTUP CHECKS AND MAIN ENTRY POINT ---
def show_config_error_and_exit(path):
    """Shows a critical error message and exits the program."""
    msg = (
        f"❌ CRITICAL ERROR: The specified path does not exist or is not accessible:\n\n"
        f"👉 {path}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. If you are launching via a script (e.g., .bat file), please edit it and set the correct 'BASE_OUTPUT_PATH' variable.\n"
        f"2. Or edit 'smartgallery.py' (USER CONFIGURATION section) and ensure the path points to an existing folder.\n\n"
        f"The program cannot continue and will now exit."
    )
    
    if TKINTER_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showerror("Smart Asset Gallery - Configuration Error", msg)
        root.destroy()
    else:
        # Fallback for headless environments (Docker, etc.)
        print(f"\n{Colors.RED}{Colors.BOLD}" + "="*70 + f"{Colors.RESET}")
        print(f"{Colors.RED}{Colors.BOLD}{msg}{Colors.RESET}")
        print(f"{Colors.RED}{Colors.BOLD}" + "="*70 + f"{Colors.RESET}\n")
    
    sys.exit(1)

def show_ffmpeg_warning():
    """Shows a non-blocking warning message for missing FFmpeg."""
    msg = (
        "WARNING: FFmpeg/FFprobe not found\n\n"
        "The system uses the 'ffprobe' utility to analyze video files. "
        "It seems it is missing or not configured correctly.\n\n"
        "CONSEQUENCES:\n"
        "❌ Video metadata extraction will be limited.\n"
        "✅ Gallery browsing, playback, and image features will still work perfectly.\n\n"
        "To fix this, install FFmpeg or check the 'FFPROBE_MANUAL_PATH' in the configuration."
    )
    
    if TKINTER_AVAILABLE:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showwarning("Smart Asset Gallery - Feature Limitation", msg)
        root.destroy()
    else:
        # Fallback for headless environments (Docker, etc.)
        print(f"\n{Colors.YELLOW}{Colors.BOLD}" + "="*70 + f"{Colors.RESET}")
        print(f"{Colors.YELLOW}{msg}{Colors.RESET}")
        print(f"{Colors.YELLOW}{Colors.BOLD}" + "="*70 + f"{Colors.RESET}\n")


# --- AUTO-INITIALIZATION FOR WSGI ---
# Initialize gallery on module import (for gunicorn, uwsgi, etc.)
# This ensures social features and authentication work in production.
_gallery_initialized = False

def _ensure_initialized():
    global _gallery_initialized
    if not _gallery_initialized:
        initialize_gallery()
        _gallery_initialized = True

# Initialize when module is imported (required for WSGI servers)
_ensure_initialized()


if __name__ == '__main__':

    print_startup_banner()
    check_for_updates()
    print_configuration()

    # --- CHECK: OUTPUT PATH CHECK (Auto-create for containers/deployments) ---
    if not os.path.exists(BASE_OUTPUT_PATH):
        try:
            os.makedirs(BASE_OUTPUT_PATH, exist_ok=True)
            print(f"{Colors.GREEN}Created output directory: {BASE_OUTPUT_PATH}{Colors.RESET}")
        except OSError as e:
            print(f"{Colors.RED}Failed to create output directory: {e}{Colors.RESET}")
            show_config_error_and_exit(BASE_OUTPUT_PATH)

    # --- CHECK: INPUT PATH CHECK (Auto-create for containers/deployments) ---
    if not os.path.exists(BASE_INPUT_PATH):
        try:
            os.makedirs(BASE_INPUT_PATH, exist_ok=True)
            print(f"{Colors.GREEN}Created input directory: {BASE_INPUT_PATH}{Colors.RESET}")
        except OSError as e:
            # Input directory is optional, just warn
            print(f"{Colors.YELLOW}{Colors.BOLD}WARNING: Input Path not found and could not be created!{Colors.RESET}")
            print(f"{Colors.YELLOW}   Path: '{BASE_INPUT_PATH}'{Colors.RESET}")
            print(f"{Colors.YELLOW}   Error: {e}{Colors.RESET}")
            print(f"{Colors.YELLOW}   > Source media lookups will be DISABLED.{Colors.RESET}")
            print(f"{Colors.YELLOW}   > The gallery will still function normally.{Colors.RESET}\n")
    
    # Ensure gallery is initialized (may already be done on module import)
    _ensure_initialized()
    
    # --- CHECK: FFMPEG WARNING ---
    if not FFPROBE_EXECUTABLE_PATH:
        # Check if we are in a headless environment (like Docker) where tk might fail
        if os.environ.get('DISPLAY') or os.name == 'nt':
            try:
                show_ffmpeg_warning()
            except:
                print(f"{Colors.RED}WARNING: FFmpeg not found. Video metadata extraction disabled.{Colors.RESET}")
        else:
            print(f"{Colors.RED}WARNING: FFmpeg not found. Video metadata extraction disabled.{Colors.RESET}")

    print(f"{Colors.GREEN}{Colors.BOLD}🚀 Smart Asset Gallery started successfully!{Colors.RESET}")
    print(f"👉 Access URL: {Colors.CYAN}{Colors.BOLD}http://127.0.0.1:{SERVER_PORT}/galleryout/{Colors.RESET}")
    print(f"   (Press CTRL+C to stop)")
    
    app.run(host='0.0.0.0', port=SERVER_PORT, debug=False, threaded=True)