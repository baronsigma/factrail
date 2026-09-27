"""Evidence ledger — provenance tracking for FACTRAIL Trade V0.1.

Every consequential external fact should be traceable.  Internally we use
an evidence model and a ledger that accumulates Evidence records across the
pipeline.  The final response deduplicates sources but preserves provenance.

Evidence status values:
  - verified:    from an authoritative primary source
  - derived:     computed deterministically from verified inputs
  - estimated:   reasoned estimate with explicit assumptions
  - inferred:    model / rule-of-thumb guess
  - unavailable: authoritative source unreachable / not integrated
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import Evidence, ProvenanceStatus


class EvidenceLedger:
    """Accumulates Evidence records across the assessment pipeline.

    Designed to be passed mutably through the pipeline so each stage can
    append its own evidence.  Deduplication happens at the top level when
    building the final response.
    """

    def __init__(self) -> None:
        self._records: list[Evidence] = []

    def append(self, evidence: Evidence) -> None:
        """Add an Evidence record to the ledger."""
        if evidence is None:
            return
        self._records.append(evidence)

    def extend(self, records: list[Evidence]) -> None:
        """Add multiple Evidence records."""
        for r in records:
            self.append(r)

    def snapshot(self) -> list[Evidence]:
        """Return a shallow copy of all records."""
        return list(self._records)

    def deduplicate(self) -> list[Evidence]:
        """Return deduplicated records by content hash.

        Two records are considered the same if their value, status, authority,
        source, url, and confidence are identical.
        """
        seen: set[tuple[Any, str, str, str, str, float]] = set()
        result: list[Evidence] = []
        for e in self._records:
            key = (
                e.value,
                e.status.value if e.status else "",
                e.authority,
                e.source,
                e.url,
                e.confidence,
            )
            if key not in seen:
                seen.add(key)
                result.append(e)
        return result

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return bool(self._records)
