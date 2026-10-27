"""LLM client: vLLM-ийн OpenAI-compatible /v1/chat/completions. Төлбөртэй API ашиглахгүй — бүх загвар локал.

Онцлог: n хувилбар (санал хураалт), seed, assistant prefill (vLLM: continue_final_message), chat_template_kwargs
(Qwen3/3.5-ийн enable_thinking), token тоо ба хугацаа бүртгэнэ. MockLLM — тест, oracle горимд.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    texts: list[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    raw: dict = field(default_factory=dict)


class LLM(Protocol):
    name: str

    def chat(self, messages: list[dict], *, n: int = 1, temperature: float = 0.0, top_p: float = 1.0,
             max_tokens: int = 2048, stop: list[str] | None = None, seed: int | None = None,
             prefill: str | None = None) -> LLMResponse: ...


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str = "EMPTY", timeout_s: float = 180,
                 extra_body: dict | None = None, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.name = model
        self.extra_body = dict(extra_body or {})
        self.max_retries = max_retries
        self.client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0),
                                   headers={"Authorization": f"Bearer {api_key}"},
                                   limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
        self._usage_lock = threading.Lock()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def models(self) -> list[dict]:
        r = self.client.get(f"{self.base_url}/models")
        r.raise_for_status()
        return r.json().get("data", [])

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None) -> LLMResponse:
        msgs = list(messages)
        body = {"model": self.name, "messages": msgs, "n": n, "temperature": temperature, "top_p": top_p,
                "max_tokens": max_tokens}
        if stop:
            body["stop"] = stop
        if seed is not None:
            body["seed"] = seed
        if prefill:
            msgs.append({"role": "assistant", "content": prefill})
            body["continue_final_message"] = True
            body["add_generation_prompt"] = False
        body.update(self.extra_body)
        delay = 2.0
        last_err = None
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                r = self.client.post(f"{self.base_url}/chat/completions", json=body)
                if r.status_code == 400 and "context length" in r.text and attempt < self.max_retries:
                    # prompt + max_tokens > max_model_len: гаралтын хязгаарыг багасгаж дахин (нэг удаа хангалттай)
                    new_max = _fit_max_tokens(r.text, int(body["max_tokens"]))
                    if new_max and new_max < int(body["max_tokens"]):
                        log.warning("Context хэтэрсэн — max_tokens %s → %s", body["max_tokens"], new_max)
                        body["max_tokens"] = new_max
                        continue
                if r.status_code == 400 and prefill and "continue_final_message" in r.text:
                    # prefill дэмжихгүй сервер — prefill-гүйгээр дахин
                    body.pop("continue_final_message", None)
                    body.pop("add_generation_prompt", None)
                    body["messages"] = msgs[:-1]
                    prefill = None
                    continue
                r.raise_for_status()
                data = r.json()
                texts = [(prefill or "") + (c.get("message", {}).get("content") or "") for c in data["choices"]]
                usage = data.get("usage") or {}
                resp = LLMResponse(texts, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                   (time.perf_counter() - t0) * 1000, data.get("model", self.name), data)
                with self._usage_lock:
                    self.total_prompt_tokens += resp.prompt_tokens
                    self.total_completion_tokens += resp.completion_tokens
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_err = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    break
                log.warning("LLM алдаа (%s), %d-р оролдлого: %s", self.name, attempt + 1, e)
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"LLM дуудлага амжилтгүй: {last_err}")


def _fit_max_tokens(error_text: str, current: int) -> int | None:
    """vLLM-ийн "maximum context length is N ... M input tokens" алдаанаас багтах max_tokens-ийг тооцно."""
    import re
    m_ctx = re.search(r"maximum context length is (\d+)", error_text)
    m_in = re.search(r"(\d+) (?:input tokens|in the messages|tokens in the messages)", error_text) or \
        re.search(r"request has (\d+) input", error_text)
    if m_ctx and m_in:
        room = int(m_ctx.group(1)) - int(m_in.group(1)) - 8
        return room if room >= 256 else None
    return current // 2 if current >= 512 else None


class MockLLM:
    """Тестэд: responder(messages) → текст. Oracle горимд gold SQL-ийг загварын протоколоор буцаана."""

    def __init__(self, responder: Callable[[list[dict]], str], name: str = "mock"):
        self.responder = responder
        self.name = name

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None) -> LLMResponse:
        t0 = time.perf_counter()
        texts = [(prefill or "") + self.responder(messages) for _ in range(n)]
        return LLMResponse(texts, sum(len(m["content"]) // 4 for m in messages), sum(len(t) // 4 for t in texts),
                           (time.perf_counter() - t0) * 1000, self.name)


def make_llm(llm_cfg: dict) -> OpenAICompatLLM:
    return OpenAICompatLLM(llm_cfg["base_url"], llm_cfg["model"], llm_cfg.get("api_key", "EMPTY"),
                           # T2S_LLM_TIMEOUT_S: удаан GPU (Kaggle T4) дээр урт reasoning-д хугацааг сунгана
                           float(os.environ.get("T2S_LLM_TIMEOUT_S") or llm_cfg.get("timeout_s", 180)),
                           llm_cfg.get("extra_body") or {})
