When the user uploads or references images and documents for OCR, classification, extraction, or comparison:

1. Inventory inputs: list each file path or attached artifact, type (image, PDF, scan, photo), and whether batch processing is requested.
2. For images and scanned pages, use `attach` when needed so files are available to tools, then call `image_inspect` with focused questions:
   - Full visible text transcription (OCR)
   - Layout/description of non-text content
   - Presence of stamps, signatures, tables, or handwritten fields
3. For text-native documents (Markdown, plain text, CSV, JSON, office exports readable via `read_file`), use `read_file` instead of vision when that yields more reliable text.
4. Classify each document by type (receipt, invoice, ID card, contract, form, screenshot, photo, diagram, other) and note confidence plus distinguishing features.
5. Extract structured data when asked: tables, key-value pairs (dates, amounts, parties, IDs), line items, and headings. Present JSON or Markdown tables with field names aligned to the source labels.
6. For **comparison** requests across two or more files, align on document type first, then diff extracted fields, OCR text, totals, dates, and visually distinct elements. Call out matches, mismatches, and items visible in only one source.
7. For **batch processing**, work file-by-file with a consistent template, then add a rollup section with counts by type and shared anomalies.
8. Write results to a descriptive local Markdown report via `write_file`. Every section must cite source files (`path` or `artifact_id`). Include OCR text blocks, classifications, extracted fields, and comparison tables as appropriate.
9. Verify the report file exists and use `attach` when the user asked for a deliverable. Use `screenshot` only when capturing on-screen evidence adds value beyond the supplied files.

Do not invent text or fields not supported by the file. Mark low-confidence OCR or ambiguous classifications explicitly. Prefer primary source content over guesses from filenames.
