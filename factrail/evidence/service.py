"""Registry-based deterministic verification router."""
from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from .company_fr import resolve_company_fr
from .models import EvidenceEnvelope
from .receipts import ReceiptRepository


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: str
    identifier: str
    fields: list[str] | None = Field(default=None, min_length=1)


Resolver = Callable[[str, list[str] | None], EvidenceEnvelope]
RESOLVERS: dict[str, Resolver] = {"company_fr": resolve_company_fr}


def verify(request: VerificationRequest, repository: ReceiptRepository | None = None,
           resolvers: dict[str, Resolver] | None = None) -> EvidenceEnvelope:
    resolver = (resolvers or RESOLVERS).get(request.subject_type)
    if resolver is None:
        raise ValueError(f"unsupported subject_type: {request.subject_type}")
    return (repository or ReceiptRepository()).save(resolver(request.identifier, request.fields))
