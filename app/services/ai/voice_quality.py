"""Voice quality gate (task #50): WER measurement + transcription-aware confidence clamp.

Two jobs:

1. **Word Error Rate (WER)** — measure how well the ASR transcribes real audio.
   ``word_error_rate`` is the standard word-level edit-distance metric;
   ``evaluate_wer_corpus`` runs it over a labelled corpus given any transcribe
   callable, so the harness is pure and testable. Validating the *real* number
   needs real Nigerian-accent samples (see tests/fixtures/voice_wer/README.md);
   until those land the corpus test skips.

2. **Confidence clamp** — the model's self-reported extraction confidence is
   optimistic when the transcript it read is itself unreliable. ``clamp_confidence``
   lowers confidence (and flips ``requires_review``) for short transcripts, low ASR
   confidence, or high measured/estimated WER, so the mobile app forces the tech to
   confirm shaky extractions instead of silently trusting them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# Below this final confidence the tech must review before the form is accepted.
DEFAULT_REVIEW_THRESHOLD = 0.7
_MIN_RELIABLE_WORDS = 3
_LOW_ASR_CONFIDENCE = 0.6
_HIGH_WER = 0.3


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in str(text or "")).split()]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance / reference length (Levenshtein over tokens).

    Empty reference: 0.0 if the hypothesis is also empty, else 1.0.
    """
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    # Classic DP edit distance over token lists.
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cost = 0 if r == h else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(hyp)] / len(ref)


@dataclass(frozen=True)
class WerSample:
    key: str
    reference: str


def evaluate_wer_corpus(samples: list[WerSample], transcribe: Callable[[WerSample], str]) -> dict:
    """Transcribe each sample and report per-sample + mean WER.

    ``transcribe`` maps a sample to its ASR hypothesis (real or stubbed), keeping
    this function free of any audio/IO dependency.
    """
    rows = []
    for sample in samples:
        hypothesis = transcribe(sample)
        rows.append(
            {
                "key": sample.key,
                "reference": sample.reference,
                "hypothesis": hypothesis,
                "wer": word_error_rate(sample.reference, hypothesis),
            }
        )
    mean_wer = sum(r["wer"] for r in rows) / len(rows) if rows else None
    return {"count": len(rows), "mean_wer": mean_wer, "samples": rows}


@dataclass(frozen=True)
class QualityVerdict:
    confidence: float
    requires_review: bool
    reasons: list[str] = field(default_factory=list)


def clamp_confidence(
    model_confidence: float | None,
    *,
    transcript: str,
    asr_confidence: float | None = None,
    estimated_wer: float | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
) -> QualityVerdict:
    """Lower extraction confidence when the underlying transcript is unreliable."""
    confidence = 0.5 if model_confidence is None else max(0.0, min(1.0, float(model_confidence)))
    reasons: list[str] = []

    word_count = len(_tokens(transcript))
    if word_count < _MIN_RELIABLE_WORDS:
        confidence = min(confidence, 0.3)
        reasons.append("transcript_too_short")

    if asr_confidence is not None and asr_confidence < _LOW_ASR_CONFIDENCE:
        confidence = min(confidence, max(0.0, float(asr_confidence)))
        reasons.append("low_asr_confidence")

    if estimated_wer is not None and estimated_wer > _HIGH_WER:
        confidence = min(confidence, max(0.0, 1.0 - float(estimated_wer)))
        reasons.append("high_wer")

    confidence = max(0.0, min(1.0, confidence))
    return QualityVerdict(
        confidence=confidence,
        requires_review=confidence < review_threshold,
        reasons=reasons,
    )
