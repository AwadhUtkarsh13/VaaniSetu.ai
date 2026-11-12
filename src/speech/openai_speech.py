"""Speech via OpenAI: `gpt-4o-mini-transcribe` (speech -> text) and `gpt-4o-mini-tts` (text -> speech).

The default voice provider: it needs only the OpenAI key the agent already uses, handles Hindi,
English and code-mixed Hinglish speech, and costs well under a rupee per minute. Bhashini
(`bhashini.py`) remains available as an alternative via SPEECH_PROVIDER=bhashini.
"""

from __future__ import annotations

import re

from openai import AsyncOpenAI, OpenAIError

from config.settings import Settings
from src.telemetry import stage

TRANSCRIBE_PROMPT = (
    "An Indian citizen asking about PM-KISAN, Ayushman Bharat PM-JAY or filing a CPGRAMS "
    "grievance. Speech may mix Hindi and English (Hinglish)."
)
TTS_INSTRUCTIONS = (
    "Speak warmly and clearly at a calm, moderate pace, like a patient government help-desk "
    "assistant. Pronounce Hindi and Indian names naturally."
)

# How masked PII tokens are read aloud: never the raw value (it must not reach the TTS API).
_SPOKEN_TOKEN = {
    "AADHAAR": "your Aadhaar number", "PAN": "your PAN", "PHONE": "your phone number",
    "BANK_ACCOUNT": "your bank account number", "UPI": "your UPI ID", "EMAIL": "your email",
    "NAME": "the name you gave",
}  # fmt: skip


class SpeechError(RuntimeError):
    """Transcription or synthesis failed upstream."""


def speakable(text: str) -> str:
    """Prepare reply text for TTS: drop citations and markdown, voice PII tokens generically."""
    text = re.sub(r"\s*\[[^\]]*§[^\]]*\]", "", text)  # [PM-KISAN §2.1]
    text = re.sub(r"<([A-Z_]+)_\d+>", lambda m: _SPOKEN_TOKEN.get(m.group(1), "that detail"), text)
    text = re.sub(r"[*_`#>]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{2,}", "\n", text).strip()


def transcription_language(language: str) -> str | None:
    """OpenAI accepts ISO-639-1 hints; 'auto', 'hinglish' and 3-letter codes -> auto-detect."""
    return language if len(language) == 2 and language.isalpha() else None


class OpenAISpeech:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        if client is None:
            if not settings.openai_enabled:
                raise RuntimeError("OPENAI_API_KEY is required for OpenAI speech")
            assert settings.openai_api_key is not None
            client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
            )
        self._client = client
        self.settings = settings

    async def transcribe(self, audio: bytes, filename: str = "voice.wav", language: str = "auto") -> str:
        try:
            with stage("asr_ms", "openai.transcribe"):
                result = await self._client.audio.transcriptions.create(
                    model=self.settings.openai_transcribe_model,
                    file=(filename, audio),
                    prompt=TRANSCRIBE_PROMPT,
                    **({"language": lang} if (lang := transcription_language(language)) else {}),
                )
        except OpenAIError as exc:
            raise SpeechError(f"Transcription failed: {type(exc).__name__}") from exc
        return result.text.strip()

    async def synthesize(self, text: str) -> bytes:
        """Return MP3 bytes for `text` (already localised and made speakable)."""
        try:
            with stage("tts_ms", "openai.tts"):
                response = await self._client.audio.speech.create(
                    model=self.settings.openai_tts_model,
                    voice=self.settings.openai_tts_voice,
                    input=text[:4000],
                    instructions=TTS_INSTRUCTIONS,
                    response_format="mp3",
                )
        except OpenAIError as exc:
            raise SpeechError(f"Speech synthesis failed: {type(exc).__name__}") from exc
        return response.content
