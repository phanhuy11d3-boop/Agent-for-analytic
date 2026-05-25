import os
import json
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from urllib.parse import urlparse, unquote
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        return False

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

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEYS = [
    key.strip()
    for key in os.getenv("GROQ_API_KEYS", GROQ_API_KEY).replace(";", ",").split(",")
    if key.strip()
]
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
OPENROUTER_MODELS = [
    model.strip()
    for model in os.getenv("OPENROUTER_MODELS", OPENROUTER_MODEL).replace(";", ",").split(",")
    if model.strip()
]
OPENROUTER_TIMEOUT = float(os.getenv("OPENROUTER_TIMEOUT", "20"))
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

NINEROUTER_API_KEY = os.getenv("NINEROUTER_API_KEY", "")
NINEROUTER_BASE_URL = os.getenv("NINEROUTER_BASE_URL", "http://localhost:20128/v1")
NINEROUTER_MODEL = os.getenv("NINEROUTER_MODEL", "kc/deepseek/deepseek-chat")
NINEROUTER_TIMEOUT = float(os.getenv("NINEROUTER_TIMEOUT", "20"))
NINEROUTER_URL = f"{NINEROUTER_BASE_URL}/chat/completions"

QUERY_SAMPLE_LIMIT = int(os.getenv("QUERY_SAMPLE_LIMIT", "1000"))
PROFILE_SAMPLE_LIMIT = int(os.getenv("PROFILE_SAMPLE_LIMIT", "1000"))
SEMANTIC_PROFILE_SAMPLE_LIMIT = int(os.getenv("SEMANTIC_PROFILE_SAMPLE_LIMIT", "300"))
SEMANTIC_PROPOSAL_USE_LLM = os.getenv("SEMANTIC_PROPOSAL_USE_LLM", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
PROFILE_TOP_N = int(os.getenv("PROFILE_TOP_N", "10"))
PROFILE_COLUMN_LIMIT = int(os.getenv("PROFILE_COLUMN_LIMIT", "80"))
SQL_ROW_LIMIT = QUERY_SAMPLE_LIMIT  # Backward-compatible alias for older imports.
SQL_TIMEOUT = 30
ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "180"))
MAX_AGENT_STEPS = 10


def _is_groq_rate_limit_error(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text


def _message_to_openrouter(message) -> dict:
    msg_type = getattr(message, "type", "")
    role = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
    }.get(msg_type, "user")
    return {"role": role, "content": str(getattr(message, "content", message))}


def invoke_openrouter(messages, temperature: float = 0):
    if not OPENROUTER_API_KEY:
        raise RuntimeError("No OpenRouter API key configured")

    openrouter_messages = [_message_to_openrouter(message) for message in messages]
    last_error = None

    for idx, model in enumerate(OPENROUTER_MODELS or [OPENROUTER_MODEL]):
        payload = {
            "model": model,
            "messages": openrouter_messages,
            "temperature": temperature,
        }
        request = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "AI Data Analyst",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
            try:
                return SimpleNamespace(content=result["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as error:
                raise RuntimeError(f"Unexpected OpenRouter response: {result}") from error
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"OpenRouter error {error.code}: {body}")
            if idx < len(OPENROUTER_MODELS) - 1 and error.code in {404, 429, 503}:
                continue
            raise last_error from error

    raise last_error or RuntimeError("OpenRouter call failed")


def invoke_ninerouter(messages, temperature: float = 0):
    if not NINEROUTER_API_KEY:
        raise RuntimeError("No 9router API key configured")

    ninerouter_messages = [_message_to_openrouter(m) for m in messages]
    payload = {
        "model": NINEROUTER_MODEL,
        "messages": ninerouter_messages,
        "temperature": temperature,
    }
    request = urllib.request.Request(
        NINEROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {NINEROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=NINEROUTER_TIMEOUT) as response:
            raw_body = response.read().decode("utf-8")
        try:
            result = json.loads(raw_body)
        except json.JSONDecodeError as jde:
            brace_count = 0
            json_str = ""
            for char in raw_body:
                json_str += char
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        break
            if json_str:
                result = json.loads(json_str)
            else:
                raise jde
        elapsed = round(time.time() - t0, 2)
        print(f"[9router] {NINEROUTER_MODEL} responded in {elapsed}s")
        try:
            return SimpleNamespace(content=result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"Unexpected 9router response: {result}") from error
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"9router error {error.code}: {body}") from error


def invoke_groq(messages, temperature: float = 0):
    if LLM_PROVIDER == "ninerouter":
        return invoke_ninerouter(messages, temperature=temperature)
    if LLM_PROVIDER == "openrouter":
        return invoke_openrouter(messages, temperature=temperature)

    if not GROQ_API_KEYS:
        raise RuntimeError("No Groq API key configured")

    last_error = None
    for idx, api_key in enumerate(GROQ_API_KEYS):
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=GROQ_MODEL, api_key=api_key, temperature=temperature)
        try:
            return llm.invoke(messages)
        except Exception as error:
            last_error = error
            if idx < len(GROQ_API_KEYS) - 1 and _is_groq_rate_limit_error(error):
                continue
            raise

    raise last_error
