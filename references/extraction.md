# Medical Archive Extraction

Use this reference when converting a PDF/image into the JSON payload consumed by `{baseDir}/scripts/medical_archive.py`.

## Required JSON Shape

```json
{
  "patient_full_name": "Фамилия Имя Отчество",
  "test_date": "2026-05-08",
  "test_type": "Общий анализ крови",
  "type_slug": "CBC",
  "institution": "Название лаборатории",
  "source_path": "Raw files/source.pdf",
  "review_status": "verified",
  "results": [
    {
      "parameter": "Гемоглобин",
      "value": "145",
      "unit": "г/л",
      "reference_range": "130-160",
      "status": "OK",
      "panel": "ОАК",
      "material": "кровь",
      "method": ""
    }
  ],
  "notes": "Комментарии, заключения и текст из документа, не вошедшие в таблицу."
}
```

## Extraction Rules

- Extract `patient_full_name` from fields such as `Пациент`, `ФИО`, `Ф.И.О.`.
- Extract `test_date` from document content first, then filename if needed. Convert only the date format to ISO `YYYY-MM-DD`.
- Detect test type from Russian or English keywords. Use `type_slug` for filenames when confident.
- Preserve raw `value`, `unit`, and `reference_range` exactly as printed. Do not convert units or normalize typography.
- If the document has repeated parameter names, disambiguate before ingestion: `IgG (Toxoplasma)`, `IgG (CMV)`, `Лейкоциты (кровь)`.
- If a value says `см.комм.`, `см. комм.`, or `see comment`, look for the actual value in the same row's comment/footnote. If absent, store the pointer as-is and use unknown status.
- Store descriptive conclusions in `notes`. For studies such as ultrasound, ECG, X-ray, MRI, or CT, also add one `results` row with `parameter: "Заключение"` and the conclusion in `value` when it is the only analyzable result.

## Status Rules

Only compare with reference ranges printed in the document.

- `OK` when the value is inside the document range.
- `HIGH` when the value is above the document range.
- `LOW` when the value is below the document range.
- `unknown` when no range exists, the range cannot be parsed, or the value is non-numeric and semantic comparison is uncertain.

Supported range patterns:

- `130-160`, `130–160`, `130 - 160`
- `< 5.0`, `менее 5.0`, `до 5.0`
- `> 1.0`, `более 1.0`, `от 1.0`

## Review Status

Use `review_status: "verified"` only when patient, date, type, source, and result rows are clear. Otherwise use `needs_review` or omit the field so the CLI stores it as `needs_review`.

Never fill uncertain values creatively. Leave them blank or mark the row/status as unknown.
