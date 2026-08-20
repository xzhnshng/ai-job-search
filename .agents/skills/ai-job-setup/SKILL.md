---
name: ai-job-setup
version: 0.1.0
description: >
  Set up or inspect the local AI-Driven Job Search workspace, database, storage,
  and career-evidence onboarding status. Use when the user asks to initialize
  the product, upload or import a CV, add project evidence, inspect setup, or
  prepare the workspace for first use.
context: fork
enabled: true
---

# AI-Driven Job Search Setup

Use the typed local CLI for state and report the result clearly.

Current foundation operations:

```text
PYTHONPATH=src python3 -m ai_job_search --json db status
PYTHONPATH=src python3 -m ai_job_search --json db init
PYTHONPATH=src python3 -m ai_job_search --json storage status
```

Rules:

- Treat `.ai-job-search/` as private.
- Never put real user data in tests or Git.
- Never edit SQLite directly.
- Explain which requested onboarding capability is implemented.
- Do not claim that CV/project ingestion is available until its CLI command and
  schema have landed.
- When ingestion is available, preview extracted facts and require approval
  before committing them.
