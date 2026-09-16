# SPDX-License-Identifier: Apache-2.0
"""Model tier abstraction (RCA-7, WF-14 §B): the only AI boundary in Wakey.

``ModelClient`` is what agents speak to; concrete tiers bind providers.
``FakeModel`` is deterministic and key-free — tests, demos, and the
silent tier of graceful degradation (NFR-4) all run through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelReply:
    """Structured agent output; text is the model's answer, data its payload."""

    text: str
    data: dict[str, str]


class ModelClient(Protocol):
    def complete(self, system: str, prompt: str) -> ModelReply:
        """One-shot completion; implementations must never raise on bad input —
        they return an error reply and the caller degrades."""
