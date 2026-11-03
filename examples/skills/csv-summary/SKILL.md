---
name: csv-summary
description: Computes summary statistics (row count, per-column min, max, mean, missing values) for a CSV file and writes them as JSON. Use when the user asks to summarize, profile or describe a CSV or spreadsheet export.
---

# CSV summary

Always use the bundled script; do not compute statistics by hand.

1. Run: `python3 ${CLAUDE_SKILL_DIR}/scripts/summarize.py <input.csv> <output.json>`
   - Default output path: `summary.json` next to the input.
2. If the script exits non-zero, show its error message and stop.
3. Reply with a two-sentence plain-language summary of the JSON (row count and the most notable column).

The JSON format is described in [the format reference](references/format.md).
