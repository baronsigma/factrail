"""Pydantic models for Factrail responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, model_serializer, model_validator

NULL_REASONS = ("unavailable", "unknown", "not_applicable")


class Address(BaseModel):
    line_1: Optional[str] = None
    line_2: Optional[str] = None
    locality: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = "FR"


class SourceMeta(BaseModel):
    id: str
    name: str
    url: str
    retrieved_at: datetime


class FrenchCompany(BaseModel):
    siren: str
    siret: Optional[str] = None
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    status: Optional[str] = None
    diffusion_status: Optional[str] = None
    legal_form_code: Optional[str] = None
    legal_form_label: Optional[str] = None
    creation_date: Optional[str] = None
    cessation_date: Optional[str] = None
    naf_code: Optional[str] = None
    naf_code_nomenclature: Optional[str] = None
    naf_label: Optional[str] = None
    employee_band: Optional[str] = None
    employee_band_year: Optional[int] = None
    address: Optional[Address] = None
    sources: list[SourceMeta] = Field(default_factory=list)
    events: Optional[dict] = None
    checked_at: datetime

    null_reasons: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_required(self) -> "FrenchCompany":
        if not self.siren:
            raise ValueError("siren is required (would indicate schema drift)")
        return self

    @model_serializer(mode="wrap")
    def _serialize(self, default_handler: Any) -> dict:
        data = default_handler(self)
        for field_name, reason in self.null_reasons.items():
            data[f"{field_name}_reason"] = reason
        return data


def mark_null(fc: FrenchCompany, field_name: str, reason: str) -> None:
    if reason not in NULL_REASONS:
        raise ValueError(f"null reason must be one of {NULL_REASONS}, got {reason!r}")
    fc.null_reasons[field_name] = reason
