import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
QR_UPLOAD_CHANNEL = os.getenv("QR_UPLOAD_CHANNEL", "")

import time as _time

START_TIME = _time.time()

ADMIN_USERNAMES = [
    u.strip().lstrip("@").lower()
    for u in os.getenv("ADMIN_USERNAMES", "inhgalator").split(",")
    if u.strip()
]
ADMIN_IDS = [
    int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip().isdigit()
]


def is_admin(user) -> bool:
    if not user:
        return False
    uname = getattr(user, "username", None)
    return (
        (uname is not None and uname.lower() in ADMIN_USERNAMES)
        or user.id in ADMIN_IDS
    )


MAX_FILE_MB = 19
MAX_PARALLEL = max(1, int(os.getenv("MAX_PARALLEL", "2")))
FFMPEG_THREADS = max(1, int(os.getenv("FFMPEG_THREADS", "1")))
NICE_LEVEL = max(0, int(os.getenv("NICE_LEVEL", "15")))

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
TMP_DIR = Path(os.getenv("TMP_DIR", str(DATA_DIR / "tmp")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db")))


def _button_icons() -> dict:
    icons = {}
    for key, value in os.environ.items():
        if key.startswith("ICON_") and value.strip():
            icons[key[5:].lower().replace("_", ":")] = value.strip()
    return icons


BUTTON_ICONS = _button_icons()
