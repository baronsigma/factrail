"""FACTRAIL Trade — import assessment vertical (V0.1).

Architecture:
  assess_import
      |
      +-- product resolver
      |
      +-- classification engine
      |
      +-- tariff/measures engine
      |
      +-- origin engine
      |
      +-- compliance engine
      |
      +-- landed cost engine
      |
      +-- risk/confidence engine
      |
      +-- evidence ledger

Single public MCP tool: assess_import.
"""

from __future__ import annotations

from .models import (
    AssessImportInput,
    ImportAssessment,
    Evidence,
)
from .service import assess_import

__all__ = ["assess_import", "AssessImportInput", "ImportAssessment", "Evidence"]
