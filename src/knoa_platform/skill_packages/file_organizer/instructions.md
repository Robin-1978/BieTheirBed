When the user asks to organize, clean up, sort, archive, or tidy files on their computer:

1. Confirm the target scope before scanning: root directory path, whether to include subdirectories, and any paths that must never be touched (system dirs, `.git`, active project roots, cloud-sync folders).
2. Scan the target directory with `run_command` (for example `find`, `du`, or platform-appropriate listing) and collect evidence: file counts by extension, total size per category, oldest/newest modification dates, and the largest files.
3. Identify cleanup candidates and group them into categories:
   - Duplicates (same name/size/hash where hash is affordable; otherwise size + name heuristics with uncertainty noted)
   - Temporary files (`*.tmp`, `*.temp`, `~*`, `.DS_Store`, `Thumbs.db`, browser/app caches when clearly safe)
   - Large or stale files that appear unused (old downloads, installers, oversized media/logs)
   - Misplaced files that belong in a clearer folder structure (Documents, Images, Archives, Projects, etc.)
4. Present a categorized plan **before making any changes**. For each category show counts, total size, example paths, proposed destination or action (move, archive, delete, keep), and risk level. Ask the user to confirm the full plan or adjust categories.
5. Do not move, rename, delete, or archive anything until the user explicitly approves the plan (all categories or a selected subset).
6. Execute approved actions with `run_command`, preferring reversible moves into an `_organized/` or dated archive folder over permanent deletion. Create destination folders with `run_command` or `write_file` as needed.
7. Write an undo log as a restore script (shell or platform-appropriate) that records every move/rename/delete with source and destination so changes can be reversed. Save it beside the organized output.
8. Generate a Markdown summary report listing what changed: files moved/removed/archived, space reclaimed, categories applied, skipped items, and the path to the restore script. Use `write_file` for the report.
9. Verify key outputs exist (restore script, summary report) and use `attach` when the user asked for a report or deliverable file.

Never delete without explicit user approval for that category. When evidence is incomplete, state uncertainty instead of guessing duplicate or safety status.
