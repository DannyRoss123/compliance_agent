from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("GUARDIANS_DATA_DIR", BASE_DIR / "data"))
TASKS_DIR = DATA_DIR / "tasks"
PRS_DIR = DATA_DIR / "prs"
RUNS_DIR = DATA_DIR / "runs"

API_TOKEN = os.getenv("GUARDIANS_API_TOKEN")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", API_TOKEN)
GITHUB_ACCESS_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'guardians.db'}")


def ensure_directories() -> None:
    for directory in (DATA_DIR, TASKS_DIR, PRS_DIR, RUNS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
