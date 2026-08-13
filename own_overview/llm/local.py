"""Local, zero-cloud LLM fallback — the `PROVIDER=local` "echo" model.

`EchoLLM` is a **deterministic, extractive stand-in for a real LLM**. It does no
generation and needs no weights or network: it reads the numbered context blocks
out of the grounding prompt, stitches the first sentence of each, and re-attaches
that block's `[n]` citation marker. The result is a grounded-looking, *cited*
answer, so the local pipeline is end-to-end runnable (retrieve -> "ground" ->
cite) with zero cloud.

This is a scaffold, not a language model — it cannot paraphrase, reason, or
synthesize. Swap in `BedrockLLM` (or any real `contracts.LLM`) for real answers.
The grounding node builds the prompt with one numbered block per retrieved
passage; the parser below is tolerant of the common marker styles (`[1]`, `1.`,
`(1)`) so it stays robust to small prompt-format changes.
"""

from __future__ import annotations

import re

from ..config import Settings

# A numbered context block: a leading marker ([1] / 1. / (1)) at a line start,
# then its text, running up to the next marker (or end of string).
_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*[\[(]?(\d+)[\])\.]\s+(.*?)(?=(?:\n\s*[\[(]?\d+[\])\.]\s+)|\Z)",
    re.S,
)
# Split into sentences on ., ! or ? followed by whitespace.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_ABSTAIN = (
    "I could not find grounded context to answer from. "
    "(Local echo model — no numbered passages were present in the prompt.)"
)


class EchoLLM:
    """Extractive fallback LLM. Implements the `contracts.LLM` Protocol."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def complete(self, system: str, prompt: str) -> str:  # noqa: ARG002 - system unused
        """Return a stitched, cited pseudo-answer built from the prompt's blocks.

        `system` is accepted for Protocol parity but ignored — there is no model
        to steer. Only `prompt` (which carries the numbered passages) is read.
        """
        sentences: list[str] = []
        for marker, text in _BLOCK_RE.findall(prompt):
            first = self._first_sentence(text)
            if first:
                sentences.append(f"{first} [{marker}]")
        if not sentences:
            return _ABSTAIN
        return " ".join(sentences)

    @staticmethod
    def _first_sentence(text: str) -> str:
        text = " ".join(text.split())  # collapse whitespace / newlines
        if not text:
            return ""
        return _SENTENCE_RE.split(text, maxsplit=1)[0].strip()
