# Medical Archive Workflows

Use this reference for user command handling. Use `{baseDir}/scripts/medical_archive.py` as the canonical script path. If the runtime cannot expand `{baseDir}`, run commands from the installed skill root and replace it with `scripts/medical_archive.py`.

## Initialize Or Check The Archive

```bash
python3 {baseDir}/scripts/medical_archive.py init
python3 {baseDir}/scripts/medical_archive.py health
```

Run `init` before the first write in a session. Run `health` after migrations or when the user asks for diagnostics.

## Add Patient

User examples:

- `добавить пользователя Иванов Иван Иванович`
- `зарегистрируй пациента ...`

Action:

```bash
python3 {baseDir}/scripts/medical_archive.py add-patient --full-name "Иванов Иван Иванович"
```

The CLI transliterates the folder name, checks duplicates, creates `<Folder>/Documents`, and returns JSON. If the CLI reports a folder conflict, ask the user before doing anything else.

## Process Batch Files

User examples:

- `обработать файлы`
- `разбери анализы из Raw files`

Action:

1. Run `health` and inspect `$MEDICAL_ARCHIVE_ROOT/Raw files/`.
2. For each supported PDF/image file, OCR/extract according to `references/extraction.md`.
3. Write one payload JSON under `$MEDICAL_ARCHIVE_ROOT`, for example `.medical-archive-payloads/<source-stem>.json`.
4. Run:

```bash
python3 {baseDir}/scripts/medical_archive.py validate-json --payload "$MEDICAL_ARCHIVE_ROOT/.medical-archive-payloads/file.json"
python3 {baseDir}/scripts/medical_archive.py ingest-json --payload "$MEDICAL_ARCHIVE_ROOT/.medical-archive-payloads/file.json"
```

5. Report created, skipped/existing, and blocked files. Ask follow-up questions for missing patient/date/type instead of guessing.

## Process One Attachment

User examples:

- `добавить файл`
- user sends a PDF/image attachment with a request to process it

Action:

1. Stage the raw file inside `$MEDICAL_ARCHIVE_ROOT/Raw files/`. Accepted extensions are `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, `.heic`, and `.heif`.
2. OCR/extract a JSON payload.
3. If extraction is uncertain, show the table preview and ask for correction.
4. Run `validate-json`, then `ingest-json`.
5. Show the saved Markdown path and extracted table.

## Migrate Or Reindex

User examples:

- `проиндексировать архив`
- `обнови схему медицинского архива`
- `проверь базу анализов`

Action:

```bash
python3 {baseDir}/scripts/medical_archive.py migrate
python3 {baseDir}/scripts/medical_archive.py health
```

The CLI creates a timestamped `Backups/metrics.db.before-v2-*.bak` before migration when a database exists. Do not restart the Gateway.

## Analytics

User examples:

- `покажи динамику [пациент] за год`
- `покажи отклонения`
- `что ухудшилось у [пациент]`

Use read-only SQLite queries. Do not read Markdown files for analytics. Example:

```sql
SELECT p.full_name, d.test_date, d.test_type, r.parameter,
       r.value, r.unit, r.reference_range, r.status_code,
       r.numeric_value, r.numeric_min, r.numeric_max, d.review_status
FROM test_results r
JOIN documents d ON d.id = r.document_id
JOIN patients p ON p.id = d.patient_id
WHERE p.full_name = '...'
ORDER BY r.parameter, d.test_date;
```

Response rules:

- Display raw values and units.
- Mention `needs_review` documents.
- Say "по референсам из документа" rather than implying diagnosis.
- Recommend consulting a clinician only for interpretation, not as a medical conclusion.
