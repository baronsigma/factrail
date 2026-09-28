"""Offline ingestion and query support for official Commission TARIC snapshots.

This module deliberately separates acquisition from ingestion. It accepts official
Commission Excel extracts already supplied as files, directories, or ZIP archives;
queries never require a network connection.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    "is_leaf": {"isleaf"},
    "operation": {"publish", "operation"},
    "sequence_number": {"sequencenumber", "sequence"},
}


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
        key = _norm(name)
        row[name] = text
        if "measuretype" in key and "code" in key:
            row["measure_type"] = text
        if "isleaf" in key:
            row["is_declarable"] = text
        if key in {"countrygroupcode", "countrygroupidentifier"}:
            row["parent_area"] = text
        if key in {"countrycodeabbreviation", "membercountrycode", "isocode"}:
            row["country_code"] = text
        if key in {"countryterritorycode", "memberidentifiercode", "membercode"}:
            row["member_code"] = text
        if key in {"countrybecamemember", "membershipstartdate", "countrybecamememberdate"}:
            row["validity_start"] = text
        if key in {"countryceasetobemember", "membershipenddate", "countryceasetobememberdate"}:
            row["validity_end"] = text
        for canonical, aliases in _FIELD_ALIASES.items():
            if key in aliases:
                row[canonical] = text
                break
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


def _source_files(source: str | Path) -> tuple[list[tuple[str, bytes]], str]:
    path = Path(source)
    if path.is_dir():
        files = [(p.relative_to(path).as_posix(), p.read_bytes()) for p in sorted(path.rglob("*.xlsx"))]
        if not files:
            raise TaricSnapshotError("No .xlsx workbooks found in snapshot directory")
        return files, "directory"
    if not path.is_file():
        raise TaricSnapshotError(f"Snapshot path does not exist: {path}")
    if path.suffix.lower() == ".xlsx":
        return [(path.name, path.read_bytes())], "xlsx"
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            files = [(n, archive.read(n)) for n in sorted(archive.namelist()) if n.lower().endswith(".xlsx") and not n.startswith("__MACOSX/")]
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
               retrieved_at: datetime | None = None) -> dict[str, Any]:
        try:
            result = self._ingest(source, reference_date=reference_date, partial=partial, retrieved_at=retrieved_at)
            (self.root / "refresh-failure.json").unlink(missing_ok=True)
            return result
        except Exception as exc:
            self.record_refresh_failure("snapshot_validation_failed", error_code=type(exc).__name__)
            raise

    def _ingest(self, source: str | Path, *, reference_date: str, partial: bool = False,
               retrieved_at: datetime | None = None) -> dict[str, Any]:
        try:
            ref_date = date.fromisoformat(reference_date).isoformat()
        except ValueError as exc:
            raise TaricSnapshotError("reference_date must be YYYY-MM-DD") from exc
        source_files, package_format = _source_files(source)
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
        digest_payload = b"".join(name.encode() + b"\0" + bytes.fromhex(entry["sha256"]) for (name, _), entry in zip(source_files, file_manifest))
        snapshot_hash = hashlib.sha256(digest_payload).hexdigest()
        retrieved = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        manifest = {
            "source": SOURCE, "publisher": PUBLISHER, "reference_date": ref_date,
            "retrieved_at": retrieved, "files": file_manifest, "sha256": snapshot_hash,
            "format_version": "Commission TARIC Excel extraction (table-level source format)",
            "ingestion_version": INGESTION_VERSION, "package_format": package_format,
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
                self._sanity_check(stage, manifest)
                os.replace(stage, db_path)
            finally:
                stage.unlink(missing_ok=True)
        # Check data before changing either pointer. Active good snapshot remains
        # untouched if validation/building fails.
        self._sanity_check(db_path, manifest)
        old = self._read_pointer("active.json")
        if old and old.get("sha256") != snapshot_hash:
            _atomic_json(self.root / "previous.json", old)
        pointer = {"sha256": snapshot_hash, "database": db_path.name, "activated_at": retrieved}
        _atomic_json(self.root / "active.json", pointer)
        return self.status()

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
                                  "state": "ready" if active else "source_unavailable",
                                  "active": None, "last_good": None}
        failed_refresh = self._read_pointer("refresh-failure.json")
        if active and failed_refresh:
            result["state"] = "stale"
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
