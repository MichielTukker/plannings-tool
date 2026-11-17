"""Fixed automatic-break placement policy."""

from dataclasses import dataclass
from datetime import time
from typing import Literal

from .models import Defaults


@dataclass(frozen=True)
class AutomaticBreakRule:
    name: str
    kind: Literal["coffee", "lunch"]
    preferred: time
    shift_minutes: int

    def duration(self, defaults: Defaults) -> int:
        return defaults.coffee_break if self.kind == "coffee" else defaults.lunch


AUTOMATIC_BREAK_POLICY = (
    AutomaticBreakRule("Morning coffee", "coffee", time(10, 30), 30),
    AutomaticBreakRule("Lunch", "lunch", time(12, 15), 15),
    AutomaticBreakRule("Afternoon coffee", "coffee", time(15, 0), 30),
)
