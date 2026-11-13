"""Bhashini (ULCA config + Dhruva inference) async client.

Two-call protocol:
  1. Config call (cached per task-chain + languages, TTL) -> serviceIds, callbackUrl, inference key.
  2. Compute call to callbackUrl with the inference key, chaining tasks in one request.

Chained tasks must be language-aligned: asr.source == translation.source and
translation.target == tts.source (validated before every request).

Telemetry: Dhruva returns no per-task timing, so a chained call is recorded as one span —
"bhashini.asr+nmt" under `asr_ms`, "bhashini.nmt+tts" under `tts_ms`, a plain translation
under `nmt_ms`.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from config.settings import Settings
from src.telemetry import stage

# 22 scheduled languages + English (ISO 639-1/3 codes used by Bhashini).
BHASHINI_LANGUAGES = frozenset("en as bn brx doi gom gu hi kn ks mai ml mni mr ne or pa sa sat sd ta te ur".split())


class BhashiniError(RuntimeError):
    """Any upstream failure (credentials, timeout, HTTP error, malformed response)."""


@dataclass(frozen=True)
class Task:
    task_type: str  # asr | translation | tts
    source: str
    target: str | None = None

    def language(self) -> dict[str, str]:
        lang = {"sourceLanguage": self.source}
        if self.target:
            lang["targetLanguage"] = self.target
        return lang


@dataclass
class PipelineConfig:
    callback_url: str
    auth_header: str
    auth_value: str
    service_ids: dict[str, str]
    fetched_at: float = field(default_factory=time.monotonic)


def validate_chain(tasks: list[Task]) -> None:
    """Enforce language alignment between consecutive chained tasks."""
    for prev, nxt in itertools.pairwise(tasks):
        produced = prev.target if prev.task_type == "translation" else prev.source
        if nxt.source != produced:
            raise ValueError(
                f"Misaligned chain: {prev.task_type} produces '{produced}' but {nxt.task_type} expects '{nxt.source}'"
            )
    for t in tasks:
        for code in filter(None, (t.source, t.target)):
            if code not in BHASHINI_LANGUAGES:
                raise ValueError(f"Language '{code}' is not supported by Bhashini")


class BhashiniClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=settings.bhashini_timeout_s)
        self._cache: dict[tuple[Task, ...], PipelineConfig] = {}
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.settings.bhashini_enabled

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Call 1: pipeline config (cached) ------------------------------------------------
    async def get_config(self, tasks: list[Task]) -> PipelineConfig:
        key = tuple(tasks)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached.fetched_at < self.settings.bhashini_config_ttl_s:
            return cached
        async with self._lock:
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached.fetched_at < self.settings.bhashini_config_ttl_s:
                return cached
            config = await self._fetch_config(tasks)
            self._cache[key] = config
            return config

    async def _fetch_config(self, tasks: list[Task]) -> PipelineConfig:
        if not self.enabled:
            raise BhashiniError("Bhashini credentials are not configured")
        assert self.settings.bhashini_ulca_api_key is not None
        body = {
            "pipelineTasks": [{"taskType": t.task_type, "config": {"language": t.language()}} for t in tasks],
            "pipelineRequestConfig": {"pipelineId": self.settings.bhashini_pipeline_id},
        }
        headers = {
            "userID": self.settings.bhashini_user_id or "",
            "ulcaApiKey": self.settings.bhashini_ulca_api_key.get_secret_value(),
        }
        data = await self._post(self.settings.bhashini_config_url, body, headers)
        try:
            endpoint = data["pipelineInferenceAPIEndPoint"]
            service_ids = {}
            for task, task_cfg in zip(tasks, data["pipelineResponseConfig"], strict=True):
                options = task_cfg["config"]
                match = next(
                    (o for o in options if o["language"].get("sourceLanguage") == task.source),
                    options[0],
                )
                service_ids[task.task_type] = match["serviceId"]
            return PipelineConfig(
                callback_url=endpoint["callbackUrl"],
                auth_header=endpoint["inferenceApiKey"].get("name", "Authorization"),
                auth_value=endpoint["inferenceApiKey"]["value"],
                service_ids=service_ids,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise BhashiniError(f"Unexpected config response shape: {exc!r}") from exc

    # --- Call 2: compute -------------------------------------------------------------------------
    async def compute(self, tasks: list[Task], input_data: dict[str, Any]) -> list[dict[str, Any]]:
        validate_chain(tasks)
        for attempt in (1, 2):
            config = await self.get_config(tasks)
            body = {
                "pipelineTasks": [self._task_body(t, config) for t in tasks],
                "inputData": input_data,
            }
            try:
                data = await self._post(config.callback_url, body, {config.auth_header: config.auth_value})
                return data["pipelineResponse"]
            except BhashiniError as exc:
                # An expired inference key surfaces as 401/403: refresh config once.
                if attempt == 1 and getattr(exc, "status", None) in (401, 403):
                    self._cache.pop(tuple(tasks), None)
                    continue
                raise
            except (KeyError, TypeError) as exc:
                raise BhashiniError(f"Unexpected compute response shape: {exc!r}") from exc
        raise BhashiniError("unreachable")

    def _task_body(self, t: Task, config: PipelineConfig) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "language": t.language(),
            "serviceId": config.service_ids[t.task_type],
        }
        if t.task_type == "asr":
            cfg |= {"audioFormat": "wav", "samplingRate": self.settings.audio_sample_rate}
        elif t.task_type == "tts":
            cfg |= {"gender": self.settings.bhashini_tts_gender}
        return {"taskType": t.task_type, "config": cfg}

    async def _post(self, url: str, body: dict, headers: dict[str, str]) -> dict[str, Any]:
        try:
            resp = await self._http.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise BhashiniError("Bhashini request timed out") from exc
        except httpx.HTTPError as exc:
            raise BhashiniError(f"Bhashini network error: {exc}") from exc
        if resp.status_code >= 400:
            err = BhashiniError(f"Bhashini HTTP {resp.status_code}: {resp.text[:200]}")
            err.status = resp.status_code  # type: ignore[attr-defined]
            raise err
        try:
            return resp.json()
        except ValueError as exc:
            raise BhashiniError("Bhashini returned non-JSON response") from exc

    # --- High-level operations ----------
    async def speech_to_english(self, audio_b64: str, source_lang: str) -> tuple[str, str]:
        """Stage A: ASR (+ NMT to English). Returns (source_text, english_text)."""
        tasks = [Task("asr", source_lang)]
        if source_lang != "en":
            tasks.append(Task("translation", source_lang, "en"))
        with stage("asr_ms", "bhashini.asr+nmt" if len(tasks) > 1 else "bhashini.asr"):
            out = await self.compute(tasks, {"audio": [{"audioContent": audio_b64}]})
        source_text = _output(out, "asr")["source"]
        english = _output(out, "translation")["target"] if len(tasks) > 1 else source_text
        return source_text, english

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        if source_lang == target_lang:
            return text
        with stage("nmt_ms", "bhashini.nmt"):
            out = await self.compute([Task("translation", source_lang, target_lang)], {"input": [{"source": text}]})
        return _output(out, "translation")["target"]

    async def translate_and_speak(self, text_en: str, target_lang: str) -> tuple[str, str]:
        """Stage B: NMT (English -> target) + TTS in one compute call. Returns (text, b64 wav)."""
        tasks = [Task("tts", target_lang)]
        if target_lang != "en":
            tasks.insert(0, Task("translation", "en", target_lang))
        with stage("tts_ms", "bhashini.nmt+tts" if len(tasks) > 1 else "bhashini.tts"):
            out = await self.compute(tasks, {"input": [{"source": text_en}]})
        text = _output(out, "translation")["target"] if len(tasks) > 1 else text_en
        try:
            tts = next(o for o in out if o.get("taskType") == "tts")
            return text, tts["audio"][0]["audioContent"]
        except (StopIteration, KeyError, IndexError) as exc:
            raise BhashiniError("No 'tts' audio in Bhashini response") from exc

    async def speak(self, text: str, language: str) -> str:
        """TTS only, for text that is already in `language`. Returns base64 WAV."""
        with stage("tts_ms", "bhashini.tts"):
            out = await self.compute([Task("tts", language)], {"input": [{"source": text}]})
        try:
            return next(o for o in out if o.get("taskType") == "tts")["audio"][0]["audioContent"]
        except (StopIteration, KeyError, IndexError) as exc:
            raise BhashiniError("No 'tts' audio in Bhashini response") from exc


def _output(response: list[dict[str, Any]], task_type: str) -> dict[str, Any]:
    for item in response:
        if item.get("taskType") == task_type and item.get("output"):
            return item["output"][0]
    raise BhashiniError(f"No '{task_type}' output in Bhashini response")
