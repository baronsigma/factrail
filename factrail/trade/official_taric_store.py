"""Offline ingestion and query support for official Commission TARIC snapshots.

This module deliberately separates acquisition from ingestion. It accepts official
Commission Excel extracts already supplied as files, directories, or ZIP archives;
queries never require a network connection.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

PUBLISHER = "European Commission DG TAXUD"
SOURCE = "EU_TARIC"
INGESTION_VERSION = "1.0"
PARSER_VERSION = "2.4.0B"

# These are the semantic extract table names in the Commission extraction guide.
# Full snapshots require all tables needed to determine applicability. A caller may
# opt into a partial snapshot, but results then identify their incomplete scope.
FULL_REQUIRED_TABLES = frozenset({
    "Goods_Nomenclature", "Measures", "Geographical_Areas",
    "Declarable_Codes", "Geographical_Area_Membership", "Measure_Exclusions", "Measure_Conditions",
    "Measure_Footnotes", "Legal_Bases", "Additional_Codes", "Certificates",
    "Footnotes", "Measure_Types", "Duty_Expressions", "Measurement_Units", "Monetary_Units",
})
CORE_REQUIRED_TABLES = frozenset({"Goods_Nomenclature", "Measures"})
NONEMPTY_REQUIRED_TABLES = frozenset({"Goods_Nomenclature", "Measures"})

_TABLE_ALIASES = {
    "goodsnomenclature": "Goods_Nomenclature", "nomenclature": "Goods_Nomenclature", "goodsnomenclatures": "Goods_Nomenclature",
    "goodsnomenclatureitem": "Goods_Nomenclature",
    "declarablecodes": "Declarable_Codes",
    "measures": "Measures", "dutiesimport0199": "Measures", "dutiesimport": "Measures",
    "importduties": "Measures", "taricmeasures": "Measures",
    # Current guide names are human-readable file titles (e.g. "Geographical
    # area composition"), while the old importer used invented normalized
    # names. Keep the source's actual extraction vocabulary here.
    "geographicalareas": "Geographical_Areas", "geoareas": "Geographical_Areas",
    "geographicalareadescriptions": "Geographical_Areas", "geographicalareasdescriptions": "Geographical_Areas",
    "geographicalareamembership": "Geographical_Area_Membership",
    "geographicalmembership": "Geographical_Area_Membership", "geographicareaassociation": "Geographical_Area_Membership",
    "dutiesimportexport0199": "Measures", "dutiesimportexport": "Measures",
    "dutiesimportexport01-99": "Measures",
    "measureexclusions": "Measure_Exclusions", "measureconditions": "Measure_Conditions",
    "measurefootnotes": "Measure_Footnotes", "legalbases": "Legal_Bases",
    "legalbasesandregulations": "Legal_Bases",
    "additionalcodes": "Additional_Codes", "additionalcodesdescriptions": "Additional_Codes",
    "certificates": "Certificates", "box44codesofthesad": "Certificates",
    "footnotes": "Footnotes", "footnotesdescriptions": "Footnotes",
    "measurementunits": "Measurement_Units", "measurementunitsandmeasurementunitqualifiercode": "Measurement_Units",
    "monetaryunits": "Monetary_Units", "monetaryunit": "Monetary_Units",
    "measuretype": "Measure_Types", "measuretypes": "Measure_Types",
    "measureconditions": "Measure_Conditions", "measurefootnotes": "Measure_Footnotes",
    "nomenclaturefootnotes": "Nomenclature_Footnotes",
    "geographicalareacomposition": "Geographical_Area_Membership",
    "measurementunitsandmeasurementunitqualifiers": "Measurement_Units",
    "dutyexpression": "Duty_Expressions", "dutyexpressions": "Duty_Expressions",
    "addcodes": "Additional_Codes",
}

_FIELD_ALIASES = {
    "goods_code": {"goodscode", "goodsnomenclaturecode", "goodsnomenclatureitemid", "nomenclaturecode", "commoditycode", "productcode"},
    "product_line_suffix": {"productlinesuffix", "suffix"},
    "hierarchy_level": {"hierarchylevel", "hierarchicalposition", "level", "indentationlevel"},
    "is_declarable": {"isleaf", "isdeclarable", "declarable", "leaf", "declarablecodeinacustomsdeclaration"},
    "validity_start": {"validitystart", "validitystartdate", "startdate", "validfrom", "start"},
    "validity_end": {"validityend", "validityenddate", "enddate", "validto", "end"},
    "measure_type": {"measuretype", "measuretypeid", "measuretypecode", "measuretypecodes", "measurecode", "type"},
    "measure_category": {"measuretypedescription", "measuretypelongdescription", "shortdescription"},
    "geographical_area": {"geographicalarea", "geographicalareaid", "geographicalareacode", "origin", "originarea", "origincode", "geocode"},
    "duty_expression": {"dutyexpression", "duty", "duties", "rate", "tariff"},
    "additional_code": {"additionalcode", "additionalcodeid", "addcode"},
    "quota_order_number": {"quotaordernumber", "quotaordernumberid", "quotaorder", "ordernumber"},
    "regulation": {"regulation", "regulationid", "legalact", "legalbase", "legalbasis", "legalreference", "eulawreference"},
    "footnote_code": {"footnotecode", "footnoteid"},
    "condition_code": {"conditioncode", "conditiontype", "conditiontypecode"},
    "condition_sequence": {"conditionsequencenumber", "sequencenumber"},
    "certificate_code": {"certificatecode", "certificate"},
    "country_code": {"countrycode", "countrycodeabbreviation", "membercountrycode", "member", "country"},
    "member_code": {"countryterritorycode", "memberidentifiercode", "membercode"},
    "parent_area": {"parentarea", "parentgeographicalarea", "countrygroupcode", "countrygroup", "geographicalareaid", "groupcode", "areacode", "countrygroupidentifier"},
    "area_id": {"areaid", "geographicalareaid", "geographicalareacode", "geocode", "measurearea", "countrygroupcode", "code"},
    "excluded_area": {"excludedarea", "excludedgeographicalarea", "excludedcountry", "excludedcode", "excludedcountrycode", "countryterritoryexcluded"},
    "conditions": {"conditions", "condition"},
    "description": {"description", "goodsdescription", "text", "footnotetext"},
    "action": {"action", "actioncode"},
    "duty_amount": {"dutyamount", "amount"},
    "measurement_unit": {"measurementunit", "measurementunitcode", "measurementunitidentifier", "unit"},
    "measurement_qualifier": {"measurementunitqualifier", "measurementqualifier", "unitqualifier"},
    "monetary_unit": {"monetaryunit", "currencycode", "currency"},
    "operation": {"publish", "operation"},
    "sequence_number": {"sequencenumber", "sequence"},
}

# Header semantics are centralized here. Canonical headings are based on the
# Commission extraction guide; only confirmed spelling variants belong here.
# Strict mode accepts the canonical normalized heading. Compatible mode also
# accepts these documented aliases, provided every mapping is one-to-one.
CANONICAL_HEADERS = {
    "Goods_Nomenclature": {"goods_code", "validity_start", "validity_end", "hierarchy_level", "description"},
    "Declarable_Codes": {"goods_code", "is_declarable", "validity_start", "validity_end"},
    "Measures": {"goods_code", "additional_code", "quota_order_number", "validity_start", "validity_end", "geographical_area", "measure_type", "regulation", "duty_expression"},
    "Geographical_Area_Membership": {"parent_area", "country_code", "validity_start", "validity_end"},
    "Measure_Exclusions": {"goods_code", "measure_type", "geographical_area", "excluded_area"},
    "Measure_Conditions": {"goods_code", "measure_type", "condition_code"},
    "Measure_Footnotes": {"goods_code", "measure_type", "footnote_code"},
    "Legal_Bases": {"regulation"},
    "Additional_Codes": {"additional_code"},
}
CRITICAL_HEADERS = {
    "Goods_Nomenclature": {"goods_code", "validity_start", "validity_end", "hierarchy_level"},
    "Declarable_Codes": {"goods_code", "is_declarable", "validity_start", "validity_end"},
    "Measures": {"goods_code", "validity_start", "validity_end", "geographical_area", "measure_type", "duty_expression"},
    "Geographical_Area_Membership": {"parent_area", "country_code"},
}
OPTIONAL_TABLES = frozenset(FULL_REQUIRED_TABLES - {"Goods_Nomenclature", "Measures"})

SUPPORTED_DUTY_RE = re.compile(r"^\s*(?:\d+(?:[.,]\d+)?\s*%|free|0\s*(?:%|$))\s*$", re.I)
MEASUREMENT_STRUCTURE_FIELDS = ("duty_amount", "measurement_unit", "measurement_qualifier", "monetary_unit")


class TaricSnapshotError(ValueError):
    """Snapshot is malformed or structurally incomplete."""


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _table_name(value: str) -> str | None:
    key = _norm(Path(value).stem)
    exact = _TABLE_ALIASES.get(key)
    if exact:
        return exact
    # Commission Daily Update workbooks append YYYYMMDD_HHMM to the five
    # table names. Accept only a numeric suffix after a known table token.
    # Daily filenames use YYYYMMDD_HHMM, while some current document examples
    # spell the goods table as plural.
    if key.startswith("goodsnomenclatures") and re.fullmatch(r"goodsnomenclatures\d{8}\d{4}", key):
        return "Goods_Nomenclature"
    for alias, canonical in _TABLE_ALIASES.items():
        suffix = key[len(alias):] if key.startswith(alias) else ""
        if suffix and suffix.isdigit():
            return canonical
    return None


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _canonical_row(headers: list[Any], values: Iterable[Any], table: str) -> dict[str, Any]:
    values = list(values)
    row: dict[str, Any] = {}
    for header, value in zip(headers, values):
        name = _cell_text(header)
        text = _cell_text(value)
        if not name or text is None:
            continue
        row[name] = text
        field, _ = _field_for_header(name, table, "compatible")
        if field:
            row[field] = text
        key = _norm(name)
        if table == "Geographical_Area_Membership" and key in {"countrygroupcode", "countrygroupidentifier"}:
            row["parent_area"] = text
        if key in {"countrybecamemember", "membershipstartdate", "countrybecamememberdate"}:
            row["validity_start"] = text
        if key in {"countryceasetobemember", "membershipenddate", "countryceasetobememberdate"}:
            row["validity_end"] = text
    if table == "Geographical_Areas" and "geographical_area" in row:
        row["area_id"] = row["geographical_area"]
    if table == "Geographical_Area_Membership":
        row["area_id"] = row.get("parent_area") or row.get("area_id")
    if "goods_code" in row:
        code = re.sub(r"\D", "", row["goods_code"])
        # The Commission guide defines a code as ten digits plus a separate
        # suffix. Measures carry ten digits; nomenclature/declarable rows use
        # the twelve-digit key so several technical lines remain distinct.
        suffix = row.get("product_line_suffix")
        if not suffix and len(code) == 12:
            suffix, code = code[10:12], code[:10]
        if table in {"Goods_Nomenclature", "Declarable_Codes"}:
            if suffix:
                row["product_line_suffix"] = suffix
        row["goods_code"] = code
        if table == "Goods_Nomenclature" and "hierarchy_level" not in row and code.isdigit() and len(code) <= 10:
            padded = code.ljust(10, "0")
            row["hierarchy_level"] = str(next((level for level in (10, 8, 6, 4, 2)
                                               if padded[level - 2:level] != "00"), 2))
    # The Commission's extract guide also identifies the first column in
    # nomenclature/measures as the goods code. Some historic extracts use a
    # positional heading; retain that explicit positional fallback.
    if "goods_code" not in row and headers and table in {"Goods_Nomenclature", "Declarable_Codes", "Measures"}:
        first = _cell_text(values[0])
        if first and first.isdigit():
            row["goods_code"] = first[:10] if table in {"Goods_Nomenclature", "Declarable_Codes"} and len(first) == 12 else first
            if table in {"Goods_Nomenclature", "Declarable_Codes"} and len(first) == 12:
                row["product_line_suffix"] = first[10:12]
    for code_key in ("goods_code", "product_line_suffix", "additional_code", "quota_order_number", "measure_type", "geographical_area", "country_code", "parent_area", "area_id", "excluded_area", "regulation", "footnote_code", "condition_code", "certificate_code"):
        if code_key in row:
            row[code_key] = str(row[code_key]).strip()
    return row


def _field_for_header(header: Any, table: str, mode: str) -> tuple[str | None, str | None]:
    """Return a canonical semantic field and optional compatibility note."""
    raw = _cell_text(header)
    key = _norm(raw)
    candidates = [field for field, aliases in _FIELD_ALIASES.items() if key == _norm(field) or key in aliases]
    if table == "Geographical_Area_Membership" and key in {"countrygroupcode", "countrygroupidentifier"}:
        candidates = ["parent_area"]
    # Exact official vocabulary has a few table-specific meanings.
    if key == "origin" and table == "Measures":
        candidates = ["geographical_area"]
    if key == "label" and table == "Duty_Expressions":
        candidates = ["duty_expression_label"]
    if key == "code" and table in {"Geographical_Areas", "Measure_Types", "Duty_Expressions"}:
        candidates = ["area_id" if table == "Geographical_Areas" else "measure_type" if table == "Measure_Types" else "duty_expression"]
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) > 1:
        raise TaricSnapshotError(f"Ambiguous header {raw!r} in {table}: {', '.join(candidates)}")
    if not candidates:
        return None, None
    field = candidates[0]
    if mode == "strict" and raw != field:
        # Strict mode uses semantic field keys as canonical headers, avoiding
        # silent interpretation of source-specific labels.
        raise TaricSnapshotError(f"Strict mode rejects non-canonical header {raw!r} in {table}; canonical header is {field!r}")
    note = None if raw == field else f"mapped header {raw!r} to {field!r} in {table}"
    return field, note


def _discover_package(source: str | Path, mode: str = "compatible", partial: bool = False) -> dict[str, Any]:
    """Inspect files/workbooks without activation and produce an acceptance report."""
    if mode not in {"strict", "compatible"}:
        raise ValueError("mode must be 'strict' or 'compatible'")
    source_files, package_format = _source_files(source)
    tables: dict[str, list[dict[str, Any]]] = {}
    discovered_files: list[dict[str, Any]] = []
    unknown_tables: list[dict[str, str]] = []
    parse_errors: list[str] = []
    adaptations: list[str] = []
    malformed_rows: list[dict[str, Any]] = []
    headers_by_table: dict[str, list[list[str]]] = {}
    normalized_headers: dict[str, list[list[str]]] = {}
    duplicate_issues: list[dict[str, Any]] = []
    duty_patterns: dict[str, dict[str, Any]] = {}
    date_candidates: list[dict[str, Any]] = []
    recognized_columns: dict[str, set[str]] = {}
    unknown_columns: dict[str, set[str]] = {}
    for name, content in source_files:
        file_info = {"name": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "sheets": []}
        try:
            book = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            parse_errors.append(f"Malformed Excel workbook {name}: {type(exc).__name__}")
            discovered_files.append(file_info)
            continue
        try:
            for sheet in book.worksheets:
                table = _table_name(sheet.title) or _table_name(name)
                info = {"name": sheet.title, "recognized_table": table}
                file_info["sheets"].append(info)
                if table is None:
                    unknown_tables.append({"file": name, "sheet": sheet.title})
                    continue
                rows = sheet.iter_rows(values_only=True)
                raw_headers = list(next(rows, ()))
                header_names = [_cell_text(x) or "" for x in raw_headers]
                fields: list[str | None] = []
                for header in raw_headers:
                    try:
                        field, adaptation = _field_for_header(header, table, mode)
                        fields.append(field)
                        if adaptation:
                            adaptations.append(adaptation)
                    except TaricSnapshotError as exc:
                        parse_errors.append(str(exc))
                        fields.append(None)
                info["headers"] = header_names
                info["normalized_headers"] = [x for x in fields if x]
                headers_by_table.setdefault(table, []).append(header_names)
                normalized_headers.setdefault(table, []).append([x or "" for x in fields])
                recognized_columns.setdefault(table, set()).update(x for x in fields if x)
                unknown_columns.setdefault(table, set()).update(h for h, f in zip(header_names, fields) if h and f is None)
                count = 0
                for row_number, values in enumerate(rows, start=2):
                    vals = list(values)
                    if not any(v is not None for v in vals):
                        continue
                    count += 1
                    try:
                        canonical = _canonical_row(raw_headers, vals, table)
                    except TaricSnapshotError as exc:
                        parse_errors.append(str(exc))
                        malformed_rows.append({"table": table, "file": name, "sheet": sheet.title,
                                               "row": row_number, "issue": "ambiguous column semantics"})
                        continue
                    canonical["_source_file"] = name
                    canonical["_source_sheet"] = sheet.title
                    canonical["_source_row"] = row_number
                    if table == "Measures":
                        expr = canonical.get("duty_expression")
                        structure = {key: canonical.get(key) for key in MEASUREMENT_STRUCTURE_FIELDS if canonical.get(key) is not None}
                        pattern = json.dumps({"expression": expr, "measurement_structure": structure}, sort_keys=True)
                        category = "supported" if expr and SUPPORTED_DUTY_RE.fullmatch(expr) else "preserved_but_not_calculable" if expr or structure else "unsupported"
                        duty_patterns[pattern] = {"pattern": json.loads(pattern), "classification": category}
                        if not re.fullmatch(r"\d{10}", str(canonical.get("goods_code") or "")):
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "measure goods_code must contain ten digits"})
                        for date_field in ("validity_start", "validity_end"):
                            if canonical.get(date_field):
                                date_candidates.append({"table": table, "field": date_field, "value": canonical[date_field], "file": name, "row": row_number})
                    if table in {"Goods_Nomenclature", "Declarable_Codes"}:
                        code = str(canonical.get("goods_code") or "")
                        suffix = str(canonical.get("product_line_suffix") or "")
                        if not code.isdigit() or len(code) != 10:
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "goods code must normalize to ten digits"})
                        if suffix and (len(suffix) != 2 or not suffix.isdigit()):
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "product-line suffix must be two digits"})
                        if not suffix:
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "product-line suffix is missing"})
                        if table == "Declarable_Codes" and canonical.get("is_declarable") not in {"0", "1"}:
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "IS_LEAF must be 0 or 1"})
                        if table == "Goods_Nomenclature" and canonical.get("hierarchy_level") not in {"2", "4", "6", "8", "10"}:
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "hierarchy level must be 2, 4, 6, 8, or 10"})
                    for field in ("validity_start", "validity_end"):
                        value = _date_text(canonical.get(field))
                        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                            malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": f"invalid {field}: {value}"})
                    start, end = _date_text(canonical.get("validity_start")), _date_text(canonical.get("validity_end"))
                    if start and end and start > end:
                        malformed_rows.append({"table": table, "file": name, "sheet": sheet.title, "row": row_number, "issue": "validity start is after validity end"})
                    tables.setdefault(table, []).append(canonical)
                info["row_count"] = count
        finally:
            book.close()
        discovered_files.append(file_info)
    present = set(tables)
    missing_required = sorted(CORE_REQUIRED_TABLES - present)
    missing_optional = sorted(OPTIONAL_TABLES - present)
    missing_critical_columns = {table: sorted(fields - recognized_columns.get(table, set()))
                                for table, fields in CRITICAL_HEADERS.items()
                                if fields - recognized_columns.get(table, set()) and not (partial and table not in present)}
    # Duplicate identifiers are reported by natural keys when available.
    key_fields = {"Goods_Nomenclature": ("goods_code", "product_line_suffix", "validity_start"),
                  "Declarable_Codes": ("goods_code", "product_line_suffix", "validity_start"),
                  "Measures": ("goods_code", "additional_code", "quota_order_number", "validity_start", "geographical_area", "measure_type")}
    for table, keys in key_fields.items():
        seen: set[tuple[Any, ...]] = set()
        for row in tables.get(table, []):
            key = tuple(row.get(k) for k in keys)
            identity_present = bool(row.get("goods_code")) and bool(row.get("validity_start"))
            if table == "Measures":
                identity_present = identity_present and bool(row.get("measure_type")) and bool(row.get("geographical_area"))
            if identity_present and key in seen:
                duplicate_issues.append({"table": table, "key_fields": list(keys), "key": list(key)})
            seen.add(key)
    errors = list(parse_errors)
    if missing_required:
        errors.append("missing required tables: " + ", ".join(missing_required))
    if missing_critical_columns:
        errors.append("missing critical columns: " + json.dumps(missing_critical_columns, sort_keys=True))
    accepted = not errors and not malformed_rows and not duplicate_issues and all(
        len(_date_text(row.get("validity_start")) or "") == 10 and len(_date_text(row.get("validity_end")) or "") == 10
        for rows in tables.values() for row in rows
        if row.get("validity_start") and row.get("validity_end")
    )
    return {
        "source": SOURCE, "publisher": PUBLISHER, "mode": mode, "package_format": package_format,
        "package_name": Path(source).name,
        "files": discovered_files, "recognized_tables": {t: len(rows) for t, rows in sorted(tables.items())},
        "unknown_tables": unknown_tables,
        "required_tables": {"present": sorted(CORE_REQUIRED_TABLES & present), "missing": missing_required,
                            "other_required_missing": sorted(FULL_REQUIRED_TABLES - present)},
        "optional_tables": {"present": sorted(OPTIONAL_TABLES & present), "missing": missing_optional},
        "headers": {t: headers_by_table.get(t, []) for t in sorted(set(headers_by_table) | set(normalized_headers))},
        "normalized_headers": normalized_headers,
        "recognized_columns": {t: sorted(v) for t, v in sorted(recognized_columns.items())},
        "unknown_columns": {t: sorted(v) for t, v in sorted(unknown_columns.items())},
        "missing_critical_columns": missing_critical_columns,
        "adaptations": sorted(set(adaptations)), "row_counts": {t: len(v) for t, v in sorted(tables.items())},
        "date_reference_candidates": date_candidates[:100], "duplicate_key_issues": duplicate_issues,
        "malformed_rows": malformed_rows, "parse_errors": errors,
        "duty_expression_patterns": sorted(duty_patterns.values(), key=lambda x: json.dumps(x["pattern"], sort_keys=True)),
        "field_candidates": {
            "nomenclature": ["goods_code", "hierarchy_level", "description", "validity_start", "validity_end"],
            "product_line_suffix": sorted({str(r.get("product_line_suffix")) for r in tables.get("Goods_Nomenclature", []) if r.get("product_line_suffix")}),
            "leaf_declarability": sorted({str(r.get("is_declarable")) for r in tables.get("Declarable_Codes", []) if r.get("is_declarable")}),
            "geographic_references": sorted({str(r.get("geographical_area") or r.get("parent_area") or r.get("area_id")) for r in tables.get("Measures", []) if r.get("geographical_area") or r.get("parent_area") or r.get("area_id")})[:100],
            "legal_bases": sorted({str(r.get("regulation")) for r in tables.get("Measures", []) if r.get("regulation")})[:100],
            "additional_codes": sorted({str(r.get("additional_code")) for r in tables.get("Measures", []) if r.get("additional_code")})[:100],
            "conditions_fields": sorted(recognized_columns.get("Measure_Conditions", set())),
            "footnotes_fields": sorted(recognized_columns.get("Measure_Footnotes", set())),
        },
        "snapshot_sha256": _package_sha256(source_files, source),
        "accepted": accepted,
        "supported_scope": ["nomenclature", "raw measures"] if missing_optional else ["nomenclature", "geography", "measures", "conditions", "references"],
        "warnings": [f"unknown columns in {table}: {', '.join(cols)}" for table, cols in sorted(unknown_columns.items()) if cols]
                    + [f"unknown table in {item['file']}: {item['sheet']}" for item in unknown_tables]
                    + [f"optional table missing: {name}" for name in missing_optional],
        "statistics": {"files": len(source_files), "rows": sum(map(len, tables.values())), "tables": len(tables),
                       "unsupported_expression_patterns": sum(x["classification"] == "unsupported" for x in duty_patterns.values()),
                       "preserved_not_calculable_patterns": sum(x["classification"] == "preserved_but_not_calculable" for x in duty_patterns.values())},
    }


def _package_sha256(source_files: list[tuple[str, bytes]], source: str | Path | None = None) -> str:
    if source is not None:
        path = Path(source)
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    payload = b"".join(name.encode() + b"\0" + bytes.fromhex(hashlib.sha256(content).hexdigest())
                       for name, content in sorted(source_files, key=lambda item: item[0]))
    return hashlib.sha256(payload).hexdigest()


def _archive_source(source: str | Path, store_root: Path, snapshot_hash: str) -> list[dict[str, str]]:
    """Retain the original operator-supplied package/files for audit."""
    source_path = Path(source)
    archive_root = store_root / "source_archive" / snapshot_hash
    archive_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    if source_path.is_dir():
        candidates = sorted(p for p in source_path.rglob("*") if p.is_file() and p.suffix.lower() in {".xlsx", ".xlsm"})
        for path in candidates:
            relative = path.relative_to(source_path)
            destination = archive_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.resolve() != destination.resolve():
                shutil.copyfile(path, destination)
            entries.append({"original_name": relative.as_posix(), "archived_path": destination.relative_to(store_root).as_posix(),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    else:
        destination = archive_root / source_path.name
        if source_path.resolve() != destination.resolve():
            shutil.copyfile(source_path, destination)
        entries.append({"original_name": source_path.name, "archived_path": destination.relative_to(store_root).as_posix(),
                        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()})
    return entries


def _source_files(source: str | Path) -> tuple[list[tuple[str, bytes]], str]:
    path = Path(source)
    if path.is_dir():
        files = [(p.relative_to(path).as_posix(), p.read_bytes()) for p in sorted(path.rglob("*")) if p.is_file() and p.suffix.lower() == ".xlsx"]
        if not files:
            raise TaricSnapshotError("No .xlsx workbooks found in snapshot directory")
        return files, "directory"
    if not path.is_file():
        raise TaricSnapshotError(f"Snapshot path does not exist: {path}")
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return [(path.name, path.read_bytes())], path.suffix.lower().lstrip(".")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            files = [(n, archive.read(n)) for n in sorted(archive.namelist()) if n.lower().endswith((".xlsx", ".xlsm")) and not n.startswith("__MACOSX/")]
        if not files:
            raise TaricSnapshotError("ZIP contains no .xlsx workbooks")
        return files, "zip"
    raise TaricSnapshotError("Expected a .xlsx file, directory, or ZIP archive")


def _read_tables(files: list[tuple[str, bytes]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    file_manifest: list[dict[str, Any]] = []
    for name, content in files:
        digest = hashlib.sha256(content).hexdigest()
        file_manifest.append({"name": name, "sha256": digest, "bytes": len(content)})
        try:
            book = load_workbook(filename=__import__("io").BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            raise TaricSnapshotError(f"Malformed Excel workbook: {name}") from exc
        recognized = False
        try:
            for sheet in book.worksheets:
                table = _table_name(sheet.title) or _table_name(name)
                if table is None:
                    continue
                rows = sheet.iter_rows(values_only=True)
                headers = list(next(rows, ()))
                if not headers or all(v is None for v in headers):
                    raise TaricSnapshotError(f"Empty table {sheet.title!r} in {name}")
                count = 0
                for values in rows:
                    vals = list(values)
                    if not any(v is not None for v in vals):
                        continue
                    row = _canonical_row(headers, vals, table)
                    # Keep each original extracted column as well as normalized
                    # query columns so no source detail is thrown away.
                    row["_source_file"] = name
                    row["_source_sheet"] = sheet.title
                    tables.setdefault(table, []).append(row)
                    count += 1
                recognized = True
        finally:
            book.close()
        if not recognized:
            raise TaricSnapshotError(f"No recognized Commission TARIC tables in {name}")
    for required in NONEMPTY_REQUIRED_TABLES:
        if not tables.get(required):
            raise TaricSnapshotError(f"Snapshot has no non-empty {required} table")
    return tables, file_manifest


def _date_text(value: str | None) -> str | None:
    if not value:
        return None
    text = value[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, sort_keys=True, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class OfficialTaricStore:
    """Content-addressed, local SQLite TARIC snapshot store."""

    def __init__(self, store_dir: str | Path | None = None) -> None:
        self.root = Path(store_dir or os.environ.get("FACTRAIL_TARIC_SNAPSHOT_DIR", Path.home() / ".factrail" / "taric_snapshots"))
        self.root.mkdir(parents=True, exist_ok=True)

    def ingest(self, source: str | Path, *, reference_date: str, partial: bool = False,
               retrieved_at: datetime | None = None, mode: str = "compatible",
               acceptance_report: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = self._ingest(source, reference_date=reference_date, partial=partial, retrieved_at=retrieved_at,
                                  mode=mode, acceptance_report=acceptance_report)
            (self.root / "refresh-failure.json").unlink(missing_ok=True)
            return result
        except Exception as exc:
            self.record_refresh_failure("snapshot_validation_failed", error_code=type(exc).__name__)
            _atomic_json(self.root / "validation-failed.json", {
                "state": "validation_failed", "failed_at": datetime.now(timezone.utc).isoformat(),
                "error_code": type(exc).__name__, "detail": str(exc),
            })
            raise

    def _ingest(self, source: str | Path, *, reference_date: str, partial: bool = False,
               retrieved_at: datetime | None = None, mode: str = "compatible",
               acceptance_report: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            ref_date = date.fromisoformat(reference_date).isoformat()
        except ValueError as exc:
            raise TaricSnapshotError("reference_date must be YYYY-MM-DD") from exc
        source_files, package_format = _source_files(source)
        inspected_report = self.doctor(source, mode=mode, partial=partial, persist=True)
        if acceptance_report is not None and (
            acceptance_report.get("snapshot_sha256") != inspected_report.get("snapshot_sha256")
            or acceptance_report.get("accepted") is not inspected_report.get("accepted")
        ):
            raise TaricSnapshotError("Supplied acceptance report rejected or does not match fresh package validation")
        report = inspected_report
        acceptance_dir = self.root / "acceptance"
        acceptance_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(acceptance_dir / f"{report.get('snapshot_sha256', 'unknown')}.json", report)
        if not report.get("accepted"):
            reasons = report.get("parse_errors", []) + [str(r.get("issue")) for r in report.get("malformed_rows", [])]
            raise TaricSnapshotError("Acceptance report rejected this snapshot: " + "; ".join(reasons[:8]))
        if report.get("snapshot_sha256") != _package_sha256(source_files, source):
            raise TaricSnapshotError("Acceptance report hash does not match package")
        tables, file_manifest = _read_tables(source_files)
        missing = sorted(FULL_REQUIRED_TABLES - tables.keys())
        if missing and not partial:
            raise TaricSnapshotError("Snapshot is missing required tables: " + ", ".join(missing) + "; pass partial=True only when the reduced scope is intentional")
        if not CORE_REQUIRED_TABLES <= tables.keys():
            raise TaricSnapshotError("Snapshot requires both Goods_Nomenclature and Measures")
        # An ostensibly full snapshot without geography/applicability tables
        # would make origin applicability unsafe.
        applicability_tables = {"Geographical_Areas", "Geographical_Area_Membership", "Measure_Exclusions",
                                "Measure_Conditions", "Measure_Footnotes", "Legal_Bases", "Additional_Codes",
                                "Certificates", "Footnotes"}
        absent_applicability = sorted(applicability_tables - tables.keys())
        if absent_applicability and not partial:
            raise TaricSnapshotError("Snapshot is missing applicability tables: " + ", ".join(absent_applicability) + "; pass partial=True only when the reduced scope is intentional")
        snapshot_hash = report["snapshot_sha256"]
        retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        manifest = {
            "source": SOURCE, "publisher": PUBLISHER, "reference_date": ref_date,
            "package_name": report.get("package_name"), "import_method": "manual",
            "retrieved_at": retrieved, "files": file_manifest, "sha256": snapshot_hash,
            "format_version": "Commission TARIC Excel extraction (table-level source format)",
            "ingestion_version": INGESTION_VERSION, "package_format": package_format,
            "parser_version": PARSER_VERSION, "acceptance_report": report,
            "partial": bool(missing or absent_applicability),
            "missing_tables": sorted(set(missing) | set(absent_applicability)),
            "supported_scope": ["nomenclature", "raw measures"] if missing or absent_applicability else ["nomenclature", "geography", "measures", "conditions", "references"],
        }
        db_path = self.root / f"snapshot-{snapshot_hash}.sqlite3"
        if not db_path.exists():
            fd, stage_name = tempfile.mkstemp(prefix="taric-", suffix=".sqlite3", dir=self.root)
            os.close(fd)
            stage = Path(stage_name)
            try:
                self._build_database(stage, manifest, tables)
                sanity = self._semantic_sanity(tables)
                if sanity["errors"]:
                    report["accepted"] = False
                    report["semantic_sanity"] = sanity
                    report.setdefault("parse_errors", []).extend(sanity["errors"])
                    _atomic_json(acceptance_dir / f"{snapshot_hash}.json", report)
                    raise TaricSnapshotError("Semantic validation failed: " + "; ".join(sanity["errors"][:10]))
                manifest["semantic_sanity"] = sanity
                manifest["acceptance_report"]["semantic_sanity"] = sanity
                manifest["acceptance_report"]["accepted"] = bool(report.get("accepted")) and sanity["accepted"]
                if not manifest["acceptance_report"]["accepted"]:
                    raise TaricSnapshotError("Acceptance report did not pass semantic checks")
                # Rewrite staged metadata with the completed acceptance report.
                with sqlite3.connect(stage) as stage_db:
                    for key, value in manifest.items():
                        stage_db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, json.dumps(value, sort_keys=True)))
                    stage_db.commit()
                self._sanity_check(stage, manifest)
                os.replace(stage, db_path)
            finally:
                stage.unlink(missing_ok=True)
        # Check data before changing either pointer. Active good snapshot remains
        # untouched if validation/building fails.
        self._sanity_check(db_path, manifest)
        archived_source_files = _archive_source(source, self.root, snapshot_hash)
        with sqlite3.connect(db_path) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                       ("archived_source_files", json.dumps(archived_source_files, sort_keys=True)))
            db.commit()
        manifest["archived_source_files"] = archived_source_files
        persisted_report = {**report, "semantic_sanity": manifest.get("semantic_sanity"), "accepted": True}
        _atomic_json(acceptance_dir / f"{snapshot_hash}.json", persisted_report)
        (self.root / "validation-failed.json").unlink(missing_ok=True)
        old = self._read_pointer("active.json")
        if old and old.get("sha256") != snapshot_hash:
            _atomic_json(self.root / "previous.json", old)
        pointer = {"sha256": snapshot_hash, "database": db_path.name, "activated_at": retrieved}
        _atomic_json(self.root / "active.json", pointer)
        return self.status()

    @staticmethod
    def _semantic_sanity(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        counts: dict[str, int] = {}
        goods = tables.get("Goods_Nomenclature", [])
        code_keys: set[tuple[str, str | None]] = set()
        levels: dict[str, str] = {}
        for row in goods:
            code = str(row.get("goods_code") or "")
            suffix = row.get("product_line_suffix")
            if not re.fullmatch(r"\d{10}", code):
                errors.append(f"invalid goods code at {row.get('_source_file')}:{row.get('_source_row')}")
            if suffix is not None and not re.fullmatch(r"\d{2}", str(suffix)):
                errors.append(f"invalid product-line suffix for {code}: {suffix}")
            code_keys.add((code, str(suffix) if suffix is not None else None))
            if row.get("hierarchy_level"):
                levels[code] = str(row["hierarchy_level"])
        for row in tables.get("Measures", []):
            code = str(row.get("goods_code") or "")
            if not re.fullmatch(r"\d{10}", code):
                errors.append(f"invalid measure goods code at {row.get('_source_file')}:{row.get('_source_row')}")
            start, end = _date_text(row.get("validity_start")), _date_text(row.get("validity_end"))
            if start and end and start > end:
                errors.append(f"measure validity start after end: {code}")
        for table, rows in tables.items():
            for row in rows:
                start, end = _date_text(row.get("validity_start")), _date_text(row.get("validity_end"))
                if start and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                    errors.append(f"invalid validity start in {table}: {start}")
                if end and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                    errors.append(f"invalid validity end in {table}: {end}")
                if start and end and start > end:
                    errors.append(f"validity start after end in {table}: {start} > {end}")
        # Ancestor links should resolve to an existing nomenclature code where
        # parent-level records are represented explicitly.
        known_codes = {code for code, _ in code_keys}
        missing_measure_codes = sorted({str(r.get("goods_code")) for r in tables.get("Measures", [])
                                        if r.get("goods_code") and str(r["goods_code"]) not in known_codes})
        if missing_measure_codes:
            errors.append(f"measure goods codes absent from nomenclature: {len(missing_measure_codes)}")
        area_ids = {str(r.get("area_id") or r.get("geographical_area") or "") for r in tables.get("Geographical_Areas", [])}
        area_ids.update(str(r.get("parent_area") or r.get("area_id") or "") for r in tables.get("Geographical_Area_Membership", []))
        unresolved_areas = sorted({str(r.get("geographical_area")) for r in tables.get("Measures", [])
                                   if r.get("geographical_area") and str(r["geographical_area"]) not in area_ids
                                   and str(r["geographical_area"]).upper() not in {"ERGA OMNES", "WORLD", "WORLDWIDE"}})
        if unresolved_areas and tables.get("Geographical_Areas"):
            errors.append(f"geographical references not resolved in extracted description/composition tables: {len(unresolved_areas)}")
        additional_ids = {str(r.get("additional_code")) for r in tables.get("Additional_Codes", []) if r.get("additional_code")}
        unresolved_additional = sorted({str(r.get("additional_code")) for r in tables.get("Measures", [])
                                        if r.get("additional_code") and str(r["additional_code"]) not in additional_ids})
        if unresolved_additional and tables.get("Additional_Codes"):
            errors.append(f"additional-code references unresolved: {len(unresolved_additional)}")
        type_ids = {str(r.get("measure_type")) for r in tables.get("Measure_Types", []) if r.get("measure_type")}
        unresolved_types = sorted({str(r.get("measure_type")) for r in tables.get("Measures", [])
                                   if r.get("measure_type") and type_ids and str(r["measure_type"]) not in type_ids})
        if unresolved_types:
            if tables.get("Measure_Types"):
                errors.append(f"measure types unresolved: {len(unresolved_types)}")
            else:
                warnings.append(f"measure types could not be checked because Measure_Types is absent: {len(unresolved_types)}")
        legal_ids = {str(r.get("regulation")) for r in tables.get("Legal_Bases", []) if r.get("regulation")}
        unresolved_legal = sorted({str(r.get("regulation")) for r in tables.get("Measures", [])
                                   if r.get("regulation") and legal_ids and str(r["regulation"]) not in legal_ids})
        if unresolved_legal:
            errors.append(f"measure legal-base references unresolved: {len(unresolved_legal)}")
        counts["goods_codes"] = len(goods)
        counts["measures"] = len(tables.get("Measures", []))
        counts["unresolved_measure_goods_codes"] = len(missing_measure_codes)
        counts["unresolved_geographical_references"] = len(unresolved_areas)
        counts["unresolved_additional_codes"] = len(unresolved_additional)
        counts["unresolved_measure_types"] = len(unresolved_types)
        return {"accepted": not errors, "errors": errors, "warnings": warnings, "statistics": counts}

    def doctor(self, source: str | Path, *, mode: str = "compatible", partial: bool = False, persist: bool = True) -> dict[str, Any]:
        report = _discover_package(source, mode=mode, partial=partial)
        report.update({"reference_date_candidates": report.pop("date_reference_candidates", []),
                       "ingestion_version": INGESTION_VERSION, "parser_version": PARSER_VERSION,
                       "retrieved_at": datetime.now(timezone.utc).isoformat()})
        if persist:
            directory = self.root / "acceptance"
            directory.mkdir(parents=True, exist_ok=True)
            _atomic_json(directory / f"{report['snapshot_sha256']}.json", report)
        return report

    def _build_database(self, path: Path, manifest: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> None:
        with sqlite3.connect(path) as db:
            db.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tables (name TEXT PRIMARY KEY, row_count INTEGER NOT NULL);
                CREATE TABLE taric_rows (
                    id INTEGER PRIMARY KEY, table_name TEXT NOT NULL, goods_code TEXT,
                    product_line_suffix TEXT,
                    measure_type TEXT, geographical_area TEXT, validity_start TEXT,
                    validity_end TEXT, additional_code TEXT, regulation TEXT,
                    country_code TEXT, parent_area TEXT, area_id TEXT, excluded_area TEXT,
                    condition_code TEXT, footnote_code TEXT, certificate_code TEXT,
                    is_declarable TEXT,
                    row_json TEXT NOT NULL
                );
                CREATE INDEX idx_taric_goods ON taric_rows(goods_code);
                CREATE INDEX idx_taric_goods_suffix ON taric_rows(goods_code, product_line_suffix);
                CREATE INDEX idx_taric_measure ON taric_rows(measure_type);
                CREATE INDEX idx_taric_geo ON taric_rows(geographical_area);
                CREATE INDEX idx_taric_validity ON taric_rows(validity_start, validity_end);
                CREATE INDEX idx_taric_additional ON taric_rows(additional_code);
                CREATE INDEX idx_taric_regulation ON taric_rows(regulation);
                CREATE INDEX idx_taric_table_goods ON taric_rows(table_name, goods_code);
                CREATE INDEX idx_taric_country_area ON taric_rows(country_code, parent_area);
                CREATE INDEX idx_taric_area_id ON taric_rows(area_id);
                CREATE INDEX idx_taric_excluded_area ON taric_rows(excluded_area);
            """)
            for key, value in manifest.items():
                db.execute("INSERT INTO metadata VALUES (?,?)", (key, json.dumps(value, sort_keys=True)))
            for table, rows in tables.items():
                db.execute("INSERT INTO tables VALUES (?,?)", (table, len(rows)))
                db.executemany(
                    "INSERT INTO taric_rows(table_name,goods_code,product_line_suffix,measure_type,geographical_area,validity_start,validity_end,additional_code,regulation,country_code,parent_area,area_id,excluded_area,condition_code,footnote_code,certificate_code,is_declarable,row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(table, r.get("goods_code"), r.get("product_line_suffix"), r.get("measure_type"), r.get("geographical_area"),
                      _date_text(r.get("validity_start")), _date_text(r.get("validity_end")),
                      r.get("additional_code"), r.get("regulation"), r.get("country_code"), r.get("parent_area"),
                      r.get("area_id"), r.get("excluded_area"), r.get("condition_code"), r.get("footnote_code"),
                      r.get("certificate_code"), r.get("is_declarable"), json.dumps(r, sort_keys=True)) for r in rows],
                )
            db.commit()

    @staticmethod
    def _sanity_check(path: Path, manifest: dict[str, Any]) -> None:
        with sqlite3.connect(path) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise TaricSnapshotError("Built SQLite index failed integrity check")
            present = {r[0] for r in db.execute("SELECT name FROM tables")}
            if not CORE_REQUIRED_TABLES <= present:
                raise TaricSnapshotError("Indexed database is missing core TARIC tables")
            if db.execute("SELECT COUNT(*) FROM taric_rows WHERE table_name='Goods_Nomenclature'").fetchone()[0] == 0:
                raise TaricSnapshotError("Indexed nomenclature is empty")
            if db.execute("SELECT COUNT(*) FROM taric_rows WHERE table_name='Measures'").fetchone()[0] == 0:
                raise TaricSnapshotError("Indexed measures are empty")

    def _read_pointer(self, filename: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.root / filename).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def record_refresh_failure(self, detail: str, *, error_code: str = "refresh_failed") -> None:
        """Record a safe normalized failed-refresh marker without raw payloads."""
        _atomic_json(self.root / "refresh-failure.json", {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "detail": detail,
            "error_code": error_code,
        })

    def _db_path(self, pointer: dict[str, Any]) -> Path:
        path = self.root / pointer["database"]
        if not path.exists():
            raise TaricSnapshotError("Active snapshot database is missing")
        return path

    def status(self) -> dict[str, Any]:
        active = self._read_pointer("active.json")
        previous = self._read_pointer("previous.json")
        result: dict[str, Any] = {"source": SOURCE, "publisher": PUBLISHER,
                                  "state": "installed_current" if active else "not_installed",
                                  "installation_state": "installed_current" if active else "not_installed",
                                  "active": None, "last_good": None}
        failed_refresh = self._read_pointer("refresh-failure.json")
        if active and failed_refresh:
            result["state"] = "installed_stale"
            result["installation_state"] = "installed_stale"
            result["last_refresh_failure"] = failed_refresh
        for label, pointer in (("active", active), ("last_good", previous or active)):
            if not pointer:
                continue
            db_path = self._db_path(pointer)
            with sqlite3.connect(db_path) as db:
                meta = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM metadata")}
                counts = dict(db.execute("SELECT name,row_count FROM tables"))
            result[label] = {**pointer, **meta, "row_counts": counts,
                             "stale": bool(failed_refresh and label == "active"),
                             "source_detail": "stale_cache_after_refresh_failure" if failed_refresh and label == "active" else None}
        if active and not failed_refresh:
            active_ref = result.get("active", {}).get("reference_date")
            if active_ref and active_ref < datetime.now(timezone.utc).date().isoformat():
                result["state"] = "installed_stale"
                result["installation_state"] = "installed_stale"
                result["active"]["stale"] = True
                result["active"]["source_detail"] = "snapshot_reference_date_is_historical"
        validation_failure = self._read_pointer("validation-failed.json")
        if validation_failure:
            result["last_validation_failure"] = validation_failure
            if not active:
                result["state"] = "validation_failed"
                result["installation_state"] = "validation_failed"
        return result

    def fingerprint(self, *, include_acceptance: bool = False) -> dict[str, Any]:
        state = self.status()
        active = state.get("active")
        if not active:
            return {"source": SOURCE, "installation_state": state["installation_state"], "snapshot_sha256": None}
        report = active.get("acceptance_report", {})
        result = {"source": SOURCE, "installation_state": state["installation_state"],
                  "snapshot_sha256": active.get("sha256"), "reference_date": active.get("reference_date"),
                  "tables": active.get("row_counts", {}),
                  "normalized_header_sets": report.get("normalized_headers", {}),
                  "duty_expression_patterns": report.get("duty_expression_patterns", []),
                  "semantic_sanity": active.get("semantic_sanity", {})}
        return result

    def query(self, goods_code: str, origin_country: str, assessment_date: str | None = None) -> dict[str, Any]:
        pointer = self._read_pointer("active.json")
        if not pointer:
            return {"source": SOURCE, "source_outcome": "source_unavailable", "source_detail": "snapshot_not_installed", "measures": []}
        path = self._db_path(pointer)
        effective = (date.fromisoformat(assessment_date).isoformat() if assessment_date
                     else datetime.now(timezone.utc).date().isoformat())
        code = re.sub(r"\s", "", str(goods_code))
        if not code.isdigit():
            raise ValueError("goods_code must contain digits only")
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            meta = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM metadata")}
            table_counts = dict(db.execute("SELECT name,row_count FROM tables"))
            normalized_code = code.ljust(10, "0")
            nomenclature = [json.loads(r[0]) for r in db.execute("SELECT row_json FROM taric_rows WHERE table_name='Goods_Nomenclature' AND goods_code=?", (normalized_code,))]
            if not nomenclature:
                nomenclature = [json.loads(r[0]) for r in db.execute("SELECT row_json FROM taric_rows WHERE table_name='Goods_Nomenclature' AND goods_code=?", (code,))]
            # Prefix matching can mistake sibling codes for descendants. A row
            # is a nomenclature descendant only when its padded 10-digit code
            # starts with the input prefix and is longer in hierarchical level.
            children = [json.loads(r[0]) for r in db.execute("SELECT row_json FROM taric_rows WHERE table_name='Goods_Nomenclature' AND goods_code LIKE ? AND goods_code<>? LIMIT 1000", (code + "%", code))]
            def row_is_active(row: dict[str, Any]) -> bool:
                start, end = _date_text(row.get("validity_start")), _date_text(row.get("validity_end"))
                return not (start and effective < start or end and effective > end)
            nomenclature = [row for row in nomenclature if row_is_active(row)]
            children = [row for row in children if row_is_active(row) and str(row.get("hierarchy_level") or "0").isdigit()
                        and int(row.get("hierarchy_level") or "0") > int(next((r.get("hierarchy_level") for r in nomenclature), 0) or 0)]
            if not nomenclature:
                return {"source": SOURCE, "source_outcome": "partial", "source_detail": "code_not_in_snapshot", "code": code, "measures": [], "snapshot": meta}
            nomenclature_row = nomenclature[0]
            level_value = nomenclature_row.get("hierarchy_level")
            level_map = {"2": "chapter", "4": "heading", "6": "hs6", "8": "cn8", "10": "taric10"}
            level = level_map.get(str(level_value), f"level_{level_value}" if level_value else f"digits_{len(code)}")
            declarable_rows = [json.loads(r[0]) for r in db.execute("SELECT row_json FROM taric_rows WHERE table_name='Declarable_Codes' AND goods_code=?", (normalized_code,))]
            if not declarable_rows:
                declarable_rows = [json.loads(r[0]) for r in db.execute("SELECT row_json FROM taric_rows WHERE table_name='Declarable_Codes' AND goods_code=?", (code,))]
            declarable_rows = [row for row in declarable_rows if row_is_active(row)]
            leaf_raw = declarable_rows[0].get("is_declarable") if declarable_rows else nomenclature_row.get("is_declarable")
            leaf_value = str(leaf_raw or "").lower()
            is_declarable: bool | None = (leaf_value in {"1", "true", "yes", "y"}) if leaf_value else None
            has_children = bool(children)
            # Select exact code and true ancestors from the official 10-digit
            # hierarchy. Measures can be declared at a parent level.
            prefixes = [normalized_code[:n].ljust(10, "0") for n in (2, 4, 6, 8, 10) if n <= len(code)]
            # Official rows keep their canonical 10-digit representation with
            # trailing zeroes; common spreadsheet fixtures/legacy exports may
            # omit that right padding. Match both without introducing sibling
            # codes (all values here are hierarchy prefixes).
            prefixes.extend(code[:n] for n in (2, 4, 6, 8, 10) if n <= len(code))
            prefixes = list(dict.fromkeys(prefixes))
            measure_rows = []
            params: list[Any] = [*prefixes]
            if prefixes:
                sql = "SELECT row_json FROM taric_rows WHERE table_name='Measures' AND goods_code IN (" + ",".join("?" for _ in prefixes) + ")"
                for raw, in db.execute(sql, params):
                    row = json.loads(raw)
                    start, end = _date_text(row.get("validity_start")), _date_text(row.get("validity_end"))
                    if start and effective < start or end and effective > end:
                        continue
                    row["defined_goods_code"] = row.get("goods_code")
                    row["inherited_from_parent"] = row.get("goods_code") != code
                    measure_rows.append(row)
            def read_json_rows(table: str, where: str = "", values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
                sql = "SELECT row_json FROM taric_rows WHERE table_name=?" + (" AND " + where if where else "")
                return [json.loads(r[0]) for r in db.execute(sql, (table, *values))]
            type_ids = sorted({str(row.get("measure_type")) for row in measure_rows if row.get("measure_type")})
            measure_type_rows = read_json_rows("Measure_Types", "measure_type IN (" + ",".join("?" for _ in type_ids) + ")" if type_ids else "1=0", tuple(type_ids))
            areas_used = sorted({str(r.get("geographical_area")) for r in measure_rows if r.get("geographical_area")})
            membership_sql = "country_code=?"
            membership_params: tuple[Any, ...] = (origin_country.upper(),)
            if areas_used:
                membership_sql += " OR parent_area IN (" + ",".join("?" for _ in areas_used) + ")"
                membership_params += tuple(areas_used)
            memberships = read_json_rows("Geographical_Area_Membership", "(" + membership_sql + ")", membership_params)
            areas = {str(r.get("area_id") or r.get("parent_area") or ""): r for r in
                     read_json_rows("Geographical_Areas", "area_id IN (" + ",".join("?" for _ in areas_used) + ")" if areas_used else "", tuple(areas_used))}
            placeholders = ",".join("?" for _ in prefixes)
            code_filter = f"goods_code IN ({placeholders})" if prefixes else "1=0"
            exclusions = read_json_rows("Measure_Exclusions", code_filter, tuple(prefixes))
            conditions = read_json_rows("Measure_Conditions", code_filter, tuple(prefixes))
            footnotes = read_json_rows("Measure_Footnotes", code_filter, tuple(prefixes))
            regulations = sorted({r.get("regulation") for r in measure_rows if r.get("regulation")})
            legal = read_json_rows("Legal_Bases", "regulation IN (" + ",".join("?" for _ in regulations) + ")" if regulations else "1=0", tuple(regulations))
            additional_codes = sorted({r.get("additional_code") for r in measure_rows if r.get("additional_code")})
            additional = read_json_rows("Additional_Codes", "additional_code IN (" + ",".join("?" for _ in additional_codes) + ")" if additional_codes else "1=0", tuple(additional_codes))

        origin = origin_country.upper()
        def active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            active: list[dict[str, Any]] = []
            for row in rows:
                start, end = _date_text(row.get("validity_start")), _date_text(row.get("validity_end"))
                if start and effective < start or end and effective > end:
                    continue
                active.append(row)
            return active
        memberships = active_rows(memberships)
        exclusions = active_rows(exclusions)
        conditions = active_rows(conditions)
        footnotes = active_rows(footnotes)
        legal = active_rows(legal)
        additional = active_rows(additional)
        def area_matches(area: str | None) -> bool | None:
            if not area:
                return None
            if area.upper() in {origin, "ERGA OMNES", "WORLD", "WORLDWIDE"}:
                return True
            if area in areas:
                area_row = areas[area]
                if (area_row.get("country_code") or "").upper() == origin:
                    return True
                label = " ".join(str(area_row.get(k) or "") for k in ("description", "acronym", "country_code")).lower()
                if any(term in label for term in ("erga omnes", "all origins", "all third countries")):
                    return True
            found = False
            for membership in memberships:
                group = membership.get("parent_area") or membership.get("area_id") or membership.get("geographical_area")
                country = membership.get("country_code") or membership.get("member_code") or membership.get("member")
                if str(group) == area:
                    found = True
                    if str(country).upper() == origin:
                        return True
            return False if found else None

        enriched = []
        for measure in measure_rows:
            area = measure.get("geographical_area")
            match = area_matches(area)
            keyfields = ("goods_code", "measure_type", "geographical_area", "additional_code", "quota_order_number")
            same_measure = lambda item: (any(item.get(k) for k in keyfields) and
                                         all(not item.get(k) or item.get(k) == measure.get(k) for k in keyfields))
            excluded = any(same_measure(ex) and
                            (not ex.get("excluded_area") or area_matches(ex.get("excluded_area")) is True) for ex in exclusions)
            related = lambda rows: [x for x in rows if same_measure(x)]
            measure["origin_applicability"] = "applicable" if match is True else "excluded" if excluded else "candidate" if match is None else "not_applicable"
            if excluded:
                measure["origin_applicability"] = "excluded"
            measure["conditions"] = related(conditions)
            measure["footnotes"] = related(footnotes)
            measure["legal_basis"] = [x for x in legal if measure.get("regulation") and x.get("regulation") == measure.get("regulation")]
            measure["additional_code_details"] = related(additional)
            measure["additional_code_required"] = bool(measure.get("additional_code"))
            expression = str(measure.get("duty_expression") or "")
            measure["duty_expression_support"] = (
                "supported" if SUPPORTED_DUTY_RE.fullmatch(expression) else
                "preserved_but_not_calculable" if expression else "unsupported"
            )
            measure["calculation_status"] = "not_calculated" if measure["duty_expression_support"] == "supported" else "unsupported"
            measure["measure_type_description"] = next((row.get("description") or row.get("measure_category")
                for row in measure_type_rows if row.get("measure_type") == measure.get("measure_type")), None)
            measure["measure_identifier"] = "taric:" + hashlib.sha256(json.dumps({
                key: measure.get(key) for key in ("goods_code", "measure_type", "geographical_area",
                    "additional_code", "quota_order_number", "validity_start", "validity_end", "duty_expression", "regulation")
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
            measure["source_row_reference"] = {"file": measure.get("_source_file"),
                "worksheet": measure.get("_source_sheet"), "defined_goods_code": measure.get("defined_goods_code")}
            measure["applicability_complete"] = not meta.get("partial") and measure["origin_applicability"] not in ("candidate",)
            enriched.append(measure)
        is_leaf_record = not children
        declarable = bool(is_declarable) and is_leaf_record
        refresh_failure = self._read_pointer("refresh-failure.json")
        snapshot_stale = bool(meta.get("reference_date") and meta["reference_date"] < effective)
        stale = bool(refresh_failure or snapshot_stale)
        complete_scope = not meta.get("partial") and bool(table_counts.get("Geographical_Area_Membership")) and bool(table_counts.get("Measure_Exclusions")) and bool(table_counts.get("Measure_Conditions"))
        applicable_found = any(m.get("origin_applicability") == "applicable" for m in enriched)
        no_measures_confirmed = complete_scope and not enriched
        detail = ("stale_cache_after_refresh_failure" if refresh_failure else "stale_snapshot" if snapshot_stale else
                 "response_no_applicable_measures" if no_measures_confirmed else "response_complete" if applicable_found and not meta.get("partial") else "response_partial")
        return {
            "source": SOURCE, "publisher": PUBLISHER,
            "source_outcome": "partial" if stale or meta.get("partial") or not applicable_found and not no_measures_confirmed else "success",
            "source_detail": detail,
            "stale": stale,
            "assessment_date": effective,
            "classification": {"code": code, "level": level, "is_declarable": declarable if is_declarable is not None else None,
                               "has_children": has_children, "required_precision": None if declarable else "official_declarable_leaf" if is_declarable is False or has_children else "declarable_code_data_required",
                               "candidate_subdivisions": [r.get("goods_code") for r in children[:100]]},
            "measures": enriched, "snapshot": meta,
            "complete_for_applicability": complete_scope,
        }
