from __future__ import annotations

import re

RISKY_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bin the style of\b",
        r"\bsounds? like\b",
        r"\bsing like\b",
        r"\bvoice of\b",
        r"\bmake it like\b",
        r"\bcopy\b.*\b(song|track|artist|band|album|lyrics?)\b",
        r"\bremix\b.*\b(song|track|artist|band|album|lyrics?)\b",
        r"\bimpersonat(e|ion)\b",
        r"\bwith lyrics from\b",
        r"\bcover of\b",
    ]
)


def assert_prompt_is_licensing_safe(prompt: str, *, field: str = "prompt") -> None:
    """Reject prompts that are likely to violate common AI-music licensing restrictions.

    This is intentionally conservative. It does not prove a prompt is legally safe, but it blocks
    the most common failure mode: asking the model to imitate a known artist, song, voice, label, or
    copyrighted lyric.
    """
    for pattern in RISKY_PROMPT_PATTERNS:
        if pattern.search(prompt):
            raise ValueError(
                f"Unsafe {field}: avoid artist/song/voice imitation, covers, remixes, lyrics, "
                "or prompts such as 'in the style of' / 'sounds like'. Use genre, mood, BPM, "
                "instruments, structure, and production adjectives instead."
            )
