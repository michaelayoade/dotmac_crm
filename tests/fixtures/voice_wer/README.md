# Voice WER corpus

Drop real field-tech voice samples here to measure the transcription Word Error
Rate (WER) of the configured ASR against accented, in-the-field speech. Until
samples exist, `tests/test_voice_quality.py::test_wer_corpus_against_real_samples`
skips.

## Format

- `manifest.json` — a list of entries:
  ```json
  [
    {"key": "install-lagos-01", "audio": "install-lagos-01.webm", "reference": "installed the O N T serial H G eight five four six, downstream signal minus twenty one"},
    {"key": "repair-ibadan-02", "audio": "repair-ibadan-02.m4a",  "reference": "replaced the drop cable, about forty metres, customer signed off"}
  ]
  ```
- Audio files referenced by `audio`, in this directory.
- `reference` is the human-verified ground-truth transcript (words as spoken).

## Targets

Aim for **mean WER ≤ 0.30** on Nigerian-accented field speech before relying on
voice pre-fill without review. Above that, the confidence clamp
(`app/services/ai/voice_quality.py`) keeps `requires_review` true so techs confirm
every extracted field.
