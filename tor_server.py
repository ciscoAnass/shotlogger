import json
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import (
    Flask, request, redirect, url_for,
    render_template_string, send_from_directory,
    session, abort
)
from werkzeug.utils import secure_filename

# ---- Load config ----

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "server_config.json"

if not CONFIG_FILE.exists():
    raise SystemExit(f"Config file not found: {CONFIG_FILE}")

with CONFIG_FILE.open("r", encoding="utf-8") as f:
    cfg = json.load(f)

ROOT_FOLDER = Path(cfg.get("root_folder", "/home/youruser/shotlogger_data"))
WEB_USERNAME = cfg.get("web_username", "admin")
WEB_PASSWORD = cfg.get("web_password", "changeme")
UPLOAD_PASSWORD = cfg.get("upload_password", "change_me")
SITE_NAME = cfg.get("site_name", "ShotLogger")
SESSION_SECRET = cfg.get("session_secret")

if not SESSION_SECRET:
    # Fallback: derive from upload password
    SESSION_SECRET = UPLOAD_PASSWORD + "_flask_secret"

ROOT_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = SESSION_SECRET.encode("utf-8")


# ---- Security headers ----

@app.after_request
def set_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline';"
    return response


# ---- auth helper ----

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ---- small validation (avoid ../ etc.) ----

def validate_identifier(value: str) -> bool:
    # Very simple: avoid path separators
    if not value:
        return False
    return ("/" not in value) and ("\\" not in value)



LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Login – {{ site_name }}</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      color: #111827;
    }
    header {
      background: #2563eb;
      color: white;
      padding: 16px 24px;
    }
    header h1 { margin: 0; font-size: 20px; }
    main { padding: 24px; }
    .box {
      max-width: 360px;
      margin: 60px auto;
      background: white;
      padding: 24px;
      border-radius: 12px;
      border: 1px solid #e5e7eb;
      box-shadow: 0 10px 20px rgba(0,0,0,0.05);
    }
    .box h2 { margin-top: 0; }
    label {
      display: block;
      margin: 8px 0 4px;
      font-size: 14px;
    }
    input[type="text"], input[type="password"] {
      width: 100%;
      padding: 8px;
      border-radius: 6px;
      border: 1px solid #d1d5db;
      font-size: 14px;
    }
    button {
      margin-top: 12px;
      padding: 8px 14px;
      border-radius: 8px;
      border: none;
      background: #2563eb;
      color: white;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: #1d4ed8; }
    .error {
      color: #b91c1c;
      font-size: 14px;
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <header><h1>{{ site_name }}</h1></header>
  <main>
    <div class="box">
      <h2>Login</h2>
      {% if error %}
        <p class="error">{{ error }}</p>
      {% endif %}
      <form method="post">
        <label>Username</label>
        <input type="text" name="username" autofocus>
        <label>Password</label>
        <input type="password" name="password">
        <button type="submit">Sign in</button>
      </form>
    </div>
  </main>
</body>
</html>
"""

INDEX_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Users – {{ site_name }}</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      color: #111827;
    }
    header {
      background: #2563eb;
      color: white;
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    header h1 { margin: 0; font-size: 20px; }
    header a {
      color: white;
      text-decoration: none;
      font-size: 14px;
      opacity: 0.9;
    }
    header a:hover { opacity: 1; }
    main {
      padding: 24px;
      max-width: 900px;
      margin: 0 auto;
    }
    .card {
      background: white;
      border-radius: 12px;
      padding: 16px 20px;
      border: 1px solid #e5e7eb;
      box-shadow: 0 10px 20px rgba(0,0,0,0.04);
    }
    .card h2 { margin-top: 0; }
    ul {
      list-style: none;
      padding-left: 0;
      margin: 0;
    }
    li + li { margin-top: 6px; }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: #eff6ff;
      color: #1d4ed8;
      margin-right: 8px;
    }
    a.link {
      color: #2563eb;
      text-decoration: none;
      font-weight: 500;
    }
    a.link:hover { text-decoration: underline; }
    .meta {
      font-size: 13px;
      color: #6b7280;
      margin-top: 4px;
    }
  </style>
</head>
<body>
  <header>
    <h1>{{ site_name }}</h1>
    <a href="{{ url_for('logout') }}">Logout</a>
  </header>
  <main>
    <div class="card">
      <h2>Users</h2>
      <ul>
      {% if users %}
        {% for u in users %}
          <li>
            <span class="pill">user</span>
            <a class="link" href="{{ url_for('view_user', username=u.username) }}">{{ u.username }}</a>
            <div class="meta">
              {{ u.days }} day(s), {{ u.files }} screenshot(s)
            </div>
          </li>
        {% endfor %}
      {% else %}
        <li>No users yet.</li>
      {% endif %}
      </ul>
    </div>
  </main>
</body>
</html>
"""

USER_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ username }} – {{ site_name }}</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      color: #111827;
    }
    header {
      background: #2563eb;
      color: white;
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    header h1 { margin: 0; font-size: 20px; }
    header a {
      color: white;
      text-decoration: none;
      font-size: 14px;
      opacity: 0.9;
    }
    header a:hover { opacity: 1; }
    main {
      padding: 24px;
      max-width: 900px;
      margin: 0 auto;
    }
    .card {
      background: white;
      border-radius: 12px;
      padding: 16px 20px;
      border: 1px solid #e5e7eb;
      box-shadow: 0 10px 20px rgba(0,0,0,0.04);
    }
    .card h2 { margin-top: 0; }
    ul {
      list-style: none;
      padding-left: 0;
      margin: 0;
    }
    li + li { margin-top: 6px; }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: #fef9c3;
      color: #854d0e;
      margin-right: 8px;
    }
    a.link {
      color: #2563eb;
      text-decoration: none;
      font-weight: 500;
    }
    a.link:hover { text-decoration: underline; }
    .back { margin-top: 12px; font-size: 14px; }
  </style>
</head>
<body>
  <header>
    <h1>{{ site_name }}</h1>
    <a href="{{ url_for('logout') }}">Logout</a>
  </header>
  <main>
    <div class="card">
      <h2>User: {{ username }}</h2>
      <ul>
      {% if days %}
        {% for d in days %}
          <li>
            <span class="pill">day</span>
            <a class="link" href="{{ url_for('view_day', username=username, day=d.day) }}">{{ d.day }}</a>
            <span class="meta">({{ d.files }} screenshot(s))</span>
          </li>
        {% endfor %}
      {% else %}
        <li>No days yet.</li>
      {% endif %}
      </ul>
      <p class="back">
        <a class="link" href="{{ url_for('index') }}">← Back to users</a>
      </p>
    </div>
  </main>
</body>
</html>
"""

DAY_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ username }} – {{ day }} – {{ site_name }}</title>
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f9fafb;
      color: #111827;
    }
    header {
      background: #2563eb;
      color: white;
      padding: 16px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    header h1 { margin: 0; font-size: 20px; }
    header a {
      color: white;
      text-decoration: none;
      font-size: 14px;
      opacity: 0.9;
    }
    header a:hover { opacity: 1; }
    main {
      padding: 24px;
      max-width: 1100px;
      margin: 0 auto;
    }
    .card {
      background: white;
      border-radius: 12px;
      padding: 16px 20px;
      border: 1px solid #e5e7eb;
      box-shadow: 0 10px 20px rgba(0,0,0,0.04);
    }
    .card h2 { margin-top: 0; }
    .thumb-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }
    .thumb-card {
      background: #f9fafb;
      border-radius: 8px;
      border: 1px solid #e5e7eb;
      padding: 8px;
      max-width: 260px;
      cursor: pointer;
    }
    .thumb-card img {
      max-width: 100%;
      border-radius: 4px;
      display: block;
    }
    .thumb-card p {
      margin: 4px 0 0 0;
      font-size: 12px;
      color: #4b5563;
      word-break: break-all;
    }
    a.link {
      color: #2563eb;
      text-decoration: none;
      font-weight: 500;
    }
    a.link:hover { text-decoration: underline; }
    .back { margin-top: 12px; font-size: 14px; }

    /* Modal preview */
    .modal-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.6);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 50;
    }
    .modal-content {
      background: #111827;
      padding: 12px;
      border-radius: 8px;
      max-width: 90vw;
      max-height: 90vh;
    }
    .modal-content img {
      max-width: 100%;
      max-height: 85vh;
      display: block;
      border-radius: 4px;
    }
    .modal-caption {
      margin-top: 6px;
      font-size: 12px;
      color: #e5e7eb;
      word-break: break-all;
    }
  </style>
  <script>
    function openPreview(src, caption) {
      const backdrop = document.getElementById('modal-backdrop');
      const img = document.getElementById('modal-img');
      const cap = document.getElementById('modal-caption');
      img.src = src;
      cap.textContent = caption;
      backdrop.style.display = 'flex';
    }
    function closePreview() {
      const backdrop = document.getElementById('modal-backdrop');
      backdrop.style.display = 'none';
    }
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        closePreview();
      }
    });
  </script>
</head>
<body>
  <header>
    <h1>{{ site_name }}</h1>
    <a href="{{ url_for('logout') }}">Logout</a>
  </header>
  <main>
    <div class="card">
      <h2>{{ username }} – {{ day }}</h2>
      <p class="back">
        <a class="link" href="{{ url_for('view_user', username=username) }}">← Back to days</a>
      </p>
      {% if files %}
      <div class="thumb-grid">
        {% for fname in files %}
          <div class="thumb-card" onclick="openPreview('{{ url_for('serve_file', username=username, day=day, filename=fname) }}', '{{ fname }}')">
            <img src="{{ url_for('serve_file', username=username, day=day, filename=fname) }}">
            <p>{{ fname }}</p>
          </div>
        {% endfor %}
      </div>
      {% else %}
        <p>No screenshots for this day.</p>
      {% endif %}
    </div>
  </main>

  <!-- Modal -->
  <div class="modal-backdrop" id="modal-backdrop" onclick="closePreview()">
    <div class="modal-content" onclick="event.stopPropagation()">
      <img id="modal-img" src="">
      <div class="modal-caption" id="modal-caption"></div>
    </div>
  </div>
</body>
</html>
"""


# ---- Web routes ----

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if user == WEB_USERNAME and pw == WEB_PASSWORD:
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


# ---- API upload ----

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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
