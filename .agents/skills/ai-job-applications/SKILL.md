---
name: ai-job-applications
version: 0.1.0
description: >
  View, update, filter, export, or inspect the AI-Driven Job Search application
  tracker, including interviews, deadlines, outcomes, offers, and salary.
  Use for any request about tracked applications or their current status.
context: fork
enabled: true
---

# AI-Driven Job Search Application Tracker

The database-backed tracker is unavailable until Slice 1 passes. Until then,
preserve `job_search_tracker.csv` and do not create a second authority.

When implemented:

- query through the typed CLI;
- express updates as previewed proposals;
- keep application events append-only;
- preserve corrections instead of deleting history;
- export Excel as a view, not the source of truth;
- never interpret a missing spreadsheet row as deletion.
