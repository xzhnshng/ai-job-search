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

Target-company plan import, approval, and registry inspection are available.
Daily discovery and official-source polling are not available until the
remaining monitoring and daily-intelligence work passes.

Current plan operations:

```text
PYTHONPATH=src python3 -m ai_job_search --json companies plan-import \
  --path <documents-path> --source-id <source-id> --market technology|trading
PYTHONPATH=src python3 -m ai_job_search --json companies plan-show <proposal-id>
PYTHONPATH=src python3 -m ai_job_search --json companies plan-list \
  [--state pending|approved|rejected] [--market technology|trading]
PYTHONPATH=src python3 -m ai_job_search --json companies plan-approve \
  <proposal-id> --actor candidate --reason <review-note>
PYTHONPATH=src python3 -m ai_job_search --json companies list \
  [--market technology|trading]
PYTHONPATH=src python3 -m ai_job_search --json companies show <company-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-propose \
  --company <id-or-exact-name> --career-url <https-url> \
  --evidence-url <official-page> --detection-note <review-note> \
  [--adapter ashby|greenhouse|lever|smartrecruiters|custom|unsupported] \
  [--tier A|B|C]
PYTHONPATH=src python3 -m ai_job_search --json companies source-proposal-show \
  <proposal-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-approve \
  <proposal-id> --actor candidate --reason <review-note>
PYTHONPATH=src python3 -m ai_job_search --json companies source-health-check \
  <company-source-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-detect \
  <company-source-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-enable-propose \
  <company-source-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-enable-proposal-show \
  <proposal-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-enable-approve \
  <proposal-id> --actor candidate --reason <review-note>
PYTHONPATH=src python3 -m ai_job_search --json companies source-poll \
  <company-source-id>
PYTHONPATH=src python3 -m ai_job_search --json jobs list \
  [--classification <freshness-class>] [--classified-since <ISO-8601>]
PYTHONPATH=src python3 -m ai_job_search --json jobs show <job-id>
PYTHONPATH=src python3 -m ai_job_search --json daily report \
  --since <ISO-8601> [--before <ISO-8601>]
```

Source approval registers the URL as disabled with `not_checked` health. A
health check records bounded HTTP and schema evidence but never enables the
source. Enablement itself is proposal-gated. The first poll is always baseline
and must never be reported as newly released. Do not claim recurring monitoring
is active until enablement, a successful baseline, and scheduling all exist.

The current daily report is read-only and intentionally unranked. It excludes
`baseline_existing`, includes source coverage and health, and must warn that an
empty report is not evidence of no openings when sources are disabled or
unhealthy.

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
