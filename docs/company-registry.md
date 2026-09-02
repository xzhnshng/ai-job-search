# Target-Company Registry

**Status:** Baseline polling implemented; recurring scheduling pending
**Scope:** Source-bound approval, registry inspection, and recorded source health

## Purpose

The company registry converts user-authored technology and trading plans into
durable SQLite records. It preserves the ranking and recommendation as user
planning input; it does not present those values as independently verified
company facts.

The registry is the control plane for future official career-source
monitoring. A company is not described as monitored until it has a confirmed
official source and successful polling history.

## Commands

Run from the repository root until the package is installed:

```text
PYTHONPATH=src python3 -m ai_job_search --json companies plan-list
PYTHONPATH=src python3 -m ai_job_search --json companies plan-show <proposal-id>
PYTHONPATH=src python3 -m ai_job_search --json companies plan-approve \
  <proposal-id> --actor candidate --reason <review-note>
PYTHONPATH=src python3 -m ai_job_search --json companies list
PYTHONPATH=src python3 -m ai_job_search --json companies list --market technology
PYTHONPATH=src python3 -m ai_job_search --json companies show <company-id>
PYTHONPATH=src python3 -m ai_job_search --json companies source-propose \
  --company <id-or-exact-name> --career-url <https-url> \
  --evidence-url <official-page> --detection-note <review-note>
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
PYTHONPATH=src python3 -m ai_job_search --json jobs list
PYTHONPATH=src python3 -m ai_job_search --json jobs list \
  --classification verified_new \
  --classification newly_detected_date_unknown
PYTHONPATH=src python3 -m ai_job_search --json jobs show <job-id>
PYTHONPATH=src python3 -m ai_job_search --json daily report \
  --since <ISO-8601> [--before <ISO-8601>]
```

`plan-list` reports whether each proposal still matches the current source
hash. Stale proposals remain reviewable history but cannot be approved.

## Approval behavior

Approval is transactional and market-specific:

- the proposal must be pending and source-current;
- company names and ranks must be unique inside the market;
- technology and trading ranks remain independent;
- existing canonical companies are reused;
- entries removed from a later full-market plan are deactivated, not deleted;
- applications linked to a company are never removed;
- the proposal decision and approval event commit with the registry changes;
- a failed validation leaves both proposal and registry unchanged.

## Current local state

The approved local registry contains:

- 38 technology targets;
- 16 trading targets;
- 54 total active target entries;
- two monitored companies with healthy, enabled Ashby sources: Etched and
  Fireworks AI;
- one disabled historical Fireworks custom-page source retained as detection
  evidence;
- 174 official roles imported by the first successful polls: 108 from Etched
  and 66 from Fireworks AI;
- all 174 first-poll roles classified `baseline_existing`; zero were described
  as newly released;
- the current daily report contains zero reportable findings because baseline
  roles are intentionally excluded;
- 52 targets without a registered official source.

The source Markdown files, SQLite database, proposals, and extracted caches are
local and Git-ignored. Only schema, deterministic services, documentation, and
synthetic tests belong in Git.

## Next implementation boundary

Official career URLs now enter through a separate proposal and confirmation
workflow. Registration:

- requires HTTPS and rejects credentials, local/private IPs, custom ports,
  query strings, and fragments;
- infers Ashby, Greenhouse, Lever, and SmartRecruiters from known ATS hosts;
- records the official page used as evidence inside the immutable proposal;
- rejects approval if the target-company plan changed after proposal creation;
- creates the source as disabled with `not_checked` health.

Bounded health checking now:

- resolves only public IP addresses over HTTPS;
- allowlists the registered or adapter-specific host;
- controls redirects and rejects cross-host escape;
- caps request time and response size;
- validates content type and supported ATS response shape;
- records both success and failure in `source_run` and `source_health_event`;
- never enables polling automatically.

Explicit source enablement and the first Ashby observation adapter are now
implemented and have established real baselines for Etched and Fireworks AI.
Enablement requires fresh healthy evidence and approval; a newer health result
makes a pending proposal stale. The first successful poll creates only
`baseline_existing` freshness records. Later complete snapshots distinguish new,
changed, closed, and reopened official IDs while preserving history.

The next boundary is completing freshness edge cases and read-only daily report
generation. The first JSON daily-report contract is available and excludes
baseline jobs, but ranking and scheduling are not yet active.
