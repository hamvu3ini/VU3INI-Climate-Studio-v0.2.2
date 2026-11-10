"""'Explain this result' panel: plain-language explanation from an LLM.

Providers: Ollama (local, free), Gemini API, Claude API. Only the result's
summary numbers, method note and info-card text are sent, never raw data or
the user's files. Model names are configurable in Settings because providers
rename models over time.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests

from core import settings as settings_mod
from core.result import AnalysisResult
from i18n import LANGUAGES

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    # Starting points; check the provider's current model list and change in Settings > AI.
    "ollama_gpu": "llama3.1:8b",
    "ollama_cpu": "llama3.2:3b",
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-5-5",
}

SYSTEM = (
    "You explain climate and weather analysis results to a non-specialist in plain, friendly language. "
    "Use ONLY the numbers given. Do not invent figures, places or dates. Say clearly when data is uncertain or limited. "
    "Do not give official forecasts, warnings or medical, legal or financial advice. "
    "Write 120-220 words in short paragraphs: what the result shows, what stands out, what it may mean in practice, "
    "and one caution about the data."
)


@dataclass
class Explanation:
    text: str
    provider: str
    model: str
    ok: bool = True
    error: str = ""


def build_prompt(res: AnalysisResult, language: str = "en") -> str:
    stats = ""
    if res.stats is not None and not res.stats.empty:
        stats = res.stats.head(25).to_csv(index=False)
    payload = {
        "analysis": res.title,
        "what_it_does": res.info_what,
        "area": res.area_name,
        "period": res.period_label,
        "baseline": res.baseline_label,
        "summary": res.summary,
        "key_numbers": res.key_numbers,
        "method": res.method_text(),
        "limitations": res.limitations,
        "warnings": res.warnings,
    }
    lang = LANGUAGES.get(language, "English")
    return (f"Explain this result in {lang}.\n\nRESULT (JSON):\n{json.dumps(payload, ensure_ascii=False, default=str, indent=1)}\n\n"
            f"STATISTICS TABLE (CSV, may be truncated):\n{stats}")


def default_model(provider: str) -> str:
    if provider == "ollama":
        from compute import device

        m = device.manager()
        return DEFAULT_MODELS["ollama_gpu"] if m.gpu_ok("llm") else DEFAULT_MODELS["ollama_cpu"]
    return DEFAULT_MODELS.get(provider, "")


def explain(res: AnalysisResult, language: str = "en", timeout: int = 180) -> Explanation:
    s = settings_mod.load()
    provider = s.ai_provider
    if provider in ("", "none", None):
        return Explanation("", "none", "", ok=False, error="not_configured")
    model = s.ai_model or default_model(provider)
    prompt = build_prompt(res, language)
    try:
        if provider == "ollama":
            text = _ollama(s.ollama_url, model, prompt, timeout)
        elif provider == "gemini":
            text = _gemini(model, prompt, timeout)
        elif provider == "claude":
            text = _claude(model, prompt, timeout)
        else:
            return Explanation("", provider, model, ok=False, error=f"Unknown provider {provider}")
        return Explanation(text.strip(), provider, model)
    except Exception as e:  # noqa: BLE001
        log.exception("AI explanation failed")
        return Explanation("", provider, model, ok=False, error=str(e))


def _ollama(url: str, model: str, prompt: str, timeout: int) -> str:
    r = requests.post(f"{url.rstrip('/')}/api/chat", timeout=timeout, json={
        "model": model, "stream": False, "options": {"temperature": 0.3},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    })
    if r.status_code == 404:
        raise RuntimeError(f"Ollama model '{model}' is not installed. Run: ollama pull {model}")
    r.raise_for_status()
    return r.json()["message"]["content"]


def ollama_models(url: str) -> list[str]:
    try:
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def _gemini(model: str, prompt: str, timeout: int) -> str:
    key = settings_mod.get_secret("gemini_api_key")
    if not key:
        raise RuntimeError("No Gemini API key saved. Add it in Settings > AI.")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"}, timeout=timeout,
        json={"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.3}},
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _claude(model: str, prompt: str, timeout: int) -> str:
    key = settings_mod.get_secret("anthropic_api_key")
    if not key:
        raise RuntimeError("No Claude API key saved. Add it in Settings > AI.")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 700, "temperature": 0.3, "system": SYSTEM,
              "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
