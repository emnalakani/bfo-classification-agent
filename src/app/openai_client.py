"""LLM client configuration.

The backend talks to any provider exposing the OpenAI-compatible chat API
(OpenAI, or Claude through https://api.anthropic.com/v1/). Settings use
provider-neutral LLM_* names, read from a .env file (searched from this folder
upwards, e.g. at the repository root), which overrides variables already
defined in the environment. Old OPENAI_* names are still accepted.
"""
import os

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

load_dotenv(override=True)


def _env(name: str, default: str = "") -> str:
    value = os.getenv(f"LLM_{name}")
    if value is None or not value.strip():
        value = os.getenv(f"OPENAI_{name}", default)
    return (value or "").strip().strip('"').strip("'")


API_KEY = _env("API_KEY")
BASE_URL = _env("BASE_URL") or None
MODEL = _env("MODEL", "gpt-4o")
MAX_TOKENS = int(_env("MAX_TOKENS", "4000"))
# 0 = as deterministic as the provider allows (recommended for evaluation runs).
# Empty or "none" = do not send a temperature (some models reject it).
_temp = _env("TEMPERATURE", "0").lower()
TEMPERATURE = None if _temp in ("", "none", "default") else float(_temp)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")

if not API_KEY:
    raise RuntimeError(
        "LLM_API_KEY is missing. Copy configs/llm.env.example to .env "
        "and paste your API key."
    )

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# Safe diagnostics (never the key itself): key prefix and length.
KEY_HINT = f"{API_KEY[:7]}…({len(API_KEY)} chars)"


# Some models reject the temperature parameter. In that case it is dropped
# automatically for the rest of the session, and /api/health reports it.
TEMPERATURE_STATE = {"sent": TEMPERATURE is not None}


def create_chat_completion(**kwargs):
    """client.chat.completions.create with an automatic temperature fallback."""
    if TEMPERATURE_STATE["sent"]:
        kwargs["temperature"] = TEMPERATURE
    try:
        return client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in kwargs and "temperature" in str(e).lower():
            TEMPERATURE_STATE["sent"] = False
            kwargs.pop("temperature")
            return client.chat.completions.create(**kwargs)
        raise


def temperature_status() -> str:
    if TEMPERATURE_STATE["sent"]:
        return str(TEMPERATURE)
    if TEMPERATURE is None:
        return "not sent (provider default)"
    return "rejected by this model: not sent (provider default)"
