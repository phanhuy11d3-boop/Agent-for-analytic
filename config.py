import os
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv

load_dotenv(override=True)

# Parse URL while keeping each component encoded, then decode individually
# This avoids the issue of decoded '@' or '/' in passwords breaking URL parsing
_raw_url = os.getenv("DATABASE_URL", "")
_parsed  = urlparse(_raw_url)

DB_HOST     = _parsed.hostname or ""
DB_PORT     = _parsed.port or 5432
DB_USER     = unquote(_parsed.username or "")
DB_PASSWORD = unquote(_parsed.password or "")
DB_NAME     = _parsed.path.lstrip("/")

# Keep the original encoded URL as well (needed by some ORMs)
DATABASE_URL = _raw_url

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

SQL_ROW_LIMIT   = 1000
SQL_TIMEOUT     = 30
MAX_AGENT_STEPS = 10
