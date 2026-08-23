import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

MAX_FILE_MB = 19

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
TMP_DIR = Path(os.getenv("TMP_DIR", str(DATA_DIR / "tmp")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "bot.db")))
