import os
import sys
import time
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
import getpass
from typing import List, Optional, Set, Dict

import mss
from mega import Mega


CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "interval_seconds": 10,
    "screenshot_folder": "",
    "enable_mega": False,
    "mega_email": "",
    "mega_password": "",
    "upload_batch_size": 10,
    "max_folder_size_mb": 500,
    "log_file": "screen_guard.log"
}


def setup_logging(log_file: str) -> None:
    """
    Configure logging to both console and a rotating log file.
    This keeps the program transparent and easier to debug.
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    fh = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def load_or_create_config() -> dict:
    """
    Load config from CONFIG_FILE.
    If it doesn't exist, create it with DEFAULT_CONFIG, explain to the user,
    and exit cleanly so they can edit it.
    """
    config_path = Path(CONFIG_FILE)

    if not config_path.exists():
        default_folder = Path.home() / "Pictures" / "CapturasSeguridad"
        DEFAULT_CONFIG["screenshot_folder"] = str(default_folder)

        with config_path.open("w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)

        print(
            f"[INFO] {CONFIG_FILE} has been created with default settings.\n"
            f"Please open it, review, and customize values (especially MEGA options if you want to use them),\n"
            f"then run the program again."
        )
        sys.exit(0)

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)

    return config


def ensure_folder(path_obj) -> Path:
    """
    Ensure the folder exists. Accepts either a string or a Path.
    """
    folder = Path(path_obj)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_folder_size_mb(folder: Path) -> float:
    """
    Calculate the total size of ALL files under 'folder' (recursive) in megabytes.
    This includes all day subfolders.
    """
    total_bytes = 0
    for item in folder.rglob("*"):
        if item.is_file():
            total_bytes += item.stat().st_size
    return total_bytes / (1024 * 1024)


def rotate_screenshots(folder: Path, max_size_mb: float, protected: Optional[Set[Path]] = None) -> None:
    """
    If the folder's total size exceeds max_size_mb, delete the oldest files
    (across all subfolders) until we are under the limit.

    'protected' is a set of Paths that must NOT be deleted (e.g. screenshots
    that have not yet been uploaded to MEGA).
    """
    if max_size_mb <= 0:
        return

    current_size = get_folder_size_mb(folder)
    if current_size <= max_size_mb:
        return

    if protected is None:
        protected = set()

    logging.info(
        "Folder size %.2f MB exceeds limit %.2f MB. Starting rotation...",
        current_size, max_size_mb
    )

    # All files in this tree, oldest first
    files = [f for f in folder.rglob("*") if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime)

    for file_path in files:
        if file_path in protected:
            continue

        try:
            logging.info("Deleting old screenshot (rotation): %s", file_path)
            file_path.unlink()
        except Exception as e:
            logging.error("Error deleting file %s: %s", file_path, e)

        current_size = get_folder_size_mb(folder)
        if current_size <= max_size_mb:
            logging.info("Rotation complete. Current folder size: %.2f MB", current_size)
            break


def take_screenshot(output_path: Path) -> None:
    """
    Capture the primary monitor and save it to output_path as PNG.
    Uses 'mss' which is efficient and works well on Windows.
    """
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            sct.grab(monitor)
            sct.shot(mon=1, output=str(output_path))
        logging.info("Screenshot saved: %s", output_path)
    except Exception as e:
        logging.error("Error taking screenshot: %s", e)


def init_mega_client(config: dict):
    """
    Initialize MEGA client if enabled in config.

    Returns a logged-in MEGA client or None on failure/disabled.
    """
    enable_mega = bool(config.get("enable_mega", False))

    if not enable_mega:
        logging.info("MEGA upload is disabled in config.")
        return None

    email = config.get("mega_email", "").strip()
    password = config.get("mega_password", "").strip()

    if not email or not password:
        logging.warning(
            "enable_mega is True, but mega_email or mega_password is missing. "
            "Uploads to MEGA will be skipped until you update config.json."
        )
        return None

    mega = Mega()
    try:
        logging.info("Logging into MEGA as %s ...", email)
        m = mega.login(email, password)
        logging.info("Logged into MEGA successfully.")
        return m
    except Exception as e:
        logging.error("Failed to login to MEGA: %s", e)
        return None


def get_day_folder_name_for_path(path: Path) -> str:
    """
    Given a screenshot path like '.../screenshot_20251121_220005.png',
    return a day folder name like '21-11-2025'.

    If filename parsing fails, fall back to file modification date.
    """
    name = path.stem  # 'screenshot_20251121_220005'
    parts = name.split("_")
    if len(parts) >= 2:
        date_str = parts[1]  # '20251121'
        try:
            dt = datetime.strptime(date_str, "%Y%m%d")
            return dt.strftime("%d-%m-%Y")
        except ValueError:
            pass

    dt = datetime.fromtimestamp(path.stat().st_mtime)
    return dt.strftime("%d-%m-%Y")


def ensure_mega_day_folder(
    mega_client,
    username: str,
    day_folder_name: str,
    cache: Dict[str, str]
) -> Optional[str]:
    """
    Ensure that the MEGA folder 'username/day_folder_name' exists.

    Uses mega.py create_folder with nested path (mkdir -p style).
    Caches the day folder node_id in 'cache' to avoid repeated API calls.

    Returns:
        node_id (str) of the day folder on success, or None on failure.
    """
    if day_folder_name in cache:
        return cache[day_folder_name]

    remote_path = f"{username}/{day_folder_name}"
    try:
        logging.info("Ensuring MEGA folder '%s' exists.", remote_path)
        result = mega_client.create_folder(remote_path)
        node_id = result.get(day_folder_name)
        if not node_id:
            logging.error(
                "MEGA create_folder did not return a node id for '%s'.",
                remote_path
            )
            return None

        cache[day_folder_name] = node_id
        logging.info(
            "Using MEGA folder '%s/%s' (node_id=%s) for uploads.",
            username, day_folder_name, node_id
        )
        return node_id

    except Exception as e:
        logging.error(
            "Error creating/locating MEGA folder '%s/%s': %s",
            username, day_folder_name, e
        )
        return None


def upload_batch_to_mega(
    mega_client,
    mega_username: str,
    mega_day_cache: Dict[str, str],
    pending_paths: List[Path]
) -> List[Path]:
    """
    Upload a batch of screenshots to MEGA.

    For each file:
      - Determine its day folder (e.g. '21-11-2025')
      - Ensure MEGA folder 'mega_username/day_folder' exists
      - Upload file there
      - If upload succeeds, delete the local copy and mark it as uploaded

    Returns:
        List[Path] that were successfully uploaded (and deleted locally).
    """
    if not pending_paths or mega_client is None:
        return []

    successfully_uploaded: List[Path] = []

    for path in pending_paths:
        try:
            if not path.exists():
                logging.warning("File %s no longer exists locally, skipping.", path)
                successfully_uploaded.append(path)
                continue

            day_folder_name = get_day_folder_name_for_path(path)
            day_node_id = ensure_mega_day_folder(
                mega_client,
                mega_username,
                day_folder_name,
                mega_day_cache
            )

            if not day_node_id:
                logging.error(
                    "Skipping upload for %s because MEGA day folder could not be ensured.",
                    path
                )
                continue

            logging.info(
                "Uploading %s to MEGA folder %s/%s...",
                path,
                mega_username,
                day_folder_name
            )
            mega_client.upload(str(path), day_node_id)
            logging.info("Uploaded %s to MEGA, deleting local copy.", path)

            path.unlink()
            successfully_uploaded.append(path)

        except Exception as e:
            logging.error("Error uploading %s to MEGA: %s", path, e)

    return successfully_uploaded


def main():
    config = load_or_create_config()

    setup_logging(config.get("log_file", "screen_guard.log"))
    logging.info(
        "ScreenGuard started. This program only logs YOUR OWN screen on YOUR machine."
    )

    interval_seconds = int(config.get("interval_seconds", 10))
    screenshot_folder = config.get("screenshot_folder")
    if not screenshot_folder:
        screenshot_folder = str(Path.home() / "Pictures" / "CapturasSeguridad")

    folder_path = ensure_folder(screenshot_folder)
    max_size_mb = float(config.get("max_folder_size_mb", 500))
    upload_batch_size = int(config.get("upload_batch_size", 10))
    mega_enabled = bool(config.get("enable_mega", False))

    # Windows username
    username = getpass.getuser()
    logging.info("Detected Windows username: %s", username)

    mega_client = None
    mega_day_cache: Dict[str, str] = {}

    if mega_enabled:
        mega_client = init_mega_client(config)
        if mega_client:
            logging.info("MEGA uploads are ENABLED.")
        else:
            logging.info(
                "MEGA uploads are ENABLED in config, but initial login failed. "
                "Will keep retrying while the program runs."
            )
    else:
        logging.info("MEGA uploads are DISABLED or not available, local-only mode.")

    logging.info("Using screenshot folder: %s", folder_path)
    logging.info("Interval: %d seconds", interval_seconds)
    logging.info("Batch size for MEGA uploads: %d screenshots", upload_batch_size)
    logging.info("Local folder size limit: %.2f MB", max_size_mb)

    pending_screenshots: List[Path] = []

    try:
        while True:
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            day_folder_name = now.strftime("%d-%m-%Y")

            day_folder_path = ensure_folder(folder_path / day_folder_name)
            filename = f"screenshot_{timestamp}.png"
            screenshot_path = day_folder_path / filename

            # Take screenshot
            take_screenshot(screenshot_path)

            # Add to pending list for next MEGA upload
            pending_screenshots.append(screenshot_path)

            rotate_screenshots(
                folder_path,
                max_size_mb,
                protected=set(pending_screenshots)
            )

            if mega_enabled and len(pending_screenshots) >= upload_batch_size:
                if mega_client is None:
                    mega_client = init_mega_client(config)

                if mega_client is not None:
                    uploaded = upload_batch_to_mega(
                        mega_client,
                        username,
                        mega_day_cache,
                        pending_screenshots
                    )
                    pending_screenshots = [
                        p for p in pending_screenshots if p not in uploaded
                    ]

            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        logging.info("ScreenGuard interrupted by user. Attempting final upload...")

        if mega_enabled and mega_client is not None and pending_screenshots:
            uploaded = upload_batch_to_mega(
                mega_client,
                username,
                mega_day_cache,
                pending_screenshots
            )
            pending_screenshots = [p for p in pending_screenshots if p not in uploaded]

        logging.info("Exiting cleanly.")
    except Exception as e:
        logging.exception("Unexpected error: %s", e)


if __name__ == "__main__":
    main()
