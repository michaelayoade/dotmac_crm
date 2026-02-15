from __future__ import annotations

from datetime import datetime


def build_performance_review_prompts(
    *,
    person_name: str,
    period_start: datetime,
    period_end: datetime,
    composite_score: float,
    domain_scores: dict,
    evidence_samples: list[str],
) -> tuple[str, str]:
    system = (
        "You are a performance coach. Return strict JSON with keys: "
        "summary, strengths, improvements, recommendations, callouts. "
        "Keep recommendations specific and actionable."
    )

    samples = "\n".join(f"- {sample}" for sample in evidence_samples) if evidence_samples else "- No samples provided"
    user = (
        f"Agent: {person_name}\n"
        f"Period: {period_start.isoformat()} to {period_end.isoformat()}\n"
        f"Composite score: {composite_score:.2f}\n"
        f"Domain scores: {domain_scores}\n"
        f"Evidence samples:\n{samples}\n"
        "Return JSON only."
    )
    return system, user
