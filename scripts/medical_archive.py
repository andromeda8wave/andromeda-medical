#!/usr/bin/env python3
"""Deterministic local CLI for the OpenClaw medical archive skill."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
PARSER_VERSION = "andromeda-medical-cli-v2"
ALLOWED_SOURCE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".heic", ".heif"}
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
TEST_TYPE_MAP = [
    (("общий анализ крови", "оак", "cbc"), "CBC"),
    (("биохимия", "биохимический анализ"), "Biochemistry"),
    (("общий анализ мочи", "оам"), "Urinalysis"),
    (("гормоны щитовидной железы", "ттг", "т3", "т4"), "Hormones"),
    (("коагулограмма", "мно", "протромбин"), "Coagulogram"),
    (("липидный профиль", "холестерин"), "Lipids"),
    (("глюкоза", "сахар"), "Glucose"),
    (("узи",), "Ultrasound"),
    (("экг", "электрокардиограмма"), "ECG"),
    (("флюорография", "рентген"), "XRay"),
    (("пцр", "pcr", "полимеразная цепная"), "PCR"),
    (("аллергология", "ige", "аллерген"), "Allergy"),
    (("иммунограмма", "иммунный статус"), "Immunology"),
    (("бакпосев", "посев", "чувствительность"), "Culture"),
    (("гистология", "биопсия"), "Histology"),
    (("мрт", "магнитно-резонансная"), "MRI"),
    (("кт", "компьютерная томография"), "CT"),
    (("витамины", "витамин d", "b12"), "Vitamins"),
    (("онкомаркеры", "пса", "ca-125"), "Oncomarkers"),
    (("инфекции", "вич", "гепатит", "rw"), "Infections"),
]


class MedicalArchiveError(Exception):
    """Base class for expected CLI errors."""


class ValidationError(MedicalArchiveError):
    """Raised when user or extracted data is invalid."""


class CliArgumentParser(argparse.ArgumentParser):
    """Argument parser that reports usage problems as JSON-friendly errors."""

    def error(self, message: str) -> None:
        raise ValidationError(f"{self.prog}: {message}")


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, sqlite3.Row):
        return dict(value)
    if isinstance(value, dict):
        return {key: json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value


def emit(result: dict[str, Any]) -> None:
    print(json.dumps(json_ready(result), ensure_ascii=False, indent=2, sort_keys=True))


def get_root() -> Path:
    raw = os.environ.get("MEDICAL_ARCHIVE_ROOT")
    if not raw:
        raise ValidationError("MEDICAL_ARCHIVE_ROOT is not set")
    return Path(raw).expanduser().resolve()


def db_path(root: Path | None = None) -> Path:
    root = root or get_root()
    return root / "metrics.db"


def ensure_within_root(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"path is outside MEDICAL_ARCHIVE_ROOT: {path}") from exc
    return resolved


def root_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def resolve_root_path(raw_path: str, root: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    return ensure_within_root(path, root)


def connect(root: Path | None = None, *, enable_wal: bool = True) -> sqlite3.Connection:
    root = root or get_root()
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if enable_wal:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return dt.date.today().isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({sql_quote_identifier(table)})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {sql_quote_identifier(table)} ADD COLUMN {column} {definition}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT UNIQUE NOT NULL,
            short_name TEXT,
            folder TEXT NOT NULL,
            added_date DATE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            test_date DATE NOT NULL,
            test_type TEXT NOT NULL,
            file_path TEXT UNIQUE NOT NULL,
            source_filename TEXT,
            source_checksum TEXT,
            institution TEXT,
            processed_at TEXT,
            review_status TEXT DEFAULT 'needs_review',
            parser_version TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(id)
        );

        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            parameter TEXT NOT NULL,
            value TEXT,
            unit TEXT,
            reference_range TEXT,
            status TEXT,
            status_code TEXT,
            row_order INTEGER,
            panel TEXT,
            material TEXT,
            method TEXT,
            numeric_value REAL,
            numeric_min REAL,
            numeric_max REAL,
            FOREIGN KEY(document_id) REFERENCES documents(id),
            UNIQUE(document_id, parameter)
        );
        """
    )
    ensure_v2_columns(conn)


def ensure_v2_columns(conn: sqlite3.Connection) -> None:
    add_column_if_missing(conn, "documents", "source_filename", "TEXT")
    add_column_if_missing(conn, "documents", "source_checksum", "TEXT")
    add_column_if_missing(conn, "documents", "institution", "TEXT")
    add_column_if_missing(conn, "documents", "processed_at", "TEXT")
    add_column_if_missing(conn, "documents", "review_status", "TEXT DEFAULT 'needs_review'")
    add_column_if_missing(conn, "documents", "parser_version", "TEXT")
    add_column_if_missing(conn, "test_results", "status_code", "TEXT")
    add_column_if_missing(conn, "test_results", "row_order", "INTEGER")
    add_column_if_missing(conn, "test_results", "panel", "TEXT")
    add_column_if_missing(conn, "test_results", "material", "TEXT")
    add_column_if_missing(conn, "test_results", "method", "TEXT")
    add_column_if_missing(conn, "test_results", "numeric_value", "REAL")
    add_column_if_missing(conn, "test_results", "numeric_min", "REAL")
    add_column_if_missing(conn, "test_results", "numeric_max", "REAL")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_documents_patient_date
            ON documents(patient_id, test_date);
        CREATE INDEX IF NOT EXISTS idx_documents_test_date
            ON documents(test_date);
        CREATE INDEX IF NOT EXISTS idx_test_results_parameter
            ON test_results(parameter);
        CREATE INDEX IF NOT EXISTS idx_test_results_status_code
            ON test_results(status_code);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_folder_unique
            ON patients(folder);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_source_checksum_unique
            ON documents(source_checksum)
            WHERE source_checksum IS NOT NULL AND source_checksum != '';
        """
    )


def backfill_v2(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE documents
        SET review_status = 'imported'
        WHERE parser_version IS NULL
          AND (review_status IS NULL OR review_status = 'needs_review')
        """
    )
    conn.execute("UPDATE documents SET parser_version = 'legacy-v1' WHERE parser_version IS NULL")
    rows = conn.execute(
        """
        SELECT id, status, value, reference_range
        FROM test_results
        WHERE status_code IS NULL
           OR numeric_value IS NULL
           OR numeric_min IS NULL
           OR numeric_max IS NULL
        """
    ).fetchall()
    for row in rows:
        status_code = normalize_status(row["status"], row["value"], row["reference_range"])
        numeric_value = parse_number(row["value"])
        numeric_min, numeric_max = parse_reference_range(row["reference_range"])
        conn.execute(
            """
            UPDATE test_results
            SET status_code = COALESCE(status_code, ?),
                numeric_value = COALESCE(numeric_value, ?),
                numeric_min = COALESCE(numeric_min, ?),
                numeric_max = COALESCE(numeric_max, ?)
            WHERE id = ?
            """,
            (status_code, numeric_value, numeric_min, numeric_max, row["id"]),
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, now_iso()),
    )


def migrate_schema(conn: sqlite3.Connection) -> None:
    create_schema(conn)
    backfill_v2(conn)


def backup_db(root: Path) -> Path | None:
    source = db_path(root)
    if not source.exists():
        return None
    backup_dir = root / "Backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"metrics.db.before-v2-{stamp}.bak"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def transliterate(text: str) -> str:
    mapping = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
    out = []
    for char in text:
        lower = char.lower()
        if lower in mapping:
            value = mapping[lower]
            out.append(value.capitalize() if char.isupper() else value)
        elif char.isascii() and char.isalnum():
            out.append(char)
    return "".join(out)


def compact_name(value: str) -> str:
    return "".join(part.capitalize() for part in re.findall(r"[A-Za-z0-9]+", value))


def patient_names(full_name: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", full_name.strip()) if part]
    if len(parts) < 2:
        raise ValidationError("full name must include at least surname and first name")
    surname = compact_name(transliterate(parts[0]))
    initials = "".join(compact_name(transliterate(part[:1])) for part in parts[1:3])
    if not surname or not initials:
        raise ValidationError("could not transliterate patient name")
    return surname + initials, surname + initials


def infer_type_slug(test_type: str) -> str:
    lowered = test_type.lower()
    for keywords, slug in TEST_TYPE_MAP:
        if any(keyword in lowered for keyword in keywords):
            return slug
    fallback = compact_name(transliterate(test_type))
    return fallback[:48] or "Analysis"


def sanitize_slug(value: str) -> str:
    slug = compact_name(transliterate(value))
    return slug[:64] or "Analysis"


def parse_iso_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"test_date must be YYYY-MM-DD: {value}") from exc


def display_status(status_code: str) -> str:
    return {
        "ok": "✅ OK",
        "high": "🔴 HIGH",
        "low": "🔵 LOW",
        "unknown": "❓",
    }.get(status_code, "❓")


def normalize_status(status: Any, value: Any = None, reference_range: Any = None) -> str:
    raw = str(status or "").strip().lower()
    if raw in {"ok", "normal", "норма", "✅ ok", "✅"} or "ok" in raw:
        return "ok"
    if raw in {"high", "above", "выше", "🔴 high"} or "high" in raw or "выше" in raw:
        return "high"
    if raw in {"low", "below", "ниже", "🔵 low"} or "low" in raw or "ниже" in raw:
        return "low"
    if raw in {"unknown", "неизвестно", "неясно", "?", "❓"}:
        return "unknown"
    numeric_value = parse_number(value)
    numeric_min, numeric_max, min_inclusive, max_inclusive = parse_reference_bounds(reference_range)
    if numeric_value is not None:
        if numeric_min is not None and (numeric_value < numeric_min or (numeric_value == numeric_min and not min_inclusive)):
            return "low"
        if numeric_max is not None and (numeric_value > numeric_max or (numeric_value == numeric_max and not max_inclusive)):
            return "high"
        if numeric_min is not None or numeric_max is not None:
            return "ok"
    return "unknown"


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_reference_range(value: Any) -> tuple[float | None, float | None]:
    numeric_min, numeric_max, _min_inclusive, _max_inclusive = parse_reference_bounds(value)
    return numeric_min, numeric_max


def parse_reference_bounds(value: Any) -> tuple[float | None, float | None, bool, bool]:
    if value is None:
        return None, None, True, True
    text = str(value).strip().replace(",", ".").replace("−", "-").replace("–", "-").replace("—", "-")
    if not text:
        return None, None, True, True
    range_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*-\s*([-+]?\d+(?:\.\d+)?)", text)
    if range_match:
        return float(range_match.group(1)), float(range_match.group(2)), True, True
    upper_match = re.search(r"(<|≤|до|менее)\s*([-+]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if upper_match:
        operator = upper_match.group(1).lower()
        return None, float(upper_match.group(2)), True, operator not in {"<", "менее"}
    lower_match = re.search(r"(>|≥|от|более)\s*([-+]?\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if lower_match:
        operator = lower_match.group(1).lower()
        return float(lower_match.group(2)), None, operator not in {">", "более"}, True
    return None, None, True, True


def load_payload(path: str) -> dict[str, Any]:
    payload_path = Path(path).expanduser()
    try:
        with payload_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    return payload


def validate_payload_shape(payload: dict[str, Any], *, require_source: bool) -> None:
    missing = []
    for key in ("patient_full_name", "test_date", "test_type", "results"):
        if key not in payload or payload[key] in ("", None, []):
            missing.append(key)
    if require_source and not payload.get("source_path"):
        missing.append("source_path")
    if missing:
        raise ValidationError("missing required fields: " + ", ".join(missing))
    parse_iso_date(str(payload["test_date"]))
    if not isinstance(payload["results"], list) or not payload["results"]:
        raise ValidationError("results must be a non-empty array")
    seen_parameters: set[str] = set()
    for index, row in enumerate(payload["results"], start=1):
        if not isinstance(row, dict):
            raise ValidationError(f"results[{index}] must be an object")
        parameter = str(row.get("parameter") or "").strip()
        if not parameter:
            raise ValidationError(f"results[{index}].parameter is required")
        if parameter in seen_parameters:
            raise ValidationError(f"duplicate parameter in one document: {parameter}")
        seen_parameters.add(parameter)
        if "row_order" in row and row["row_order"] not in (None, ""):
            try:
                int(row["row_order"])
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"results[{index}].row_order must be an integer") from exc


def validate_source_file(raw_path: str, root: Path) -> Path:
    source_abs = resolve_root_path(raw_path, root)
    raw_dir = (root / "Raw files").resolve()
    try:
        source_abs.relative_to(raw_dir)
    except ValueError as exc:
        raise ValidationError("source_path must point to a file inside $MEDICAL_ARCHIVE_ROOT/Raw files") from exc
    if source_abs.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_SOURCE_SUFFIXES))
        raise ValidationError(f"unsupported source extension: {source_abs.suffix or '<none>'}; allowed: {allowed}")
    if not source_abs.is_file():
        raise ValidationError(f"source file does not exist or is not a regular file: {raw_path}")
    return source_abs


def patient_by_name(conn: sqlite3.Connection, full_name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM patients WHERE full_name = ?", (full_name,)).fetchone()


def document_by_file_path(conn: sqlite3.Connection, file_path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE file_path = ?", (file_path,)).fetchone()


def document_by_checksum(conn: sqlite3.Connection, checksum: str) -> sqlite3.Row | None:
    if not checksum:
        return None
    return conn.execute("SELECT * FROM documents WHERE source_checksum = ?", (checksum,)).fetchone()


def target_paths(root: Path, patient: sqlite3.Row, payload: dict[str, Any], suffix: str | None = None) -> tuple[Path, Path, str, str]:
    test_date = parse_iso_date(str(payload["test_date"]))
    type_slug = sanitize_slug(str(payload.get("type_slug") or infer_type_slug(str(payload["test_type"]))))
    surname = compact_name(transliterate(str(payload["patient_full_name"]).split()[0]))
    stem = f"{test_date:%d-%m-%Y}_{surname}_{type_slug}"
    if suffix:
        stem = f"{stem}_{suffix}"
    target_dir = root / str(patient["folder"]) / "Documents" / f"{test_date:%Y}" / MONTHS[test_date.month - 1]
    source_suffix = Path(str(payload.get("source_path") or "source.pdf")).suffix or ".pdf"
    md_path = target_dir / f"{stem}.md"
    source_path = target_dir / f"{stem}{source_suffix.lower()}"
    return md_path, source_path, type_slug, stem


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def markdown_for_payload(payload: dict[str, Any], source_filename: str) -> str:
    status_rows = []
    for row in payload["results"]:
        status_code = normalize_status(row.get("status_code") or row.get("status"), row.get("value"), row.get("reference_range"))
        status_rows.append(
            "| {parameter} | {value} | {unit} | {reference_range} | {status} |".format(
                parameter=markdown_cell(str(row.get("parameter") or "").strip()),
                value=markdown_cell(row.get("value")),
                unit=markdown_cell(row.get("unit")),
                reference_range=markdown_cell(row.get("reference_range")),
                status=display_status(status_code),
            )
        )
    institution = str(payload.get("institution") or "")
    notes = str(payload.get("notes") or "")
    lines = [
        f"# {payload['test_type']}",
        "",
        f"**Дата:** {payload['test_date']}",
        f"**Пациент:** {payload['patient_full_name']}",
        f"**Источник:** {source_filename}",
        f"**Учреждение:** {institution}",
        "",
        "## Показатели",
        "",
        "| Параметр | Значение | Ед. изм. | Норма | Статус |",
        "| :------- | :------- | :------- | :---- | :----- |",
        *status_rows,
        "",
        "## Примечания из документа",
        "",
        notes,
        "",
        "---",
        f"*Обработано автоматически: {now_iso()}*",
        "",
    ]
    return "\n".join(lines)


def cmd_init(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="init")
    parser.parse_args(argv)
    root = get_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "Raw files").mkdir(parents=True, exist_ok=True)
    with connect(root) as conn:
        migrate_schema(conn)
    return {"ok": True, "status": "initialized", "root": root, "db": db_path(root)}


def cmd_migrate(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="migrate")
    parser.parse_args(argv)
    root = get_root()
    root.mkdir(parents=True, exist_ok=True)
    backup = backup_db(root)
    with connect(root) as conn:
        migrate_schema(conn)
    return {"ok": True, "status": "migrated", "backup": backup, "db": db_path(root)}


def cmd_add_patient(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="add-patient")
    parser.add_argument("--full-name", required=True)
    args = parser.parse_args(argv)
    root = get_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "Raw files").mkdir(parents=True, exist_ok=True)
    full_name = " ".join(args.full_name.split())
    short_name, folder = patient_names(full_name)
    patient_dir = root / folder / "Documents"
    with connect(root) as conn:
        migrate_schema(conn)
        existing = patient_by_name(conn, full_name)
        if existing:
            patient_dir.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "status": "exists", "patient": dict(existing)}
        folder_owner = conn.execute("SELECT * FROM patients WHERE folder = ?", (folder,)).fetchone()
        if folder_owner:
            raise ValidationError(f"folder already belongs to another patient: {folder}")
        if patient_dir.exists():
            raise ValidationError(f"patient folder already exists but is not registered: {folder}")
        conn.execute(
            """
            INSERT INTO patients (full_name, short_name, folder, added_date)
            VALUES (?, ?, ?, ?)
            """,
            (full_name, short_name, folder, today_iso()),
        )
        patient = patient_by_name(conn, full_name)
    patient_dir.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "status": "created", "patient": dict(patient)}


def cmd_validate_json(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="validate-json")
    parser.add_argument("--payload", required=True)
    args = parser.parse_args(argv)
    payload = load_payload(args.payload)
    validate_payload_shape(payload, require_source=True)
    return {"ok": True, "status": "valid"}


def insert_results(conn: sqlite3.Connection, document_id: int, payload: dict[str, Any]) -> None:
    for index, row in enumerate(payload["results"], start=1):
        status_code = normalize_status(row.get("status_code") or row.get("status"), row.get("value"), row.get("reference_range"))
        numeric_value = parse_number(row.get("value"))
        numeric_min, numeric_max = parse_reference_range(row.get("reference_range"))
        conn.execute(
            """
            INSERT INTO test_results (
                document_id, parameter, value, unit, reference_range, status, status_code,
                row_order, panel, material, method, numeric_value, numeric_min, numeric_max
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                str(row.get("parameter") or "").strip(),
                str(row.get("value") or ""),
                str(row.get("unit") or ""),
                str(row.get("reference_range") or ""),
                display_status(status_code),
                status_code,
                int(row.get("row_order") or index),
                row.get("panel"),
                row.get("material"),
                row.get("method"),
                numeric_value,
                numeric_min,
                numeric_max,
            ),
        )


def cmd_ingest_json(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="ingest-json")
    parser.add_argument("--payload", required=True)
    args = parser.parse_args(argv)
    payload = load_payload(args.payload)
    validate_payload_shape(payload, require_source=True)
    root = get_root()
    source_abs = validate_source_file(str(payload["source_path"]), root)
    source_filename = source_abs.name
    checksum = sha256_file(source_abs)

    with connect(root) as conn:
        migrate_schema(conn)
        patient = patient_by_name(conn, str(payload["patient_full_name"]))
        if not patient:
            raise ValidationError(f"patient is not registered: {payload['patient_full_name']}")
        by_checksum = document_by_checksum(conn, checksum)
        if by_checksum:
            return {
                "ok": True,
                "status": "exists",
                "document": {
                    "id": by_checksum["id"],
                    "file_path": by_checksum["file_path"],
                },
            }
        md_path, final_source_path, type_slug, _stem = target_paths(root, patient, payload)
        file_path = root_relative(md_path, root)
        source_file_path = root_relative(final_source_path, root)
        if document_by_file_path(conn, file_path) or md_path.exists() or final_source_path.exists():
            md_path, final_source_path, type_slug, _stem = target_paths(root, patient, payload, checksum[:8])
            file_path = root_relative(md_path, root)
            source_file_path = root_relative(final_source_path, root)
            if document_by_file_path(conn, file_path) or md_path.exists() or final_source_path.exists():
                raise ValidationError(f"target file already exists without index: {file_path}")

        md_path.parent.mkdir(parents=True, exist_ok=True)
        review_status = str(payload.get("review_status") or "needs_review")
        if review_status not in {"verified", "needs_review", "imported"}:
            review_status = "needs_review"
        if any(normalize_status(row.get("status_code") or row.get("status"), row.get("value"), row.get("reference_range")) == "unknown" for row in payload["results"]):
            review_status = "needs_review"

        md_tmp = md_path.with_name(f".{md_path.name}.tmp-{os.getpid()}")
        source_tmp = final_source_path.with_name(f".{final_source_path.name}.tmp-{os.getpid()}")
        moved_source = False
        md_final_created = False
        source_final_created = False
        try:
            md_tmp.write_text(markdown_for_payload(payload, source_filename), encoding="utf-8")
            shutil.move(str(source_abs), str(source_tmp))
            moved_source = True
            os.replace(md_tmp, md_path)
            md_final_created = True
            os.replace(source_tmp, final_source_path)
            source_final_created = True
            with conn:
                conn.execute(
                    """
                    INSERT INTO documents (
                        patient_id, test_date, test_type, file_path, source_filename,
                        source_checksum, institution, processed_at, review_status, parser_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        patient["id"],
                        payload["test_date"],
                        payload["test_type"],
                        file_path,
                        source_filename,
                        checksum,
                        payload.get("institution"),
                        now_iso(),
                        review_status,
                        PARSER_VERSION,
                    ),
                )
                document = document_by_file_path(conn, file_path)
                insert_results(conn, int(document["id"]), payload)
        except Exception:
            if md_tmp.exists():
                md_tmp.unlink()
            if md_final_created and md_path.exists():
                md_path.unlink()
            if source_final_created and final_source_path.exists():
                if not source_abs.exists():
                    shutil.move(str(final_source_path), str(source_abs))
                else:
                    final_source_path.unlink()
            if source_tmp.exists() and moved_source:
                shutil.move(str(source_tmp), str(source_abs))
            raise

    return {
        "ok": True,
        "status": "created",
        "document": {
            "file_path": file_path,
            "source_path": source_file_path,
            "review_status": review_status,
            "type_slug": type_slug,
        },
    }


def count_query(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def cmd_health(argv: list[str]) -> dict[str, Any]:
    parser = CliArgumentParser(prog="health")
    parser.parse_args(argv)
    root = get_root()
    result: dict[str, Any] = {
        "ok": True,
        "root": root,
        "db_exists": db_path(root).exists(),
        "raw_queue": 0,
        "checks": {},
        "counts": {},
    }
    raw_dir = root / "Raw files"
    if raw_dir.exists():
        result["raw_queue"] = len([path for path in raw_dir.iterdir() if path.is_file()])
    if not result["db_exists"]:
        result["ok"] = False
        result["checks"]["db"] = "missing"
        return result
    with connect(root, enable_wal=False) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["checks"]["integrity_check"] = integrity
        result["checks"]["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        result["checks"]["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        result["counts"]["patients"] = count_query(conn, "SELECT COUNT(*) FROM patients")
        result["counts"]["documents"] = count_query(conn, "SELECT COUNT(*) FROM documents")
        result["counts"]["test_results"] = count_query(conn, "SELECT COUNT(*) FROM test_results")
        result["checks"]["documents_without_patient"] = count_query(
            conn,
            "SELECT COUNT(*) FROM documents d LEFT JOIN patients p ON p.id = d.patient_id WHERE p.id IS NULL",
        )
        result["checks"]["results_without_document"] = count_query(
            conn,
            "SELECT COUNT(*) FROM test_results r LEFT JOIN documents d ON d.id = r.document_id WHERE d.id IS NULL",
        )
        result["checks"]["bad_document_dates"] = count_query(
            conn,
            "SELECT COUNT(*) FROM documents WHERE test_date IS NULL OR test_date NOT GLOB '????-??-??'",
        )
        missing_files = 0
        for row in conn.execute("SELECT file_path FROM documents"):
            if not (root / row["file_path"]).is_file():
                missing_files += 1
        result["checks"]["missing_document_files"] = missing_files
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        result["checks"]["schema_version"] = version
    if integrity != "ok" or any(
        result["checks"].get(key, 0)
        for key in (
            "documents_without_patient",
            "results_without_document",
            "bad_document_dates",
            "missing_document_files",
        )
    ):
        result["ok"] = False
    return result


COMMANDS = {
    "init": cmd_init,
    "migrate": cmd_migrate,
    "add-patient": cmd_add_patient,
    "validate-json": cmd_validate_json,
    "ingest-json": cmd_ingest_json,
    "health": cmd_health,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: medical_archive.py <init|migrate|health|add-patient|validate-json|ingest-json> [args...]")
        return 0
    command = argv.pop(0)
    handler = COMMANDS.get(command)
    if not handler:
        emit({"ok": False, "error": f"Unknown command: {command}", "error_type": "ValidationError"})
        return 2
    try:
        emit(handler(argv))
        return 0
    except MedicalArchiveError as exc:
        emit({"ok": False, "error": str(exc), "error_type": exc.__class__.__name__})
        return 1
    except Exception as exc:
        emit({"ok": False, "error": str(exc), "error_type": exc.__class__.__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
