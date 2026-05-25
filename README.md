# Andromeda Medical

Andromeda Medical is an OpenClaw skill for AI agents that maintain a local medical archive. It helps agents register patients, extract lab results from PDFs and images, save Markdown records, preserve original source files, index results in SQLite, run archive health checks, and answer trend/deviation questions from stored data.

The skill keeps the agent responsible for OCR and medical-data extraction, while `scripts/medical_archive.py` performs deterministic validation, file moves, Markdown generation, duplicate checks, schema migration, and SQLite writes.

## Safety Model

- Do not diagnose, prescribe, or infer treatment.
- Compare values only against reference ranges printed in the source document.
- Preserve raw `value`, `unit`, and `reference_range` fields exactly as extracted.
- Store medical data only under `MEDICAL_ARCHIVE_ROOT`.
- Do not send medical contents to external services beyond the active OpenClaw model/tool routing.

## Requirements

- OpenClaw skill runtime
- `python3`
- `sqlite3`
- `MEDICAL_ARCHIVE_ROOT` environment variable

## Usage

Install or copy this repository as an OpenClaw skill, then point `MEDICAL_ARCHIVE_ROOT` at the local archive directory.

Core commands:

```bash
python3 {baseDir}/scripts/medical_archive.py init
python3 {baseDir}/scripts/medical_archive.py health
python3 {baseDir}/scripts/medical_archive.py add-patient --full-name "Ivanov Ivan Ivanovich"
python3 {baseDir}/scripts/medical_archive.py validate-json --payload "$MEDICAL_ARCHIVE_ROOT/payload.json"
python3 {baseDir}/scripts/medical_archive.py ingest-json --payload "$MEDICAL_ARCHIVE_ROOT/payload.json"
python3 {baseDir}/scripts/medical_archive.py migrate
```

See:

- `SKILL.md` for agent instructions
- `references/extraction.md` for extraction rules
- `references/schema.md` for SQLite schema and invariants
- `references/workflows.md` for command workflows

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/medical_archive_selftest.py
python3 -m py_compile scripts/medical_archive.py scripts/medical_archive_selftest.py
```

## Contacts

- GitHub: https://github.com/andromeda8wave
- LinkedIn: https://www.linkedin.com/in/mikhail-pokoptsev/
- Telegram: https://t.me/andromeda8wave

## License

MIT-0. See `LICENSE`.
