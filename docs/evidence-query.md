# Career Evidence Query and Eligibility

**Status:** Implemented foundation for Slice 2 evidence retrieval
**Scope:** Read-only claim inspection and selector-safe candidate retrieval

## Purpose

The normalized evidence database now has a read-only query boundary. Codex and
future selection services can retrieve candidate-approved claims without
reading SQLite directly or treating every stored statement as resume content.

This is a prerequisite for role-track matching and resume-plan selection. It
does not yet generate a resume plan or application document.

## Commands

Until the package is installed, run commands from the repository root with
`PYTHONPATH=src`:

```text
PYTHONPATH=src python3 -m ai_job_search --json evidence claims
PYTHONPATH=src python3 -m ai_job_search --json evidence claims --eligible-only
PYTHONPATH=src python3 -m ai_job_search --json evidence claims --query streaming
PYTHONPATH=src python3 -m ai_job_search --json evidence claims --project "Inference Platform"
PYTHONPATH=src python3 -m ai_job_search --json evidence claims --tag senior_distributed_systems_sde
PYTHONPATH=src python3 -m ai_job_search --json evidence claim-show <claim_id>
```

Available filters include:

- normalized keyword;
- project ID or project-name fragment;
- normalized skill or skill alias;
- project role tag;
- claim type;
- verification state;
- confidentiality label;
- final-application eligibility;
- bounded pagination.

The JSON result includes project and component context, metrics, normalized
skills, evidence lineage, and an explainable eligibility decision.

## Eligibility policy

`eligible_for_final_application` permits a claim only when all of the following
are true:

- it was candidate-approved (`approved`, with `verified` retained as the
  compatibility state written by the first approval release);
- it has at least one registered evidence link;
- linked metrics are approved and structurally valid;
- its confidentiality label is allowed for the target context;
- requested wording does not exceed the approved wording strength;
- it has not been superseded;
- its claim type is application evidence.

Collaboration-boundary claims are intentionally visible but ineligible. They
prevent the system from assigning teammate work to the candidate; they are not
resume accomplishments.

The default targeted-application context permits `public` and `private` claims
and blocks other labels. Callers must explicitly provide a different allowed
set. This does not constitute approval to disclose employer-confidential
details; a later generalization workflow must remain explicit and reviewed.

## Safety boundary

- Queries are read-only.
- Search never creates claims or fills evidence gaps.
- `--eligible-only` applies policy before pagination.
- Claim IDs and evidence links remain stable inputs to later resume plans.
- Local source paths and personal evidence remain inside the ignored private
  workspace and are never committed by this feature.

## Next dependency

Role tracks and the deterministic selector will consume this query service.
They must preserve every eligibility failure and unsupported requirement as an
explicit exclusion or gap before drafting begins.
