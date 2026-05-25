---
name: andromeda-medical
description: Use when an AI agent needs to manage a local medical archive, register patients, extract lab results from PDFs/images, save structured records, check archive health, migrate SQLite, or answer trend/deviation analytics from archived results.
metadata:
  openclaw:
    requires:
      env:
        - MEDICAL_ARCHIVE_ROOT
      bins:
        - python3
        - sqlite3
---

# Andromeda Medical Archive

Manage a local family medical archive at `$MEDICAL_ARCHIVE_ROOT`. Keep user-facing conversation in Russian unless the user chooses English. Use the local CLI for all database and file mutations; do not hand-write SQL mutations in chat.

## Critical Safety Rules

- Perform write operations only inside `$MEDICAL_ARCHIVE_ROOT`.
- If `$MEDICAL_ARCHIVE_ROOT` is missing, ask the user to set it. Do not guess.
- Never edit files under this skill folder as part of normal archive use.
- Do not diagnose, prescribe, or infer treatment. Describe only extracted facts and deviations from reference ranges printed in the source document.
- Do not use "commonly accepted norms" when deciding status. If the document has no usable reference range, use `unknown`.
- Preserve raw medical values exactly: do not convert units, round values, or normalize `value`, `unit`, or `reference_range`.
- Privacy caveat: if the active OpenClaw model is hosted by a provider, document contents may be processed by that provider. Do not send medical contents to any extra external services or tools beyond the configured OpenClaw model/tool routing.

## Files To Read When Needed

- Read `references/workflows.md` for command workflows and CLI usage.
- Read `references/extraction.md` before processing PDF/image contents or building an ingestion JSON payload.
- Read `references/schema.md` before writing analytics SQL, migrating, or troubleshooting database health.

## CLI First

Use `{baseDir}/scripts/medical_archive.py` as the canonical script path. If the runtime cannot expand `{baseDir}`, run the command from the installed skill root and use `scripts/medical_archive.py`.

```bash
python3 {baseDir}/scripts/medical_archive.py init
python3 {baseDir}/scripts/medical_archive.py health
python3 {baseDir}/scripts/medical_archive.py migrate
python3 {baseDir}/scripts/medical_archive.py add-patient --full-name "Фамилия Имя Отчество"
python3 {baseDir}/scripts/medical_archive.py validate-json --payload "$MEDICAL_ARCHIVE_ROOT/payload.json"
python3 {baseDir}/scripts/medical_archive.py ingest-json --payload "$MEDICAL_ARCHIVE_ROOT/payload.json"
```

The CLI returns JSON. Treat `ok: false` as a blocker and report the error to the user.

## Supported User Requests

- `добавить пользователя [ФИО]`: run `init`, then `add-patient`.
- `обработать файлы`: inspect `$MEDICAL_ARCHIVE_ROOT/Raw files/`, extract each PDF/image into JSON, run `validate-json`, then `ingest-json`.
- `добавить файл` or an attachment: place/stage the raw file under `$MEDICAL_ARCHIVE_ROOT/Raw files/`, extract JSON, show a preview if uncertain, then run `ingest-json`.
- `проиндексировать архив` or migration/repair requests: run `migrate`, then `health`.
- `покажи динамику`, `отклонения`, `что ухудшилось`, `что улучшилось`: read SQLite only; do not scan Markdown files for analytics.

## Ingestion Contract

The agent's role is OCR/extraction. The CLI's role is validation, deterministic filenames, Markdown generation, source-file movement, SQLite writes, duplicate checks, and health reporting.

Before calling `ingest-json`, create a UTF-8 JSON payload with:

- `patient_full_name`, `test_date` in `YYYY-MM-DD`, `test_type`, `source_path`, and non-empty `results`.
- `source_path` must point to a regular PDF/image file inside `$MEDICAL_ARCHIVE_ROOT/Raw files/`.
- Each result needs a unique `parameter` within the document plus raw `value`, `unit`, `reference_range`, and optional `status`.
- Use `review_status: "verified"` only when the extraction is clear. Otherwise omit it or use `needs_review`.

After successful ingestion, show the saved Markdown path and a concise extraction summary. For single-file requests, include the extracted table for user verification.

## Analytics Rules

Use `sqlite3 -header -column "$MEDICAL_ARCHIVE_ROOT/metrics.db" ...` with read-only `SELECT` queries. Join `patients`, `documents`, and `test_results`. Prefer `status_code`, `numeric_value`, `numeric_min`, and `numeric_max` for trend calculations, while displaying the raw `value`, `unit`, and `reference_range` back to the user.

If data is missing, ambiguous, or marked `needs_review`, say that plainly and avoid medical conclusions.
