"""Voice quality gate: WER metric, corpus harness, confidence clamp (task #50)."""

import json
from pathlib import Path

import pytest

from app.services.ai.voice_quality import (
    WerSample,
    clamp_confidence,
    evaluate_wer_corpus,
    word_error_rate,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "voice_wer"


def test_wer_perfect_and_empty():
    assert word_error_rate("installed the ont", "installed the ont") == 0.0
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "spurious words") == 1.0


def test_wer_substitution_insertion_deletion():
    # 3 ref words, one substitution → 1/3.
    assert word_error_rate("signal minus twenty", "signal minus thirty") == pytest.approx(1 / 3)
    # one insertion against 3 ref words → 1/3.
    assert word_error_rate("used drop cable", "used the drop cable") == pytest.approx(1 / 3)
    # one deletion against 3 ref words → 1/3.
    assert word_error_rate("used drop cable", "used cable") == pytest.approx(1 / 3)


def test_wer_is_punctuation_and_case_insensitive():
    assert word_error_rate("Installed the ONT.", "installed the ont") == 0.0


def test_evaluate_wer_corpus_with_stub_transcriber():
    samples = [
        WerSample(key="a", reference="installed the ont"),
        WerSample(key="b", reference="used forty metres of cable"),
    ]
    # Stub ASR: perfect on the first, one error on the second.
    hyps = {"a": "installed the ont", "b": "used forty meters of cable"}
    report = evaluate_wer_corpus(samples, lambda s: hyps[s.key])

    assert report["count"] == 2
    assert report["samples"][0]["wer"] == 0.0
    assert report["samples"][1]["wer"] == pytest.approx(1 / 5)
    assert report["mean_wer"] == pytest.approx((0.0 + 1 / 5) / 2)


def test_clamp_passes_clean_high_confidence():
    verdict = clamp_confidence(0.92, transcript="installed the ont serial number all good")
    assert verdict.confidence == 0.92
    assert verdict.requires_review is False
    assert verdict.reasons == []


def test_clamp_short_transcript():
    verdict = clamp_confidence(0.95, transcript="done")
    assert verdict.confidence <= 0.3
    assert verdict.requires_review is True
    assert "transcript_too_short" in verdict.reasons


def test_clamp_low_asr_confidence():
    verdict = clamp_confidence(0.9, transcript="installed the ont and tested the line", asr_confidence=0.4)
    assert verdict.confidence <= 0.4
    assert "low_asr_confidence" in verdict.reasons
    assert verdict.requires_review is True


def test_clamp_high_wer():
    verdict = clamp_confidence(0.9, transcript="installed the ont and tested the line", estimated_wer=0.5)
    assert verdict.confidence <= 0.5
    assert "high_wer" in verdict.reasons


def test_clamp_none_confidence_defaults_mid():
    verdict = clamp_confidence(None, transcript="installed the ont and tested the line thoroughly")
    assert verdict.confidence == 0.5
    assert verdict.requires_review is True  # 0.5 < 0.7 threshold


def test_wer_corpus_against_real_samples():
    """Runs the real ASR over labelled samples once they're provided.

    Skips until tests/fixtures/voice_wer/manifest.json exists — see the README.
    Wire the real transcriber here when samples land.
    """
    manifest = _FIXTURES / "manifest.json"
    if not manifest.exists():
        pytest.skip("No real voice WER corpus yet — add tests/fixtures/voice_wer/manifest.json")

    entries = json.loads(manifest.read_text())
    samples = [WerSample(key=e["key"], reference=e["reference"]) for e in entries]
    assert samples, "manifest.json present but empty"
    # NOTE: replace with a real transcriber bound to the configured ASR endpoint.
    pytest.skip("Real-ASR transcriber not wired in this environment")
