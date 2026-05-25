#!/usr/bin/env python3
"""Self-tests for the medical archive CLI.

Run from the skill root:
    python3 scripts/medical_archive_selftest.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import medical_archive


class MedicalArchiveCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "archive"
        os.environ["MEDICAL_ARCHIVE_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.root / "metrics.db")
        conn.row_factory = sqlite3.Row
        return conn

    def valid_payload(self, source_path: str = "Raw files/cbc.pdf", value: str = "145") -> dict:
        return {
            "patient_full_name": "Петров Пётр Петрович",
            "test_date": "2026-05-08",
            "test_type": "Общий анализ крови",
            "type_slug": "CBC",
            "institution": "Test Lab",
            "source_path": source_path,
            "review_status": "verified",
            "results": [
                {
                    "parameter": "Гемоглобин",
                    "value": value,
                    "unit": "г/л",
                    "reference_range": "130-160",
                    "status": "OK",
                }
            ],
            "notes": "Комментарий врача из документа.",
        }

    def write_payload(self, payload: dict, name: str = "payload.json") -> Path:
        payload_path = self.root / name
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload_path

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["MEDICAL_ARCHIVE_ROOT"] = str(self.root)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name("medical_archive.py")), *args],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_init_and_add_patient_create_schema_and_folder(self) -> None:
        medical_archive.cmd_init([])
        result = medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["patient"]["folder"], "PetrovPP")
        self.assertTrue((self.root / "PetrovPP" / "Documents").is_dir())

        duplicate = medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        self.assertEqual(duplicate["status"], "exists")

        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 1)
            version = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            self.assertEqual(version, 2)

    def test_validate_json_rejects_missing_required_fields(self) -> None:
        medical_archive.cmd_init([])
        payload_path = self.root / "missing.json"
        payload_path.write_text(json.dumps({"patient_full_name": "Иванов Иван Иванович"}), encoding="utf-8")

        with self.assertRaises(medical_archive.ValidationError) as ctx:
            medical_archive.cmd_validate_json(["--payload", str(payload_path)])

        self.assertIn("test_date", str(ctx.exception))
        self.assertIn("test_type", str(ctx.exception))
        self.assertIn("results", str(ctx.exception))

    def test_ingest_json_writes_files_and_prevents_duplicate(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])

        raw_dir = self.root / "Raw files"
        source = raw_dir / "cbc.pdf"
        source.write_bytes(b"not a real pdf, just a fixture")
        payload_path = self.write_payload(self.valid_payload())

        result = medical_archive.cmd_ingest_json(["--payload", str(payload_path)])

        self.assertEqual(result["status"], "created")
        md_path = self.root / result["document"]["file_path"]
        raw_path = self.root / result["document"]["source_path"]
        self.assertTrue(md_path.is_file())
        self.assertTrue(raw_path.is_file())
        self.assertFalse(source.exists())
        self.assertIn("Гемоглобин", md_path.read_text(encoding="utf-8"))

        source.write_bytes(b"not a real pdf, just a fixture")
        duplicate = medical_archive.cmd_ingest_json(["--payload", str(payload_path)])
        self.assertEqual(duplicate["status"], "exists")

        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            row = conn.execute(
                "SELECT value, unit, reference_range, status_code, numeric_value, numeric_min, numeric_max "
                "FROM test_results"
            ).fetchone()
            self.assertEqual(row["value"], "145")
            self.assertEqual(row["unit"], "г/л")
            self.assertEqual(row["reference_range"], "130-160")
            self.assertEqual(row["status_code"], "ok")
            self.assertEqual(row["numeric_value"], 145.0)
            self.assertEqual(row["numeric_min"], 130.0)
            self.assertEqual(row["numeric_max"], 160.0)

    def test_ingest_rollback_removes_final_files_and_restores_source_after_db_failure(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        source = self.root / "Raw files" / "cbc.pdf"
        source.write_bytes(b"fixture")
        payload_path = self.write_payload(self.valid_payload())

        original_insert_results = medical_archive.insert_results

        def fail_insert_results(conn: sqlite3.Connection, document_id: int, payload: dict) -> None:
            raise RuntimeError("simulated DB failure")

        medical_archive.insert_results = fail_insert_results
        try:
            with self.assertRaises(RuntimeError):
                medical_archive.cmd_ingest_json(["--payload", str(payload_path)])
        finally:
            medical_archive.insert_results = original_insert_results

        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), b"fixture")
        self.assertFalse(list((self.root / "PetrovPP").rglob("*.md")))
        self.assertFalse(list((self.root / "PetrovPP").rglob("*.pdf")))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0], 0)

    def test_ingest_rejects_source_outside_raw_files(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        outside = self.root / "payload-source.pdf"
        outside.write_bytes(b"fixture")
        payload_path = self.write_payload(self.valid_payload(source_path="payload-source.pdf"))

        with self.assertRaises(medical_archive.ValidationError) as ctx:
            medical_archive.cmd_ingest_json(["--payload", str(payload_path)])

        self.assertIn("Raw files", str(ctx.exception))
        self.assertTrue(outside.is_file())

    def test_ingest_rejects_disallowed_source_extension(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        source = self.root / "Raw files" / "cbc.txt"
        source.write_bytes(b"fixture")
        payload_path = self.write_payload(self.valid_payload(source_path="Raw files/cbc.txt"))

        with self.assertRaises(medical_archive.ValidationError) as ctx:
            medical_archive.cmd_ingest_json(["--payload", str(payload_path)])

        self.assertIn("unsupported source extension", str(ctx.exception))
        self.assertTrue(source.is_file())

    def test_same_date_type_different_checksums_get_checksum_suffix(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        source1 = self.root / "Raw files" / "cbc-1.pdf"
        source2 = self.root / "Raw files" / "cbc-2.pdf"
        source1.write_bytes(b"first fixture")
        source2.write_bytes(b"second fixture")

        first = medical_archive.cmd_ingest_json(["--payload", str(self.write_payload(self.valid_payload("Raw files/cbc-1.pdf"), "p1.json"))])
        second = medical_archive.cmd_ingest_json(["--payload", str(self.write_payload(self.valid_payload("Raw files/cbc-2.pdf"), "p2.json"))])

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        self.assertNotEqual(first["document"]["file_path"], second["document"]["file_path"])
        self.assertRegex(second["document"]["file_path"], r"_[0-9a-f]{8}\.md$")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)

    def test_validate_json_requires_source_path(self) -> None:
        medical_archive.cmd_init([])
        payload = self.valid_payload()
        payload.pop("source_path")
        payload_path = self.write_payload(payload)

        with self.assertRaises(medical_archive.ValidationError) as ctx:
            medical_archive.cmd_validate_json(["--payload", str(payload_path)])

        self.assertIn("source_path", str(ctx.exception))

    def test_strict_reference_range_equality_is_out_of_range(self) -> None:
        self.assertEqual(medical_archive.normalize_status(None, "5.0", "< 5.0"), "high")
        self.assertEqual(medical_archive.normalize_status(None, "1.0", "> 1.0"), "low")
        self.assertEqual(medical_archive.normalize_status(None, "5.0", "менее 5.0"), "high")
        self.assertEqual(medical_archive.normalize_status(None, "1.0", "более 1.0"), "low")

    def test_explicit_unknown_status_is_preserved(self) -> None:
        self.assertEqual(medical_archive.normalize_status("unknown", "6.7", "4.2-5.5"), "unknown")
        self.assertEqual(medical_archive.normalize_status("❓", "6.7", "4.2-5.5"), "unknown")

    def test_bad_row_order_returns_json_error_from_cli(self) -> None:
        medical_archive.cmd_init([])
        medical_archive.cmd_add_patient(["--full-name", "Петров Пётр Петрович"])
        source = self.root / "Raw files" / "cbc.pdf"
        source.write_bytes(b"fixture")
        payload = self.valid_payload()
        payload["results"][0]["row_order"] = "not-an-int"
        payload_path = self.write_payload(payload)

        result = self.run_cli("ingest-json", "--payload", str(payload_path))

        self.assertNotEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertFalse(parsed["ok"])
        self.assertIn("row_order", parsed["error"])
        self.assertTrue(source.is_file())

    def test_markdown_table_cells_are_escaped(self) -> None:
        payload = self.valid_payload(value="line 1 | line 2\nline 3\\tail")
        markdown = medical_archive.markdown_for_payload(payload, "source.pdf")

        self.assertIn("line 1 \\| line 2<br>line 3\\\\tail", markdown)

    def test_migrate_v1_preserves_counts_and_adds_v2_columns(self) -> None:
        self.root.mkdir(parents=True)
        with sqlite3.connect(self.root / "metrics.db") as conn:
            conn.executescript(
                """
                CREATE TABLE patients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT UNIQUE,
                    short_name TEXT,
                    folder TEXT,
                    added_date DATE
                );
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER,
                    test_date DATE,
                    test_type TEXT,
                    file_path TEXT UNIQUE,
                    FOREIGN KEY(patient_id) REFERENCES patients(id)
                );
                CREATE TABLE test_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    parameter TEXT,
                    value TEXT,
                    unit TEXT,
                    reference_range TEXT,
                    status TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(id),
                    UNIQUE(document_id, parameter)
                );
                INSERT INTO patients (full_name, short_name, folder, added_date)
                VALUES ('Иванов Иван Иванович', 'Иванов', 'IvanovII', '2026-01-01');
                INSERT INTO documents (patient_id, test_date, test_type, file_path)
                VALUES (1, '2026-01-02', 'ОАК', 'IvanovII/Documents/2026/January/02-01-2026_Ivanov_CBC.md');
                INSERT INTO test_results (document_id, parameter, value, unit, reference_range, status)
                VALUES (1, 'Гемоглобин', '145', 'г/л', '130-160', '✅ OK');
                """
            )

        result = medical_archive.cmd_migrate([])

        self.assertEqual(result["status"], "migrated")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0], 1)
            doc = conn.execute("SELECT review_status, parser_version FROM documents").fetchone()
            self.assertEqual(doc["review_status"], "imported")
            self.assertEqual(doc["parser_version"], "legacy-v1")
            status_code = conn.execute("SELECT status_code FROM test_results").fetchone()[0]
            self.assertEqual(status_code, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
