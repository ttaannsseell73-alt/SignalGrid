from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(slots=True)
class SignalDiagnosticCounter:
    reasons: Counter[str] = field(default_factory=Counter)

    def observe(self, reason: str) -> None:
        self.reasons[str(reason)] += 1

    def snapshot(self) -> dict[str, int]:
        return dict(self.reasons.most_common())
