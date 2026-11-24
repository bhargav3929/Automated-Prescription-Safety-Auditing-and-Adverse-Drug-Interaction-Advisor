import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _str(name, default):
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


PROJECT_NAME = _str("PROJECT_NAME", "Automated Prescription Safety Auditing and Adverse Drug Interaction Advisor")
PROJECT_SLUG = _str("PROJECT_SLUG", "prescription-safety-advisor")
PROJECT_NUMBER = _int(os.environ.get("PROJECT_NUMBER"), 9)
VERSION = "1.0.0"
HOST = _str("HOST", "127.0.0.1")
PORT = _int(os.environ.get("PORT"), 8009)
MAX_UPLOAD_MB = _int(os.environ.get("MAX_UPLOAD_MB"), 1)
DEMO_MODE = _bool(os.environ.get("DEMO_MODE"), False)
DEMO_TICKET_SECRET = os.environ.get("DEMO_TICKET_SECRET", "").strip()
DEMO_DAILY_CAP = _int(os.environ.get("DEMO_DAILY_CAP"), 200)
DEMO_MAX_UPLOAD_MB = _int(os.environ.get("DEMO_MAX_UPLOAD_MB"), 1)
TRUST_PROXY = _bool(os.environ.get("TRUST_PROXY"), False)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = _str("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = _str("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

RXNORM_API_BASE = _str("RXNORM_API_BASE", "https://rxnav.nlm.nih.gov/REST").rstrip("/")
OPENFDA_API_BASE = _str("OPENFDA_API_BASE", "https://api.fda.gov/drug").rstrip("/")

HOURLY_RUNS = 15
DB_PATH = ROOT / "data" / "prescription_audit.db"
SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = ROOT / "static"
