"""
Provenance Guard - Stylometric Analysis Service
services/stylometric.py: Computes heuristic stylometric features to estimate
the likelihood that a piece of text was AI-generated.

Uses only the Python standard library — no third-party dependencies required.

Metrics
-------
1. Sentence Length Variance  — AI text tends to have suspiciously uniform
   sentence lengths, so *low* variance is a signal of AI authorship.
2. Type-Token Ratio (TTR)    — Extremely high vocabulary diversity can
    sometimes appear in AI-generated text, but this metric is weak on short
    passages and should only be interpreted alongside other signals.
3. Punctuation Density       — AI text tends to be punctuation-light relative
   to formal human prose; an unusually low or high density is informative.

Each metric is normalised to [0, 1] where 1.0 means "strongly AI-like".
The final score is the equal-weight average of the three normalised values.
"""

import re
import string
import statistics
from typing import Optional


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into [lo, hi]."""
    return max(lo, min(hi, value))


def _normalise_low_is_ai(value: float, low: float, high: float) -> float:
    """
    Return an AI-likelihood score where *low* values indicate AI authorship.

    Maps the range [low, high] linearly onto [1, 0]:
        value == low  → score 1.0  (strongly AI-like)
        value == high → score 0.0  (strongly human-like)
    """
    if high == low:
        return 0.5  # degenerate range — return neutral score
    score = 1.0 - (value - low) / (high - low)
    return _clamp(score)


def _normalise_high_is_ai(value: float, low: float, high: float) -> float:
    """
    Return an AI-likelihood score where *high* values indicate AI authorship.

    Maps the range [low, high] linearly onto [0, 1]:
        value == low  → score 0.0  (strongly human-like)
        value == high → score 1.0  (strongly AI-like)
    """
    if high == low:
        return 0.5
    score = (value - low) / (high - low)
    return _clamp(score)


# ---------------------------------------------------------------------------
# Individual metric computations
# ---------------------------------------------------------------------------

def _compute_sentence_length_variance(text: str) -> tuple[float, float]:
    """
    Split *text* into sentences, count words per sentence, and compute variance.

    Returns
    -------
    (raw_variance, ai_score)
        raw_variance : population variance of per-sentence word counts.
        ai_score     : normalised [0, 1] AI-likelihood contribution.
                       Low variance → higher AI score.
    """
    # Split on terminal punctuation; keep non-empty sentences
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]

    if len(sentences) < 2:
        # Not enough sentences to compute variance — return a neutral score
        return 0.0, 0.5

    word_counts = [len(s.split()) for s in sentences]

    # pstdev uses population standard deviation; variance = stdev²
    variance = statistics.pstdev(word_counts) ** 2

    # Calibration: human writing typically shows variance in the range [5, 60].
    # Values below ~5 are suspiciously uniform (AI-like).
    ai_score = _normalise_low_is_ai(variance, low=2.0, high=50.0)
    return variance, ai_score


def _compute_type_token_ratio(text: str) -> tuple[float, float]:
    """
    Compute the Type-Token Ratio (TTR): unique_words / total_words.

    Returns
    -------
    (ttr, ai_score)
        ttr      : float in (0, 1].
        ai_score : normalised [0, 1] AI-likelihood contribution.
                   High TTR → higher AI score.
    """
    # Lowercase and strip punctuation for fair token comparison
    translator = str.maketrans("", "", string.punctuation)
    words = text.lower().translate(translator).split()

    if not words:
        return 0.0, 0.5

    total_words  = len(words)
    unique_words = len(set(words))
    ttr = unique_words / total_words

    # Calibration: casual human text TTR ~ 0.40–0.65; AI text often ~0.65–0.85.
    ai_score = _normalise_high_is_ai(ttr, low=0.55, high=0.95)
    return ttr, ai_score


def _compute_punctuation_density(text: str) -> tuple[float, float]:
    """
    Compute punctuation density: punctuation_marks / total_words.

    Returns
    -------
    (density, ai_score)
        density  : float ≥ 0.
        ai_score : normalised [0, 1] AI-likelihood contribution.
                   Very low density → higher AI score (AI often under-punctuates).
    """
    words = text.split()
    if not words:
        return 0.0, 0.5

    punctuation_marks = sum(1 for ch in text if ch in string.punctuation)
    density = punctuation_marks / len(words)

    # Calibration: typical human prose ~ 0.15–0.40 marks/word.
    # AI text tends toward lower density (~0.05–0.15).
    ai_score = _normalise_low_is_ai(density, low=0.05, high=0.35)
    return density, ai_score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_stylometry(text: str) -> dict:
    """
    Analyse *text* using three stylometric features and return an overall
    AI-likelihood score together with the raw metric values.

    Args:
        text: The raw string to analyse. Should be at least a few sentences
              for meaningful results.

    Returns:
        {
            "score": float,          # 0.0 (human) – 1.0 (AI)
            "metrics": {
                "sentence_length_variance": float,
                "type_token_ratio":         float,
                "punctuation_density":      float,
            }
        }
    """
    if not text or not text.strip():
        # Return a neutral, clearly empty result for blank input
        return {
            "score": 0.5,
            "metrics": {
                "sentence_length_variance": 0.0,
                "type_token_ratio":         0.0,
                "punctuation_density":      0.0,
            },
        }

    # --- Compute each metric and its normalised AI-likelihood contribution ---
    sentence_variance, sentence_score   = _compute_sentence_length_variance(text)
    ttr,               vocabulary_score = _compute_type_token_ratio(text)
    punct_density,     punctuation_score = _compute_punctuation_density(text)

    # --- Equal-weight average of the three contributions ---
    score = (sentence_score + vocabulary_score + punctuation_score) / 3

    return {
        "score": round(score, 4),
        "metrics": {
            "sentence_length_variance": round(sentence_variance, 4),
            "type_token_ratio":         round(ttr, 4),
            "punctuation_density":      round(punct_density, 4),
        },
    }


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    SAMPLE_AI = (
        "The implementation of large language models necessitates a comprehensive "
        "understanding of transformer architectures and attention mechanisms. "
        "These systems leverage vast datasets to generate coherent and contextually "
        "appropriate responses. The underlying mathematics involves matrix "
        "multiplications and softmax normalisation across high-dimensional spaces. "
        "Researchers continue to optimise these models for efficiency and accuracy."
    )

    SAMPLE_HUMAN = (
        "Honestly, I was totally lost when I first tried to understand how these "
        "AI things work — like, where do you even begin?! A friend of mine (shoutout "
        "to Jamie) sat down with me for an hour and drew it all out on a napkin. "
        "Still didn't fully get it, but hey, at least now I can nod along at parties. "
        "Maybe one day it'll click. Or maybe not — and that's fine too, I guess."
    )

    for label, sample in (("AI-like text", SAMPLE_AI), ("Human-like text", SAMPLE_HUMAN)):
        print(f"\n{'─' * 60}")
        print(f"  {label}")
        print(f"{'─' * 60}")
        result = analyze_stylometry(sample)
        print(json.dumps(result, indent=2))
