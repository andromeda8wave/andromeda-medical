# Medical Archive Schema

Use this reference for migrations, analytics, and database troubleshooting.

## Database Location

The SQLite database lives at `$MEDICAL_ARCHIVE_ROOT/metrics.db`. Use the CLI for mutations. For analytics, direct read-only `SELECT` queries are allowed.

Every mutating SQLite session must use:

```sql
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
```

## Version 2 Tables

### `schema_migrations`

Tracks applied schema versions.

| Column | Type | Notes |
| --- | --- | --- |
| `version` | INTEGER PRIMARY KEY | Current version is `2` |
| `applied_at` | TEXT | ISO timestamp |

### `patients`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Internal ID |
| `full_name` | TEXT UNIQUE NOT NULL | Original full name |
| `short_name` | TEXT | Transliteration folder-style short name |
| `folder` | TEXT NOT NULL | Patient folder, unique index |
| `added_date` | DATE NOT NULL | ISO date |

### `documents`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Internal ID |
| `patient_id` | INTEGER NOT NULL | FK to `patients.id` |
| `test_date` | DATE NOT NULL | `YYYY-MM-DD` |
| `test_type` | TEXT NOT NULL | Human-readable test type |
| `file_path` | TEXT UNIQUE NOT NULL | Markdown path relative to root |
| `source_filename` | TEXT | Original uploaded filename |
| `source_checksum` | TEXT | SHA-256 of source file; unique when present |
| `institution` | TEXT | Lab/clinic from document |
| `processed_at` | TEXT | ISO timestamp |
| `review_status` | TEXT | `verified`, `needs_review`, or `imported` |
| `parser_version` | TEXT | CLI/parser version |

### `test_results`

Raw fields must preserve the document exactly.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Internal ID |
| `document_id` | INTEGER NOT NULL | FK to `documents.id` |
| `parameter` | TEXT NOT NULL | Unique within one document |
| `value` | TEXT | Raw value exactly as extracted |
| `unit` | TEXT | Raw unit exactly as extracted |
| `reference_range` | TEXT | Raw range exactly as extracted |
| `status` | TEXT | Display status such as `✅ OK` |
| `status_code` | TEXT | `ok`, `high`, `low`, or `unknown` |
| `row_order` | INTEGER | Original row order |
| `panel` | TEXT | Optional panel/group |
| `material` | TEXT | Optional biological material |
| `method` | TEXT | Optional measurement method |
| `numeric_value` | REAL | Parsed helper for analytics only |
| `numeric_min` | REAL | Parsed helper for analytics only |
| `numeric_max` | REAL | Parsed helper for analytics only |

## Indexes And Invariants

- `documents.file_path` is unique.
- `documents.source_checksum` is unique when present.
- `test_results` has `UNIQUE(document_id, parameter)`.
- Analytics indexes exist on document patient/date and result parameter/status.
- Store document paths relative to `$MEDICAL_ARCHIVE_ROOT`, never absolute paths.
- Do not delete legacy rows during migration.

## Health Checks

Use:

```bash
python3 {baseDir}/scripts/medical_archive.py health
```

Expected healthy state:

- `integrity_check` is `ok`.
- `foreign_keys` is `1`.
- orphan counts are `0`.
- `missing_document_files` is `0`.
- `schema_version` is `2`.
