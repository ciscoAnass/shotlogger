import sys
import json
from pathlib import Path
from datetime import datetime
from functools import wraps
import getpass
import os
import hashlib
import hmac
import shutil
import zipfile
from io import BytesIO

from flask import (
    Flask, request, redirect, url_for,
    render_template_string, send_from_directory,
    session, abort, send_file
)
from werkzeug.utils import secure_filename


# ==========================================================
#  Config loading
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"

if not CONFIG_FILE.exists():
    raise SystemExit(f"Config file not found: {CONFIG_FILE}")

with CONFIG_FILE.open("r", encoding="utf-8") as f:
    cfg = json.load(f)

ROOT_FOLDER = Path(cfg.get("root_folder", "/home/youruser/shotlogger_data"))
WEB_USERNAME = cfg.get("web_username", "admin")
WEB_PASSWORD = cfg.get("web_password", "")  # legacy/plaintext (optional)
WEB_PASSWORD_HASH = cfg.get("web_password_hash", "")  # pbkdf2 hash
UPLOAD_PASSWORD = cfg.get("upload_password", "change_me")
SITE_NAME = cfg.get("site_name", "ShotLogger")
SESSION_SECRET = cfg.get("session_secret")  # optional

if not SESSION_SECRET:
    # fallback: derive from upload password (good enough for personal use)
    SESSION_SECRET = UPLOAD_PASSWORD + "_flask_secret"

ROOT_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = SESSION_SECRET.encode("utf-8")


# ==========================================================
#  Password hashing helpers (PBKDF2-SHA256)
# ==========================================================

def hash_password(plain_password: str, iterations: int = 200_000) -> str:
    """
    Hash a password using PBKDF2-HMAC-SHA256.

    Returns a string like:
      pbkdf2_sha256$200000$<salt_hex>$<hash_hex>
    """
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_hashed_password(plain_password: str, stored: str) -> bool:
    """
    Verify a password against a stored PBKDF2-SHA256 hash string.
    """
    try:
        algo, iter_str, salt_hex, hash_hex = stored.split("$", 3)
    except ValueError:
        return False

    if algo != "pbkdf2_sha256":
        return False

    try:
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        stored_hash = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False

    test_hash = hashlib.pbkdf2_hmac(
        "sha256",
        plain_password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(stored_hash, test_hash)


def verify_web_password(plain_password: str) -> bool:
    """
    Verify the admin web password.

    Priority:
      - If WEB_PASSWORD_HASH is set -> use PBKDF2.
      - Else -> fall back to plaintext WEB_PASSWORD comparison.
    """
    if WEB_PASSWORD_HASH:
        return verify_hashed_password(plain_password, WEB_PASSWORD_HASH)
    else:
        return plain_password == WEB_PASSWORD


def set_admin_password_interactive() -> None:
    """
    CLI helper: python tor_server.py --set-admin-password

    Prompts for a new admin web password, hashes it, and writes it
    into server_config.json as web_password_hash.
    """
    print("This will set (or reset) the ADMIN web password for the ShotLogger UI.")
    pw1 = getpass.getpass("New web password: ")
    pw2 = getpass.getpass("Repeat web password: ")

    if pw1 != pw2:
        print("Passwords do not match. Aborting.")
        return

    if not pw1:
        print("Password cannot be empty. Aborting.")
        return

    new_hash = hash_password(pw1)

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        current_cfg = json.load(f)

    current_cfg["web_password_hash"] = new_hash
    current_cfg["web_password"] = ""

    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(current_cfg, f, indent=4)

    print("Admin web password updated successfully.")
    print("You can now run the server normally or via gunicorn.")


# ==========================================================
#  Security helpers
# ==========================================================

@app.after_request
def set_security_headers(response):
    # Basic hardening
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline';"
    return response


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def validate_identifier(value: str) -> bool:
    """
    Avoid path traversal with very simple checks:
    - no / or \ characters
    - non-empty
    """
    if not value:
        return False
    return ("/" not in value) and ("\\" not in value)


# ==========================================================
#  HTML TEMPLATES (white UI + click-to-preview)
# ==========================================================
LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in – {{ site_name }}</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: 'Segoe UI', 'Google Sans', Roboto, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #ffffff;
      color: #202124;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .login-container {
      width: 100%;
      max-width: 450px;
      padding: 48px 40px 36px;
      background: #fff;
      border: 1px solid #dadce0;
      border-radius: 8px;
      box-shadow: 0 1px 3px rgba(60, 64, 67, 0.3);
    }
    .logo {
      text-align: center;
      margin-bottom: 24px;
    }
    .logo h1 {
      font-size: 24px;
      font-weight: 400;
      color: #202124;
      letter-spacing: -0.5px;
    }
    .logo p {
      font-size: 16px;
      color: #5f6368;
      margin-top: 8px;
    }
    .form-group {
      margin-bottom: 24px;
    }
    label {
      display: block;
      font-size: 14px;
      color: #5f6368;
      margin-bottom: 8px;
      font-weight: 500;
    }
    input[type="text"], input[type="password"] {
      width: 100%;
      padding: 13px 15px;
      border: 1px solid #dadce0;
      border-radius: 4px;
      font-size: 16px;
      color: #202124;
      transition: all 0.2s;
      background: #fff;
    }
    input[type="text"]:focus, input[type="password"]:focus {
      outline: none;
      border-color: #1a73e8;
      box-shadow: 0 0 0 2px rgba(26, 115, 232, 0.1);
    }
    .btn-primary {
      width: 100%;
      padding: 14px;
      background: #1a73e8;
      color: white;
      border: none;
      border-radius: 4px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      letter-spacing: 0.25px;
    }
    .btn-primary:hover {
      background: #1765cc;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    }
    .btn-primary:active {
      background: #1557b0;
    }
    .error {
      background: #fce8e6;
      color: #d93025;
      padding: 12px 16px;
      border-radius: 4px;
      font-size: 14px;
      margin-bottom: 20px;
      border-left: 3px solid #d93025;
    }
  </style>
</head>
<body>
  <div class="login-container">
    <div class="logo">
      <h1>{{ site_name }}</h1>
      <p>Sign in to continue</p>
    </div>
    {% if error %}
      <div class="error">{{ error }}</div>
    {% endif %}
    <form method="post">
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" autofocus required>
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" required>
      </div>
      <button type="submit" class="btn-primary">Sign in</button>
    </form>
  </div>
</body>
</html>
"""

INDEX_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ site_name }}</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: 'Segoe UI', 'Google Sans', Roboto, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #ffffff;
      color: #202124;
      min-height: 100vh;
    }
    .header {
      background: #ffffff;
      border-bottom: 1px solid #e8eaed;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .header h1 {
      font-size: 22px;
      font-weight: 400;
      color: #5f6368;
      letter-spacing: -0.5px;
    }
    .header-right {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .btn-logout {
      padding: 8px 24px;
      background: transparent;
      color: #1a73e8;
      border: 1px solid #dadce0;
      border-radius: 4px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      text-decoration: none;
      display: inline-block;
    }
    .btn-logout:hover {
      background: #f8f9fa;
      border-color: #1a73e8;
    }
    .main-container {
      max-width: 1440px;
      margin: 0 auto;
      padding: 32px 24px;
    }
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
    }
    .section-title {
      font-size: 28px;
      font-weight: 400;
      color: #202124;
    }
    .stats {
      font-size: 14px;
      color: #5f6368;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 16px;
    }
    .user-card {
      background: #ffffff;
      border: 1px solid #e8eaed;
      border-radius: 8px;
      padding: 20px;
      transition: all 0.2s;
      cursor: pointer;
      text-decoration: none;
      color: inherit;
      display: block;
    }
    .user-card:hover {
      box-shadow: 0 1px 3px rgba(60, 64, 67, 0.3), 0 4px 8px rgba(60, 64, 67, 0.15);
      border-color: #dadce0;
    }
    .user-icon {
      width: 48px;
      height: 48px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: white;
      font-size: 20px;
      font-weight: 500;
      margin-bottom: 16px;
    }
    .user-name {
      font-size: 16px;
      font-weight: 500;
      color: #202124;
      margin-bottom: 8px;
    }
    .user-stats {
      display: flex;
      gap: 16px;
      font-size: 13px;
      color: #5f6368;
    }
    .stat-item {
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .empty-state {
      text-align: center;
      padding: 80px 20px;
      color: #5f6368;
    }
    .empty-state-icon {
      font-size: 64px;
      margin-bottom: 16px;
      opacity: 0.3;
    }
    .empty-state-text {
      font-size: 16px;
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="header-left">
      <h1>{{ site_name }}</h1>
    </div>
    <div class="header-right">
      <a href="{{ url_for('logout') }}" class="btn-logout">Sign out</a>
    </div>
  </header>

  <div class="main-container">
    <div class="section-header">
      <h2 class="section-title">Users</h2>
      {% if users %}
      <div class="stats">{{ users|length }} user{% if users|length != 1 %}s{% endif %}</div>
      {% endif %}
    </div>

    {% if users %}
      <div class="grid">
        {% for u in users %}
          <a href="{{ url_for('view_user', username=u.username) }}" class="user-card">
            <div class="user-icon">{{ u.username[0]|upper }}</div>
            <div class="user-name">{{ u.username }}</div>
            <div class="user-stats">
              <span class="stat-item">{{ u.days }} days</span>
              <span class="stat-item">{{ u.files }} photos</span>
            </div>
          </a>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty-state">
        <div class="empty-state-icon">📷</div>
        <div class="empty-state-text">No users yet</div>
      </div>
    {% endif %}
  </div>
</body>
</html>
"""

USER_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ username }} – {{ site_name }}</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: 'Segoe UI', 'Google Sans', Roboto, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #ffffff;
      color: #202124;
      min-height: 100vh;
    }
    .header {
      background: #ffffff;
      border-bottom: 1px solid #e8eaed;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .btn-back {
      padding: 8px;
      background: transparent;
      border: none;
      color: #5f6368;
      cursor: pointer;
      font-size: 20px;
      text-decoration: none;
      display: flex;
      align-items: center;
      transition: color 0.2s;
    }
    .btn-back:hover {
      color: #202124;
    }
    .header h1 {
      font-size: 22px;
      font-weight: 400;
      color: #202124;
      letter-spacing: -0.5px;
    }
    .btn-logout {
      padding: 8px 24px;
      background: transparent;
      color: #1a73e8;
      border: 1px solid #dadce0;
      border-radius: 4px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      text-decoration: none;
      display: inline-block;
    }
    .btn-logout:hover {
      background: #f8f9fa;
      border-color: #1a73e8;
    }
    .main-container {
      max-width: 1440px;
      margin: 0 auto;
      padding: 32px 24px;
    }
    .section-header {
      margin-bottom: 24px;
    }
    .section-title {
      font-size: 28px;
      font-weight: 400;
      color: #202124;
      margin-bottom: 8px;
    }
    .section-subtitle {
      font-size: 14px;
      color: #5f6368;
    }
    .timeline {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .day-row {
      display: flex;
      gap: 8px;
      align-items: stretch;
    }
    .day-card {
      background: #ffffff;
      border: 1px solid #e8eaed;
      border-radius: 8px;
      padding: 20px 24px;
      transition: all 0.2s;
      cursor: pointer;
      text-decoration: none;
      color: inherit;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex: 1;
    }
    .day-card:hover {
      box-shadow: 0 1px 3px rgba(60, 64, 67, 0.3), 0 4px 8px rgba(60, 64, 67, 0.15);
      border-color: #dadce0;
    }
    .day-info {
      flex: 1;
    }
    .day-date {
      font-size: 16px;
      font-weight: 500;
      color: #202124;
      margin-bottom: 4px;
    }
    .day-count {
      font-size: 13px;
      color: #5f6368;
    }
    .day-arrow {
      color: #5f6368;
      font-size: 18px;
    }
    .day-actions {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .btn-day {
      padding: 8px 12px;
      background: transparent;
      border: 1px solid #dadce0;
      border-radius: 4px;
      font-size: 12px;
      color: #5f6368;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.2s;
      white-space: nowrap;
    }
    .btn-day:hover {
      background: #f8f9fa;
    }
    .btn-day-danger {
      color: #d93025;
      border-color: #f28b82;
    }
    .btn-day-danger:hover {
      background: #fce8e6;
    }
    .empty-state {
      text-align: center;
      padding: 80px 20px;
      color: #5f6368;
    }
    .empty-state-icon {
      font-size: 64px;
      margin-bottom: 16px;
      opacity: 0.3;
    }
    .empty-state-text {
      font-size: 16px;
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="header-left">
      <a href="{{ url_for('index') }}" class="btn-back">←</a>
      <h1>{{ username }}</h1>
    </div>
    <a href="{{ url_for('logout') }}" class="btn-logout">Sign out</a>
  </header>

  <div class="main-container">
    <div class="section-header">
      <h2 class="section-title">Timeline</h2>
      {% if days %}
      <p class="section-subtitle">{{ days|length }} day{% if days|length != 1 %}s{% endif %} of photos</p>
      {% endif %}
    </div>

    {% if days %}
      <div class="timeline">
        {% for d in days %}
          <div class="day-row">
            <a href="{{ url_for('view_day', username=username, day=d.day) }}" class="day-card">
              <div class="day-info">
                <div class="day-date">{{ d.day }}</div>
                <div class="day-count">{{ d.files }} photo{% if d.files != 1 %}s{% endif %}</div>
              </div>
              <div class="day-arrow">→</div>
            </a>
            <div class="day-actions">
              <a href="{{ url_for('download_day', username=username, day=d.day) }}" class="btn-day">
                Download
              </a>
              <form method="POST"
                    action="{{ url_for('delete_day', username=username, day=d.day) }}"
                    onsubmit="return confirm('Delete this folder and all its photos from disk?');">
                <button type="submit" class="btn-day btn-day-danger">
                  Delete
                </button>
              </form>
            </div>
          </div>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty-state">
        <div class="empty-state-icon">📅</div>
        <div class="empty-state-text">No photos yet</div>
      </div>
    {% endif %}
  </div>
</body>
</html>
"""
DAY_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ day }} – {{ username }} – {{ site_name }}</title>
  <style>
    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body {
      font-family: 'Segoe UI', 'Google Sans', Roboto, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #ffffff;
      color: #202124;
      min-height: 100vh;
    }
    .header {
      background: #ffffff;
      border-bottom: 1px solid #e8eaed;
      padding: 16px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .btn-back {
      padding: 8px;
      background: transparent;
      border: none;
      color: #5f6368;
      cursor: pointer;
      font-size: 20px;
      text-decoration: none;
      display: flex;
      align-items: center;
      transition: color 0.2s;
    }
    .btn-back:hover {
      color: #202124;
    }
    .header-title {
      display: flex;
      flex-direction: column;
    }
    .header h1 {
      font-size: 22px;
      font-weight: 400;
      color: #202124;
      letter-spacing: -0.5px;
    }
    .header-subtitle {
      font-size: 13px;
      color: #5f6368;
    }
    .btn-logout {
      padding: 8px 24px;
      background: transparent;
      color: #1a73e8;
      border: 1px solid #dadce0;
      border-radius: 4px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      text-decoration: none;
      display: inline-block;
    }
    .btn-logout:hover {
      background: #f8f9fa;
      border-color: #1a73e8;
    }
    .main-container {
      max-width: 1600px;
      margin: 0 auto;
      padding: 24px;
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 24px;
      padding: 12px 0;
      flex-wrap: wrap;
      gap: 8px;
    }
    .view-controls {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .btn-view {
      padding: 8px 12px;
      background: transparent;
      border: 1px solid #dadce0;
      border-radius: 4px;
      color: #5f6368;
      cursor: pointer;
      font-size: 13px;
      transition: all 0.2s;
    }
    .btn-view.active {
      background: #e8f0fe;
      color: #1a73e8;
      border-color: #1a73e8;
    }
    .btn-view:hover:not(.active) {
      background: #f8f9fa;
      border-color: #5f6368;
    }
    .photo-count {
      font-size: 14px;
      color: #5f6368;
    }

    /* GRID VIEWS */
    .photo-grid {
      margin-bottom: 40px;
    }
    .photo-grid.grid-mode {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 4px;
    }
    .photo-grid.grid-mode.comfortable {
      gap: 8px;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    }
    .photo-grid.grid-mode.cozy {
      gap: 16px;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    }

    .photo-item {
      position: relative;
      cursor: pointer;
      overflow: hidden;
      background: #f8f9fa;
      border-radius: 4px;
      display: flex;
      align-items: center;
    }

    /* GRID item style */
    .photo-grid.grid-mode .photo-item {
      flex-direction: column;
      aspect-ratio: 1;
    }

    .photo-thumb-img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform 0.2s;
    }
    .photo-grid.grid-mode .photo-item:hover .photo-thumb-img {
      transform: scale(1.05);
    }
    .photo-grid.grid-mode .photo-item:hover::after {
      content: '';
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.1);
    }

    /* LIST VIEW */
    .photo-grid.list-mode {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .photo-grid.list-mode .photo-item {
      height: 40px;
      padding: 0 12px;
      flex-direction: row;
      justify-content: flex-start;
    }
    /* In LIST mode we do NOT show thumbnails at all */
    .photo-grid.list-mode .photo-thumb-img {
      display: none;
    }
    .photo-filename {
      font-size: 13px;
      color: #202124;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex: 1;
    }

    .photo-delete-form {
      position: absolute;
      top: 6px;
      right: 6px;
      z-index: 5;
    }
    .photo-delete-button {
      border: none;
      border-radius: 50%;
      width: 24px;
      height: 24px;
      font-size: 16px;
      line-height: 1;
      cursor: pointer;
      background: rgba(0, 0, 0, 0.6);
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
    }
    .photo-delete-button:hover {
      background: rgba(0, 0, 0, 0.8);
    }

    .modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.9);
      z-index: 100;
      align-items: center;
      justify-content: center;
    }
    .modal.active {
      display: flex;
    }
    .modal-content {
      position: relative;
      max-width: 95vw;
      max-height: 95vh;
      display: flex;
      flex-direction: column;
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      background: rgba(0, 0, 0, 0.8);
    }
    .modal-filename {
      color: #e8eaed;
      font-size: 14px;
      font-weight: 500;
    }
    .modal-close {
      background: transparent;
      border: none;
      color: #e8eaed;
      font-size: 28px;
      cursor: pointer;
      padding: 0;
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      transition: background 0.2s;
    }
    .modal-close:hover {
      background: rgba(255, 255, 255, 0.1);
    }
    .modal-image-container {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
    }
    .modal-image {
      max-width: 100%;
      max-height: 85vh;
      object-fit: contain;
    }
    .modal-nav {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      background: rgba(0, 0, 0, 0.6);
      border: none;
      color: white;
      font-size: 32px;
      cursor: pointer;
      padding: 20px 16px;
      border-radius: 4px;
      transition: all 0.2s;
      z-index: 101;
    }
    .modal-nav:hover {
      background: rgba(0, 0, 0, 0.8);
    }
    .modal-nav.prev {
      left: 20px;
    }
    .modal-nav.next {
      right: 20px;
    }
    .modal-nav:disabled {
      opacity: 0.3;
      cursor: not-allowed;
    }
    .empty-state {
      text-align: center;
      padding: 80px 20px;
      color: #5f6368;
    }
    .empty-state-icon {
      font-size: 64px;
      margin-bottom: 16px;
      opacity: 0.3;
    }
    .empty-state-text {
      font-size: 16px;
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="header-left">
      <a href="{{ url_for('view_user', username=username) }}" class="btn-back">←</a>
      <div class="header-title">
        <h1>{{ day }}</h1>
        <span class="header-subtitle">{{ username }}</span>
      </div>
    </div>
    <a href="{{ url_for('logout') }}" class="btn-logout">Sign out</a>
  </header>

  <div class="main-container">
    {% if files %}
      <div class="toolbar">
        <div class="photo-count">{{ files|length }} photo{% if files|length != 1 %}s{% endif %}</div>
        <div class="view-controls">
          <button class="btn-view active" data-view="list" onclick="setView('list', this)">List</button>
          <button class="btn-view" data-view="compact" onclick="setView('compact', this)">Compact</button>
          <button class="btn-view" data-view="comfortable" onclick="setView('comfortable', this)">Comfortable</button>
          <button class="btn-view" data-view="cozy" onclick="setView('cozy', this)">Cozy</button>
        </div>
      </div>

      <div class="photo-grid list-mode" id="photoGrid">
        {% for fname in files %}
          <div class="photo-item" data-index="{{ loop.index0 }}" onclick="openModal({{ loop.index0 }})">
            <div class="photo-filename">{{ fname }}</div>
            <form class="photo-delete-form"
                  method="POST"
                  action="{{ url_for('delete_file', username=username, day=day, filename=fname) }}"
                  onsubmit="return confirm('Delete this photo permanently from disk?');">
              <button type="submit"
                      class="photo-delete-button"
                      onclick="event.stopPropagation();">
                ×
              </button>
            </form>
          </div>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty-state">
        <div class="empty-state-icon">📷</div>
        <div class="empty-state-text">No photos for this day</div>
      </div>
    {% endif %}
  </div>

  <div class="modal" id="modal" onclick="closeModal(event)">
    <div class="modal-content" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span class="modal-filename" id="modalFilename"></span>
        <button class="modal-close" onclick="closeModal()">&times;</button>
      </div>
      <div class="modal-image-container">
        <button class="modal-nav prev" id="prevBtn" onclick="navigateModal(-1)">‹</button>
        <img class="modal-image" id="modalImage" src="" alt="">
        <button class="modal-nav next" id="nextBtn" onclick="navigateModal(1)">›</button>
      </div>
    </div>
  </div>

  <script>
    const photos = [
      {% for fname in files %}
      {
        url: "{{ url_for('serve_file', username=username, day=day, filename=fname) }}",
        filename: "{{ fname }}"
      }{% if not loop.last %},{% endif %}
      {% endfor %}
    ];

    let currentIndex = 0;

    function ensureThumbnailsForGrid() {
      const grid = document.getElementById('photoGrid');
      if (!grid.classList.contains('grid-mode')) {
        return;
      }
      const items = document.querySelectorAll('.photo-item');
      items.forEach(item => {
        if (item.querySelector('img')) return;
        const idx = parseInt(item.getAttribute('data-index'), 10);
        const img = document.createElement('img');
        img.className = 'photo-thumb-img';
        img.alt = photos[idx].filename;
        img.loading = 'lazy';
        img.src = photos[idx].url;
        item.insertBefore(img, item.firstChild);
      });
    }

    function setView(view, btn) {
      const grid = document.getElementById('photoGrid');
      const buttons = document.querySelectorAll('.btn-view');

      buttons.forEach(b => b.classList.remove('active'));

      if (btn) {
        btn.classList.add('active');
      } else {
        const match = document.querySelector('.btn-view[data-view="' + view + '"]');
        if (match) {
          match.classList.add('active');
        }
      }

      grid.className = 'photo-grid';
      if (view === 'list') {
        grid.classList.add('list-mode');
      } else {
        grid.classList.add('grid-mode');
        if (view === 'comfortable') grid.classList.add('comfortable');
        if (view === 'cozy') grid.classList.add('cozy');
        // Only now we create thumbnails and load images
        ensureThumbnailsForGrid();
      }

      localStorage.setItem('photoGridView', view);
    }

    function openModal(index) {
      currentIndex = index;
      updateModal();
      document.getElementById('modal').classList.add('active');
      document.body.style.overflow = 'hidden';
    }

    function closeModal(event) {
      if (event && event.target && event.target.classList && event.target.classList.contains('modal-content')) {
        return;
      }
      document.getElementById('modal').classList.remove('active');
      document.body.style.overflow = '';
    }

    function navigateModal(direction) {
      currentIndex += direction;
      if (currentIndex < 0) currentIndex = 0;
      if (currentIndex >= photos.length) currentIndex = photos.length - 1;
      updateModal();
    }

    function updateModal() {
      const photo = photos[currentIndex];
      const img = document.getElementById('modalImage');
      img.src = photo.url;  // image is loaded ONLY here for list mode
      document.getElementById('modalFilename').textContent = photo.filename;

      document.getElementById('prevBtn').disabled = currentIndex === 0;
      document.getElementById('nextBtn').disabled = currentIndex === photos.length - 1;
    }

    document.addEventListener('keydown', function(e) {
      const modal = document.getElementById('modal');
      if (!modal.classList.contains('active')) return;

      if (e.key === 'Escape') {
        closeModal();
      } else if (e.key === 'ArrowLeft') {
        navigateModal(-1);
      } else if (e.key === 'ArrowRight') {
        navigateModal(1);
      }
    });

    // Restore last selected view; default to LIST
    const savedView = localStorage.getItem('photoGridView') || 'list';
    setView(savedView);
  </script>
</body>
</html>
"""


# ==========================================================
#  Web routes
# ==========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if user == WEB_USERNAME and verify_web_password(pw):
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        else:
            error = "Invalid username or password."

    return render_template_string(
        LOGIN_TEMPLATE,
        site_name=SITE_NAME,
        error=error,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    users_data = []
    for d in ROOT_FOLDER.iterdir():
        if d.is_dir():
            username = d.name
            day_dirs = [p for p in d.iterdir() if p.is_dir()]
            file_count = sum(
                1 for dd in day_dirs for f in dd.iterdir() if f.is_file()
            )
            users_data.append(
                {"username": username, "days": len(day_dirs), "files": file_count}
            )

    return render_template_string(
        INDEX_TEMPLATE,
        site_name=SITE_NAME,
        users=sorted(users_data, key=lambda x: x["username"]),
    )


@app.route("/user/<username>")
@login_required
def view_user(username):
    if not validate_identifier(username):
        abort(400)
    user_dir = ROOT_FOLDER / username
    if not user_dir.exists():
        abort(404)

    days_data = []
    for d in user_dir.iterdir():
        if d.is_dir():
            files = [f for f in d.iterdir() if f.is_file()]
            days_data.append({"day": d.name, "files": len(files)})

    days_data.sort(key=lambda x: x["day"])

    return render_template_string(
        USER_TEMPLATE,
        site_name=SITE_NAME,
        username=username,
        days=days_data,
    )


@app.route("/user/<username>/<day>")
@login_required
def view_day(username, day):
    if not (validate_identifier(username) and validate_identifier(day)):
        abort(400)
    day_dir = ROOT_FOLDER / username / day
    if not day_dir.exists():
        abort(404)
    files = sorted([f.name for f in day_dir.iterdir() if f.is_file()])

    return render_template_string(
        DAY_TEMPLATE,
        site_name=SITE_NAME,
        username=username,
        day=day,
        files=files,
    )


@app.route("/files/<username>/<day>/<filename>")
@login_required
def serve_file(username, day, filename):
    if not (validate_identifier(username) and validate_identifier(day)):
        abort(400)
    day_dir = ROOT_FOLDER / username / day
    if not day_dir.exists():
        abort(404)
    return send_from_directory(day_dir, filename)


@app.route("/user/<username>/<day>/<filename>/delete", methods=["POST"])
@login_required
def delete_file(username, day, filename):
    # Validate path parts to avoid traversal
    if not (validate_identifier(username) and validate_identifier(day) and validate_identifier(filename)):
        abort(400)

    file_path = ROOT_FOLDER / username / day / filename
    if not file_path.exists() or not file_path.is_file():
        abort(404)

    file_path.unlink()

    # After deletion, go back to the day view
    return redirect(url_for("view_day", username=username, day=day))


@app.route("/user/<username>/<day>/delete", methods=["POST"])
@login_required
def delete_day(username, day):
    if not (validate_identifier(username) and validate_identifier(day)):
        abort(400)

    day_dir = ROOT_FOLDER / username / day
    if not day_dir.exists() or not day_dir.is_dir():
        abort(404)

    # Remove the whole folder with all images inside
    shutil.rmtree(day_dir)

    return redirect(url_for("view_user", username=username))


@app.route("/download/<username>/<day>")
@login_required
def download_day(username, day):
    if not (validate_identifier(username) and validate_identifier(day)):
        abort(400)

    day_dir = ROOT_FOLDER / username / day
    if not day_dir.exists() or not day_dir.is_dir():
        abort(404)

    mem_file = BytesIO()
    with zipfile.ZipFile(mem_file, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in day_dir.iterdir():
            if f.is_file():
                # Add each file to ZIP with just its filename
                zf.write(f, arcname=f.name)
    mem_file.seek(0)

    zip_name = f"{username}_{day}.zip"
    return send_file(
        mem_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )


# ==========================================================
#  API upload
# ==========================================================

@app.route("/api/upload", methods=["POST"])
def api_upload():
    password = request.headers.get("X-Upload-Password", "")
    if password != UPLOAD_PASSWORD:
        return {"status": "error", "message": "Unauthorized"}, 401

    username = request.form.get("username")
    if not username or not validate_identifier(username):
        return {"status": "error", "message": "invalid username"}, 400

    day = request.form.get("day")
    if not day:
        day = datetime.utcnow().strftime("%d-%m-%Y")
    if not validate_identifier(day):
        return {"status": "error", "message": "invalid day"}, 400

    file = request.files.get("file")
    if not file:
        return {"status": "error", "message": "file is required"}, 400

    user_dir = ROOT_FOLDER / username
    day_dir = user_dir / day
    day_dir.mkdir(parents=True, exist_ok=True)

    filename = secure_filename(file.filename)
    dest = day_dir / filename
    file.save(dest)

    return {"status": "ok", "path": str(dest)}, 200


# ==========================================================
#  Main entry point
# ==========================================================

if __name__ == "__main__":
    if "--set-admin-password" in sys.argv:
        set_admin_password_interactive()
    else:
        # Dev server only. For real use, prefer gunicorn:
        #   gunicorn --bind 127.0.0.1:5000 --workers 3 --threads 4 tor_server:app
        app.run(host="192.168.0.24", port=5000, debug=False)
