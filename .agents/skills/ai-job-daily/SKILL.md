---
name: ai-job-daily
version: 0.2.0
description: >
  Run or inspect the AI-Driven Job Search daily monitoring and ranked job report.
  Use for daily job search, new-job checks, source health, target-company
  monitoring, freshness, ranking, deduplication, or scheduled search requests.
context: fork
enabled: true
---

# AI-Driven Job Search Daily Workflow

Target-company plan import is available. Daily discovery and official-source
monitoring are not available until implementation Slices 5 and 6 pass.

Current plan operations:

```text
PYTHONPATH=src python3 -m ai_job_search --json companies plan-import \
  --path <documents-path> --source-id <source-id> --market technology|trading
PYTHONPATH=src python3 -m ai_job_search --json companies plan-show <proposal-id>
```

Keep rankings separate by market unless the user explicitly defines a shared
scoring model. Treat ranks, fit, income, ML opportunity, and recommendation as
user planning inputs rather than verified company facts.
Until then:

1. inspect the imported company-plan proposals and database/storage health if useful;
2. use installed portal-search skills only when the user explicitly asks for a
   live search;
3. label those results supplemental, not authoritative-new;
4. do not create applications automatically;
5. do not claim a scheduled monitor exists.

When implemented, call the daily CLI through `--json`, show source failures and
freshness confidence, and persist review actions only after user direction.
