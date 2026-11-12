"""Indian PII redaction middleware (runs before any text reaches an LLM, log or trace).

Detection: Presidio `AnalyzerEngine` with a hand-built registry containing custom Indian
recognizers (Aadhaar w/ Verhoeff, PAN, mobile, bank account, UPI) plus email and spaCy
PERSON. Replacement: a small overlap resolver + `PIIVault`, which assigns stable,
left-to-right, per-session tokens such as `<AADHAAR_1>` and keeps the raw values only in
local process memory (see CLAUDE.md ADR-1).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import EmailRecognizer, SpacyRecognizer

# Presidio warns on every unmapped spaCy label (CARDINAL, ORG, ...); they are irrelevant here.
logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)

# Presidio entity type -> token label used in redacted text.
TOKEN_LABELS: dict[str, str] = {
    "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "IN_PHONE": "PHONE",
    "IN_BANK_ACCOUNT": "BANK_ACCOUNT",
    "IN_UPI_ID": "UPI",
    "EMAIL_ADDRESS": "EMAIL",
    "PERSON": "NAME",
}
DEFAULT_ENTITIES = list(TOKEN_LABELS)
TOKEN_RE = re.compile(r"<([A-Z_]+)_(\d+)>")

# Product names, the only domain terms that never appear in the policy documents themselves.
PRODUCT_TERMS = ("VaaniSetu", "Bhashini")


@lru_cache(maxsize=1)
def corpus_vocabulary() -> frozenset[str]:
    """Capitalised words used in the official policy documents (scheme names, bodies, places).

    spaCy's small English model tags terms like "Ayushman Bharat" as PERSON; a spaCy guess made
    only of these words is domain vocabulary, not a citizen's name. Derived from the corpus, so it
    grows with the documents instead of being maintained by hand.
    """
    from config.settings import get_settings
    from src.rag.ingest import load_corpus

    words = {t.lower() for t in PRODUCT_TERMS}
    for c in load_corpus(get_settings().policies_dir):
        for text in (c.doc_title, c.parent_title, c.section_title, c.body):
            words.update(w.lower() for w in re.findall(r"\b[A-Z][A-Za-z]*(?:-[A-Z][A-Za-z]*)*\b", text))
    return frozenset(words)


# Romanised Hindi function words that spaCy's English model mis-tags as names ("Mera").
HINGLISH_STOPWORDS = frozenset(
    "mera meri mere mujhe main mai mein me hum hamara hamari aap aapka aapki tum kya hai hain "
    "h tha thi aur ka ki ke ko se nahi nahin haan han ji kaise kitna kitne kab kahan kyun kyu "
    "bhai bhaiya sir madam sahab namaste naam gaon zila sarkar yojana paisa paise kist khata "
    "kripya please hello hi dear "
    # English connectives that can follow a name ("my name is Ramesh and I ...")
    "and or from in at of the i my is am was with to for live living here "
    # Devanagari function words
    "है हैं था थी और का की के को से में मैं मेरा मेरी नाम जी".split()
)

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# --- Verhoeff checksum (used by UIDAI for the 12th Aadhaar digit) ----------
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]  # fmt: skip
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]  # fmt: skip
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_validate(number: str) -> bool:
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(digit)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(digit)]]
    return str(_INV[c])


# --- Custom recognizers ----------
class AadhaarRecognizer(PatternRecognizer):
    """12 digits, first digit 2-9, optional space/hyphen grouping.

    Verhoeff-valid numbers score 1.0. Checksum-invalid numbers keep a low base score and
    are only masked when Aadhaar context words appear nearby (likely a typo'd Aadhaar).
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="IN_AADHAAR",
            name="IndianAadhaarRecognizer",
            patterns=[Pattern("aadhaar", r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)", 0.4)],
            context=["aadhaar", "aadhar", "adhaar", "adhar", "uid", "uidai", "आधार"],
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = re.sub(r"\D", "", pattern_text)
        if verhoeff_validate(digits) and digits != digits[::-1]:
            return True
        return None  # keep base score; context may still push it over the threshold


class IntroducedNameRecognizer(EntityRecognizer):
    """Names introduced explicitly ("my name is ...", "mera naam ... hai", "मेरा नाम ...").

    spaCy's small English model misses many Indian names, especially in lower-case
    Hinglish, so self-introductions are caught deterministically.
    """

    TRIGGER = re.compile(r"(?i:\b(?:my name is|my name's|name is|this is|i am|i'm|mera naam|naam)\s+|मेरा नाम\s+)")
    WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z][a-z]*|[\u0900-\u097F]+")

    def __init__(self) -> None:
        super().__init__(supported_entities=["PERSON"], name="IntroducedNameRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text, entities, nlp_artifacts=None):  # type: ignore[override]
        results = []
        for trigger in self.TRIGGER.finditer(text):
            phrase = trigger.group(0).lower()
            explicit = any(k in phrase for k in ("name", "naam", "नाम"))
            start = end = trigger.end()
            words = 0
            for word in self.WORD.finditer(text, trigger.end()):
                gap = text[end : word.start()]
                lowered = word.group(0).lower()
                if words == 3 or (words and gap.strip()) or lowered in HINGLISH_STOPWORDS:
                    break
                # "i am X" is only a name if X is capitalised ("I am Ramesh", not "I am a farmer").
                if not explicit and not word.group(0)[0].isupper():
                    break
                end = word.end()
                words += 1
            if words:
                results.append(RecognizerResult("PERSON", start, end, 0.85))
        return results


def _pattern(entity: str, name: str, regex: str, score: float, context: list[str]):
    return PatternRecognizer(
        supported_entity=entity,
        name=name,
        patterns=[Pattern(entity.lower(), regex, score)],
        context=context,
    )


def build_recognizers() -> list[EntityRecognizer]:
    phone_ctx = ["mobile", "phone", "contact", "number", "call", "whatsapp", "mob", "फोन",
                 "मोबाइल", "नंबर"]  # fmt: skip
    return [
        AadhaarRecognizer(),
        # PAN: AAAAA9999A; 4th char encodes holder type (P=individual, C=company, ...).
        _pattern("IN_PAN", "IndianPanRecognizer",
                 r"\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b", 0.8, ["pan", "permanent account"]),
        _pattern("IN_PHONE", "IndianMobileRecognizer",
                 r"(?<![\w+])(?:\+91[\-\s]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)", 0.6, phone_ctx),
        # Bank account numbers are 9-18 digits; far too generic without context words.
        _pattern("IN_BANK_ACCOUNT", "IndianBankAccountRecognizer", r"(?<!\d)\d{9,18}(?!\d)",
                 0.2, ["account", "a/c", "acc", "acct", "khata", "bank", "खाता", "बैंक"]),
        _pattern("IN_UPI_ID", "IndianUpiRecognizer",
                 r"\b[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}\b(?![.\w])", 0.6, ["upi", "vpa", "pay"]),
        EmailRecognizer(),
        SpacyRecognizer(supported_entities=["PERSON"]),
        IntroducedNameRecognizer(),
    ]  # fmt: skip


@lru_cache(maxsize=1)
def build_analyzer() -> AnalyzerEngine:
    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=["en"])
    for recognizer in build_recognizers():
        registry.add_recognizer(recognizer)
    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=["en"],
        context_aware_enhancer=LemmaContextAwareEnhancer(context_prefix_count=6, context_suffix_count=2),
    )


# --- Vault & redaction ----------
class PIIVault:
    """Session-scoped reversible token map. Lives only in local memory; never serialised."""

    def __init__(self) -> None:
        self._token_by_value: dict[tuple[str, str], str] = {}
        self._value_by_token: dict[str, str] = {}
        self._counters: Counter[str] = Counter()

    def tokenize(self, label: str, value: str) -> str:
        key = (label, re.sub(r"[\s-]", "", value).upper())
        if key not in self._token_by_value:
            self._counters[label] += 1
            token = f"<{label}_{self._counters[label]}>"
            self._token_by_value[key] = token
            self._value_by_token[token] = value
        return self._token_by_value[key]

    def restore(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self._value_by_token.get(m.group(0), m.group(0)), text)

    def __len__(self) -> int:
        return len(self._value_by_token)

    def __repr__(self) -> str:  # never leak values via repr/logging
        return f"PIIVault(tokens={len(self)})"


@dataclass(frozen=True)
class RedactionEvent:
    """Audit record of one masked span. Deliberately carries no raw value."""

    entity_type: str
    token: str
    score: float


@dataclass
class RedactionResult:
    text: str
    events: list[RedactionEvent] = field(default_factory=list)

    @property
    def masked_count(self) -> int:
        return len(self.events)


def _trim_person(text: str, r: RecognizerResult) -> RecognizerResult | None:
    """Strip Hinglish function words from the edges of a PERSON span ("Mera Ramesh hai").

    spaCy-sourced names must also be capitalised: on romanised Hindi the English model
    tags lower-case verbs ("aayegi", "karni") as PERSON. Lower-case names typed in
    Hinglish are still caught by `IntroducedNameRecognizer`.
    """
    words = list(re.finditer(r"\S+", text[r.start : r.end]))
    from_spacy = (r.recognition_metadata or {}).get("recognizer_name") == "SpacyRecognizer"
    if from_spacy and not all(w.group(0)[0].isupper() for w in words):
        return None
    while words and words[0].group(0).strip(".,!?").lower() in HINGLISH_STOPWORDS:
        words.pop(0)
    while words and words[-1].group(0).strip(".,!?").lower() in HINGLISH_STOPWORDS:
        words.pop()
    if not words:
        return None
    return RecognizerResult("PERSON", r.start + words[0].start(), r.start + words[-1].end(), r.score)


def _resolve_overlaps(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Keep the highest-scoring (then longest) span wherever detections overlap."""
    kept: list[RecognizerResult] = []
    for r in sorted(results, key=lambda r: (-r.score, -(r.end - r.start), r.start)):
        if all(r.end <= k.start or r.start >= k.end for k in kept):
            kept.append(r)
    return sorted(kept, key=lambda r: r.start)


class PIIRedactor:
    def __init__(
        self,
        score_threshold: float = 0.5,
        entities: list[str] | None = None,
        domain_vocabulary: frozenset[str] | None = None,
    ) -> None:
        self.score_threshold = score_threshold
        self.entities = entities or DEFAULT_ENTITIES
        self.vocabulary = domain_vocabulary if domain_vocabulary is not None else corpus_vocabulary()
        self._analyzer = build_analyzer()

    def _is_domain_term(self, text: str, span: RecognizerResult, source: RecognizerResult) -> bool:
        """A spaCy PERSON guess made only of policy-document words (explicit introductions never are)."""
        from_spacy = (source.recognition_metadata or {}).get("recognizer_name") == "SpacyRecognizer"
        words = re.findall(r"[^\s.,!?]+", text[span.start : span.end].lower())
        return from_spacy and bool(words) and all(w in self.vocabulary for w in words)

    def detect(self, text: str) -> list[RecognizerResult]:
        results = self._analyzer.analyze(
            text=text,
            language="en",
            entities=self.entities,
            score_threshold=self.score_threshold,
        )
        # Never re-mask (or partially match inside) tokens from an earlier pass.
        token_spans = [m.span() for m in TOKEN_RE.finditer(text)]
        cleaned: list[RecognizerResult] = []
        for r in results:
            if any(r.start < end and r.end > start for start, end in token_spans):
                continue
            trimmed = _trim_person(text, r) if r.entity_type == "PERSON" else r
            if trimmed is not None and not (r.entity_type == "PERSON" and self._is_domain_term(text, trimmed, r)):
                cleaned.append(trimmed)
        return _resolve_overlaps(cleaned)

    def redact(self, text: str, vault: PIIVault) -> RedactionResult:
        text = text.translate(_DEVANAGARI_DIGITS)
        if not text.strip():
            return RedactionResult(text=text)
        pieces: list[str] = []
        events: list[RedactionEvent] = []
        cursor = 0
        for r in self.detect(text):
            label = TOKEN_LABELS[r.entity_type]
            token = vault.tokenize(label, text[r.start : r.end])
            pieces.extend([text[cursor : r.start], token])
            events.append(RedactionEvent(r.entity_type, token, round(r.score, 2)))
            cursor = r.end
        pieces.append(text[cursor:])
        return RedactionResult(text="".join(pieces), events=events)
