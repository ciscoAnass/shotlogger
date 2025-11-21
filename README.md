# ShotLogger – Self-Hosted Tor Screen Logger (Personal Use Only)

ShotLogger is a **personal** tool that:

- Captures screenshots on your **own Windows accounts** at a fixed interval.
- Sends them over **Tor** to your **own Debian server** running a private `.onion` site.
- Lets you browse screenshots per **Windows username** and **date** via a simple web UI in Tor Browser.

> ⚠️ This is for **your own machines and accounts only**.  
> Do **not** use it to spy on other people or devices you do not own or control.  
> The design is transparent and avoids stealthy / malware-style behavior as much as possible.


 **Note:** Tools compiled with PyInstaller often trigger **generic flags** such as  
_"Win64:Malware-gen"_, _"Suspicious PE"_, or _"BehavesLike.Win64.Generic"_.  

These are **false positives** caused by:

- The executable being packed into a single file
    
- Screenshots being taken programmatically
    
- Network communication through Tor

<img width="1087" height="587" alt="torcap" src="https://github.com/user-attachments/assets/2911b3e6-1e14-4eae-8ef7-1f8b15dbcb6e" />

---

## 1. Architecture Overview

There are two components:

1. **Server (Debian 13)** – `tor_server.py`
   - Runs a Flask web app on `127.0.0.1:5000`.
   - Exposed via Tor as a **hidden service** (e.g. `xyz123.onion`).
   - Stores screenshots on disk under a root folder, like:
     ```text
     /home/youruser/shotlogger_data/
       ├─ CiscoAnass/
       │   ├─ 21-11-2025/
       │   │   ├─ screenshot_20251121_000918.png
       │   │   └─ ...
       └─ AnotherUser/
           └─ 22-11-2025/
     ```
   - Web UI (via Tor Browser):
     - Login with your admin credentials.
     - See **users → days → thumbnails**, and click thumbnails to preview full images.

2. **Client (Windows)** – `app.py` → `ShotLogger.exe`
   - Runs in the background when the user logs in (via Task Scheduler).
   - Takes screenshots every _N_ seconds (default `10`).
   - Stores them temporarily in a local folder (e.g. `C:\Users\CiscoAnass\Pictures\Security\DD-MM-YYYY`).
   - Periodically batches uploads to the Tor server over a SOCKS proxy (`Tor`).
   - After a successful upload, it **deletes** the local screenshot.
   - Enforces a max local folder size (MB) and deletes oldest non-pending files when above the limit.

Everything is configured through **JSON files**, no environment variables needed.

---

## 2. Requirements

### 2.1 Server (Debian 13)

- Debian 13 (or similar Linux)
- `python3`, `python3-venv`, `python3-pip`
- `tor`
- Python packages (inside a venv):
  - `flask`
  - `gunicorn` (recommended for production)

### 2.2 Windows Client

For **building** the EXE:

- Windows 10/11 64-bit
- Python 3.10+ (you used 3.14)
- `pip`
- Python packages (inside a venv):
  - `mss`
  - `requests`
  - `pysocks`
  - `pyinstaller` (for building the EXE)

For **running** the EXE on each Windows PC:

- Only the **built EXE** (e.g. `ShotLogger.exe`)
- A matching `config.json` in the same folder
- Tor connectivity (typically Tor Browser or Tor service providing a SOCKS proxy on `127.0.0.1:9050` or `127.0.0.1:9150`)

---

## 3. Server Setup (Debian 13)

### 3.1 Create project folder and virtualenv

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip tor

mkdir -p ~/shotlogger
cd ~/shotlogger

python3 -m venv venv
source venv/bin/activate

pip install flask gunicorn
```

Copy these files into `~/shotlogger`:

- `tor_server.py`
- `server_config.json` (you will create it in the next step)

### 3.2 Create `server_config.json`

In `~/shotlogger/server_config.json`:

```json
{
  "root_folder": "/home/youruser/shotlogger_data",
  "web_username": "anass",
  "web_password": "",
  "web_password_hash": "",
  "upload_password": "CHANGE_THIS_UPLOAD_PASSWORD",
  "site_name": "ShotLogger",
  "session_secret": "CHANGE_THIS_SESSION_SECRET"
}
```

- `root_folder` – where screenshots are stored on the server.
- `web_username` – username for the **web UI** (Tor Browser).
- `web_password` – leave as `""` (we will replace it with a hashed password).
- `web_password_hash` – will be filled automatically by `tor_server.py --set-admin-password` (see below).
- `upload_password` – **shared secret** between server and Windows clients.  
  - This must match `upload_password` in each Windows `config.json`.
  - This is used only for the **upload API**, not for the web UI.
- `site_name` – title displayed on the site.
- `session_secret` – long random string used to secure Flask sessions.

> ⚠️ Keep this file private. It never leaves your Debian server.  
>   Set file permissions to restrict access (see section 8).

### 3.3 Set the admin web password (hashed)

The admin password for the web UI is **never stored in plain text**.  
Instead, `tor_server.py` can hash it and store only the hash (PBKDF2-SHA256).

From `~/shotlogger`:

```bash
cd ~/shotlogger
source venv/bin/activate
python tor_server.py --set-admin-password
```

You will see prompts:

```text
This will set (or reset) the ADMIN web password for the ShotLogger UI.
New web password: ********
Repeat web password: ********
Admin web password updated successfully.
```

After this, `server_config.json` will have something like:

```json
  "web_username": "anass",
  "web_password": "",
  "web_password_hash": "pbkdf2_sha256$200000$<salt_hex>$<hash_hex>",
```

From now on:

- `/login` compares your typed password with the stored **hash**.
- Even if an attacker steals `server_config.json`, they do not see your raw admin password.

### 3.4 Configure Tor Hidden Service

Edit Tor config:

```bash
sudo nano /etc/tor/torrc
```

Add this at the end (if not already present):

```text
HiddenServiceDir /var/lib/tor/shotlogger_service/
HiddenServicePort 80 127.0.0.1:5000
```

Save and restart Tor:

```bash
sudo systemctl restart tor
```

Get your `.onion` address:

```bash
sudo cat /var/lib/tor/shotlogger_service/hostname
```

Example output:

```text
nlbsg4kl2itgwcu5g6ysikwmx77lavxb5kh2c36mg7wlmazn2332hhid.onion
```

This is the URL you will use in:

- Tor Browser (to access the web UI)
- Windows `config.json` as `server_url`.

### 3.5 Run the Flask server

#### Option A – development / quick testing

```bash
cd ~/shotlogger
source venv/bin/activate
python tor_server.py
```

- Runs Flask’s built-in dev server.
- Good for quick tests, **not recommended for long-term production**.

#### Option B – production (recommended) with gunicorn

```bash
cd ~/shotlogger
source venv/bin/activate
gunicorn --bind 127.0.0.1:5000 --workers 3 --threads 4 tor_server:app
```

- `tor_server:app` = module name `tor_server` and Flask object `app` inside it.
- Multiple workers + threads handle concurrent requests better than `app.run()`.

Tor still points to `127.0.0.1:5000` via:

```text
HiddenServicePort 80 127.0.0.1:5000
```

### 3.6 Test in Tor Browser

Open Tor Browser and go to:

```text
http://YOUR_ONION_HOSTNAME.onion/
```

Log in using `web_username` and the password you set with `--set-admin-password`.

You should see:

- A **Users** list (e.g. `CiscoAnass`, `AnassWork`, etc.)
- Clicking a user shows available **days**.
- Clicking a day shows all screenshots as thumbnails.
- Clicking a thumbnail opens a **full preview modal**.

---

## 4. Windows Client Setup (builder machine)

You only need to do this on the machine where you **build** `ShotLogger.exe`.

### 4.1 Create project folder & virtualenv

On Windows (PowerShell):

```powershell
cd C:\Users\YourUser\Desktop
mkdir shotlogger
cd .\shotlogger

python -m venv venv
.\venv\Scripts\activate

pip install mss requests pysocks pyinstaller
```

Copy into this folder:

- `app.py` (ShotLogger client)
- `config.json` (client config template)

### 4.2 Create `config.json` (client)

Example `config.json`:

```json
{
  "interval_seconds": 10,
  "screenshot_folder": "C:/Users/CiscoAnass/Pictures/Security",
  "server_url": "http://nlbsg4kl2itgwcu5g6ysikwmx77lavxb5kh2c36mg7wlmazn2332hhid.onion",
  "upload_password": "CHANGE_THIS_UPLOAD_PASSWORD",
  "upload_batch_size": 10,
  "max_folder_size_mb": 500,
  "tor_socks_proxy": "socks5h://127.0.0.1:9050",
  "log_file": "screen_guard.log"
}
```

- `interval_seconds` – capture interval in seconds.
- `screenshot_folder` – base folder; client will create subfolders per day (`DD-MM-YYYY`).
- `server_url` – your `.onion` address from `hostname` (use `http://`, not https).
- `upload_password` – must match `upload_password` from `server_config.json` (used for the upload API).
- `upload_batch_size` – how many screenshots to upload at once.
- `max_folder_size_mb` – maximum local disk usage before rotation starts deleting oldest non-pending files.
- `tor_socks_proxy`:
  - If Tor is running as a service: usually `socks5h://127.0.0.1:9050`
  - If using Tor Browser only: often `socks5h://127.0.0.1:9150`
- `log_file` – log file name (stored next to the EXE by default).

You can test with:

```powershell
.\venv\Scripts\activate
python .\app.py
```

Check `screen_guard.log` and your server’s `root_folder` to confirm uploads are working.

### 4.3 Build the EXE

Use PyInstaller to build a single-file EXE with a clean, honest name (e.g. `ShotLogger.exe`):

```powershell
.\venv\Scripts\activate

pyinstaller --onefile --noconsole `
  --icon="C:\Users\YourUser\Desktop\shotlogger\shotlogger.ico" `
  --name="ShotLogger" `
  "C:\Users\YourUser\Desktop\shotlogger\app.py"
```

After it finishes, you will get:

```text
C:\Users\YourUser\Desktop\shotlogger\dist\ShotLogger.exe
```

This file is what you deploy to your Windows PCs.

---

## 5. Deploying to Each Windows PC

You do **not** need Python on every PC, only the EXE + config.

### 5.1 Folder layout on the target PC

On each Windows machine, choose a folder like:

```text
C:\Program Files\ShotLogger\
```

Copy into that folder:

- `ShotLogger.exe`
- `config.json`

You can adjust `config.json` per machine if needed (e.g. different `screenshot_folder`).

### 5.2 Create a Scheduled Task (run at user login)

1. Open **Task Scheduler**.
2. Click **Create Task…** (not “Basic Task” for more options if you prefer).
3. **General** tab:
   - Name: `ShotLogger`
   - “Run only when user is logged on” (recommended for a transparent tool).
4. **Triggers** tab → **New…**:
   - Begin the task: “At log on”
   - Settings: “Any user” or a specific user (your choice).
5. **Actions** tab → **New…**:
   - Action: “Start a program”
   - Program/script: `C:\Program Files\ShotLogger\ShotLogger.exe`
   - Start in (optional but recommended): `C:\Program Files\ShotLogger\`
6. Click **OK** to save.

Now, each time that user logs in, `ShotLogger.exe` will start, capture screenshots, and upload them to your Tor server.

---

## 6. How the Client Behaves

- On startup:
  - Loads `config.json` from the same directory as the EXE.
  - Ensures the `screenshot_folder` exists.
  - Scans that folder for existing `*.png` files and treats them as **pending uploads** (useful when internet / Tor was down).
- Every `interval_seconds`:
  - Creates a folder for today: `DD-MM-YYYY` inside the screenshot folder.
  - Captures a screenshot into `screenshot_YYYYMMDD_HHMMSS.png` inside that day folder.
  - Adds the new file to the **pending** list.
  - Runs **rotation** if total size > `max_folder_size_mb`, deleting oldest files that are not currently pending.
- Upload logic:
  - When `pending` length ≥ `upload_batch_size`, it tries to upload them to `server_url + "/api/upload"`
    via the Tor SOCKS proxy (`tor_socks_proxy`).
  - Sends:
    - `username` – the Windows username (`getpass.getuser()`).
    - `day` – derived from filename or file’s mtime (`DD-MM-YYYY`).
    - `file` – the PNG screenshot.
    - Header `X-Upload-Password: <upload_password>`.
  - On HTTP 200 → deletes the local file.
  - On error → keeps the file for the next attempt.

---

## 7. How the Server Behaves

- Reads `server_config.json` at startup.
- Stores screenshots under:

  ```text
  root_folder / <username> / <day> / <filename>.png
  ```

- Web UI structure:
  - `/login` → login form (Tor only). Password is checked against a **PBKDF2-SHA256 hash** stored in `web_password_hash`.
  - `/` → list of users
  - `/user/<username>` → list of days & file counts
  - `/user/<username>/<day>` → grid of thumbnails
  - Clicking a thumbnail opens a **full preview modal**.
- API endpoint for uploads:
  - `POST /api/upload`
  - Requires header `X-Upload-Password` equal to `upload_password` in `server_config.json`.
  - Form fields:
    - `username`
    - `day` (optional; if empty, server uses current UTC date)
    - `file` → screenshot
  - Rejects bad credentials or invalid paths with error JSON.

---

## 8. Security & Ethics

### 8.1 Password handling

- **Admin web password (`/login`)**:
  - Stored as a **PBKDF2-SHA256 hash** in `server_config.json` (`web_password_hash`).
  - Set via `python tor_server.py --set-admin-password`.
  - Never stored in plain text after that.

- **Upload password (`upload_password`)**:
  - Shared between server and client.
  - Needed so the client can authenticate to `/api/upload`.
  - If an attacker steals `config.json` on a Windows machine, they can impersonate that client.  
    This is unavoidable because the client must know the secret to use it.
  - Keep `config.json` on Windows machines as private as possible (only your account, protected device).

### 8.2 File permissions on the server

Lock down sensitive files on Debian:

```bash
sudo chown youruser:youruser /home/youruser/shotlogger/server_config.json
chmod 600 /home/youruser/shotlogger/server_config.json
```

Same for the data folder if you want to restrict which local users can view raw PNGs:

```bash
sudo chown -R youruser:youruser /home/youruser/shotlogger_data
chmod -R 700 /home/youruser/shotlogger_data
```

### 8.3 Ethics

- This tool is intentionally designed for **personal use on your own machines**:
  - You own the Debian server.
  - You own the Windows PCs / user accounts.
- Do **not** deploy it on machines you don’t own, or users you don’t have a clear agreement with.
- The client:
  - Uses a clear folder (`ShotLogger`, `ShotLogger.exe`) if you choose so.
  - Uses standard, legitimate persistence (Windows Task Scheduler).
  - Avoids stealth techniques like hiding processes, rootkits, registry abuse, etc.

### 8.4 Antivirus

- Some AV engines may flag any app that:
  - Captures screens,
  - Auto-starts with Windows,
  - Uses Tor.
- Since this is for your own lab, the honest and safe approach is:
  - Use a clear, honest program name (`ShotLogger.exe`).
  - Keep the code readable and documented.
  - If needed, add an **exception** in your own AV for this file/folder (because you trust your own code).

---

## 9. Troubleshooting

### 9.1 No users appear in the web UI

- Check that screenshots are actually being created on Windows:
  - Look inside `screenshot_folder` (and sub-folders by date).
- Check that uploads succeed:
  - Look at the Windows log file (`screen_guard.log` next to the EXE).
  - Look under `root_folder` on Debian:
    ```bash
    ls -R /home/youruser/shotlogger_data
    ```
- If you only see folders on disk but not in UI, ensure:
  - `root_folder` in `server_config.json` is exactly the parent directory containing the username folders.

### 9.2 401 Unauthorized in logs

- Means `upload_password` in Windows `config.json` does **not** match `upload_password` in `server_config.json`.
- Fix both to use the same secret string.

### 9.3 Tor / network issues

- On Debian:
  - Check Tor status: `sudo systemctl status tor`
- On Windows:
  - Ensure Tor Browser or Tor service is running.
  - Verify the `tor_socks_proxy` in `config.json`:
    - `socks5h://127.0.0.1:9050` (Tor service)
    - `socks5h://127.0.0.1:9150` (Tor Browser)
- Check Windows logs for errors like “Network error while uploading …”.

### 9.4 Disk usage growing too much on Windows

- Lower `max_folder_size_mb` in `config.json`.
- Increase `upload_batch_size` so uploads happen more frequently.
- Make sure the server is reachable so uploads can succeed and local files are removed.

---

## 10. Notes

- All code and config here is intentionally **simple and transparent**.
- You are responsible for how you use this tool; keep it ethical and legal.
- If you expand it (tray icon, GUI, encryption, multi-user web auth, etc.), keep the same principles:
  - No stealth.
  - Clear configuration.
  - Respect for privacy and consent.

