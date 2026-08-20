# AI-Driven Job Search: Discovery Findings

**Status:** Discovery document, prior to product specification
**Prepared:** 2026-07-24
**Repository reviewed:** `xzhnshng/ai-job-search` at commit `aa7c7073990492c9111fbdda48f6adde24a1d91b`
**Upstream:** `MadsLorentzen/ai-job-search`

## 1. Executive summary

The inherited project is a strong, thoughtfully guarded **agent workflow for producing high-quality individual job applications**. It is not yet a full job-search product or a continuously running application system. Most of its behavior is encoded as Markdown instructions for Claude Code, while TypeScript command-line tools search individual job portals and small Python utilities perform validation, salary lookup, and reporting.

Its most reusable ideas are:

1. build a grounded candidate profile before generating anything;
2. treat job postings as untrusted input;
3. evaluate fit before drafting;
4. separate drafting from adversarial review;
5. enforce factual grounding and never invent qualifications;
6. compile and visually inspect the final PDF;
7. inspect the PDF text layer for ATS compatibility;
8. preserve human approval before an application is sent.

These principles should be retained.

Your use case is materially broader than the inherited data model. You are targeting several career families with different definitions of “fit” and different evidence needs:

- senior backend and full-stack software engineering;
- distributed-systems engineering;
- AI application engineering;
- applied scientist, ML engineer, and ML researcher;
- quantitative research, Algorithmic Trader, and quantitative development;
- biotech research, engineering, and ML roles.

The inherited framework stores one general candidate profile, one set of search priorities, and mostly prose-level project descriptions. It can reword or reorder what is already in a master CV, but it cannot reliably perform the selection you described: choose different subsets and different components of projects A–Z for each role while preserving evidence, metrics, and factual boundaries.

The recommended product direction is therefore:

> Preserve the inherited application-quality pipeline, but place it on top of a structured, evidence-backed career knowledge base, explicit role-track models, and a staged job-discovery/ranking pipeline.

The first implementation priority should not be more scraping or automatic resume writing. It should be the **career evidence model**: projects, experiences, atomic claims, skills, metrics, artifacts, and role relevance. If this layer is weak, every later ranking and resume decision will be difficult to explain and easy to distort.

## 2. Sources and review scope

This review used:

- the complete local checkout of [`xzhnshng/ai-job-search`](https://github.com/xzhnshng/ai-job-search);
- comparison with [`MadsLorentzen/ai-job-search`](https://github.com/MadsLorentzen/ai-job-search);
- the supplied X post by [Lian Lim](https://x.com/dashboardlim/status/2062979164630614266);
- the supplied [AgentConn field report](https://agentconn.com/blog/ai-job-search-agent-drafter-reviewer-claude-code-2026/);
- the supplied screenshot;
- repository documentation, commands, skills, portal CLIs, tests, templates, configuration, and security policy.

At the reviewed commit, the fork and upstream `master` branches have **zero divergence**. There are no custom commits in the fork yet. This is a useful baseline: future changes can be organized as deliberate product decisions rather than repairs to unknown fork-specific behavior.

The X page itself did not expose readable page content to the review tool. The quoted text in the request, the screenshot, and the AgentConn article all describe the same core workflow and are consistent with the checked-out repository. Architecture and capability conclusions below are based primarily on the repository itself, not on promotional summaries.

## 3. What the inherited project actually is

### 3.1 Product form

The project is best understood as a **local, file-backed operating procedure for an AI coding agent**.

It does not contain:

- a persistent application server;
- a database;
- a web UI;
- a background scheduler;
- a queue or workflow engine;
- a single deterministic program implementing `/setup`, `/scrape`, and `/apply`.

Instead, the repository combines:

- `.claude/commands/*.md`: procedural command specifications;
- `.claude/skills/**`: profile, evaluation, writing, scraping, and upskilling rules;
- `.agents/skills/**`: portable job-portal skills, each with a TypeScript CLI;
- `CLAUDE.md`: candidate context and global workflow rules;
- LaTeX templates for CVs and cover letters;
- JSON/CSV/Markdown files as local state;
- tests and CI guards for the executable tools and configuration.

This design is easy to fork and customize, but execution quality depends on the agent following long natural-language instructions consistently. Some steps are deterministic; many remain model judgments.

### 3.2 Canonical sources and “thin pointers”

`AGENTS.md` establishes a thin-pointer approach:

- `.claude/` is the canonical workflow specification;
- `CLAUDE.md` and the job-application skill files are canonical candidate context;
- `.agents/skills/` contains portable portal search tools;
- other agent runtimes should point to these sources instead of duplicating them.

This is a sound anti-drift principle. However, the current repository still has factual overlap among:

- `CLAUDE.md`;
- `01-candidate-profile.md`;
- `cv/main_example.tex`.

The `/apply` grounding audit treats the union of all three as factual evidence and warns when they disagree. For a much larger project corpus, maintaining facts in several prose and LaTeX files will become fragile. A structured source of truth should eventually generate or feed these views.

## 4. Existing end-to-end workflows

### 4.1 Profile setup and enrichment

`/setup` supports three input paths:

- scan a populated `documents/` folder;
- import a pasted CV;
- conduct an interview.

It populates identity, education, experience, skills, behavioral preferences, writing style, target sectors, deal-breakers, job-evaluation settings, CV language, and interview examples.

`/expand` scans CVs, LinkedIn exports, diplomas, reference letters, GitHub repositories, portfolios, Kaggle, Google Scholar, and related public sources. It creates a deduplicated competency map, preserves source annotations, shows proposed additions, and requires confirmation before writing.

This is a useful ingestion foundation. Its limitation is that it enriches a mostly narrative profile. It does not normalize each project into reusable components or distinguish:

- a fact from a resume-ready claim;
- a project from a particular contribution within it;
- a tool used experimentally from a tool used deeply;
- an inferred competency from a verified outcome;
- a role-specific framing from the underlying evidence.

### 4.2 Job discovery

`/scrape`:

1. loads `job_scraper/seen_jobs.json`, the application tracker, and search queries;
2. discovers installed portal skills;
3. runs portal-specific CLIs, preferably in parallel;
4. falls back to web search when needed;
5. fetches promising job details;
6. removes already-seen and already-applied jobs;
7. detects replicated mass postings;
8. assigns a quick high/medium/low fit signal;
9. stores all seen results;
10. presents new jobs and LinkedIn people-search links;
11. performs portal health checks.

Installed portals at the reviewed commit are:

- Akademikernes Jobbank;
- Jobdanmark;
- Jobindex;
- Jobnet;
- LinkedIn public listings;
- freehire.me.

The first four are Denmark-oriented. LinkedIn and FreeHire offer broader coverage, but this is not yet a comprehensive search system for the US or other target markets. There are no built-in connectors for major company ATS platforms or common US aggregators, and no unified canonical job schema beyond the lightweight `seen_jobs.json` record.

### 4.3 Ranking and evaluation

`/rank` batch-fetches new postings and uses parallel agents to score:

- technical match: 30%;
- experience match: 25%;
- behavioral/culture fit: 15%;
- career alignment: 30%;
- location: veto/flag rather than a weighted score.

It also handles citizenship, permanent-residency, visa, clearance, deadline, and location gates. It updates the seen-job record without marking a job as applied.

`/apply` repeats a deeper evaluation with company research before generating documents.

The evaluation framework is unusually thoughtful about:

- legal eligibility and work authorization;
- explicit deal-breakers;
- energy and motivation, not just qualification;
- career trajectory;
- salary benchmarks when local data is supplied;
- the difference between a gap and an adjacent skill.

However, it has one global weighting model. Your role families require different weights and rubrics. “Research depth” is central for an ML researcher, while production reliability and scale may dominate a distributed-systems role. A single score can hide these differences.

Culture, company future, future-career leverage, and value for a future business are also not equivalent:

- culture is uncertain evidence about the working environment;
- company future is an investment-like assessment of market and organizational durability;
- career leverage asks what capabilities and credibility the role will build;
- founder leverage asks what domain, network, ownership, or market insight the role may create.

These should be separate, evidence-labelled dimensions rather than one broad “career alignment” score.

### 4.4 Application generation

`/apply` implements the strongest part of the project:

1. parse a posting from URL or pasted text;
2. treat posting content as untrusted data;
3. perform eligibility checks and full fit evaluation;
4. extract every requirement;
5. draft a tailored two-page LaTeX CV;
6. draft a one-page LaTeX cover letter;
7. ground facts against the profile, master CV, and `CLAUDE.md`;
8. send the drafts inline to a fresh reviewer agent;
9. receive exact edits plus narrative critique;
10. revise without accepting fabricated suggestions;
11. compile with LuaLaTeX/XeLaTeX;
12. visually inspect and iterate;
13. extract the CV text layer with `pdftotext`;
14. check ATS reading order, contact details, and keyword coverage;
15. present verification results and files for human review.

The reviewer is explicitly a hiring-manager proxy. It checks missed requirements, company-specific framing, passive language, tone, profile consistency, and unsupported claims.

The workflow does **not** automatically submit applications. That is a deliberate and valuable boundary, not a missing feature.

### 4.5 After application

The inherited system also includes:

- `/outcome`: application history, follow-ups, thank-you notes, and feedback loops;
- `/interview`: stage-specific interview preparation grounded in submitted materials;
- `/gmail-sync`: proposes status updates from email, with approval before local writes;
- `/notion-sync`: a one-way presentation layer, with local files remaining authoritative;
- `/html-report`: an offline dashboard;
- `/upskill`: gap heatmaps and current learning resources;
- `/add-portal` and `/add-template`: extension workflows;
- `/reset`: guarded deletion of profile or document data.

These capabilities make the project broader than the “three commands” description. The three commands are the entry path, not the complete system.

## 5. Strengths worth preserving

### 5.1 Grounded generation

The explicit no-fabrication rule is reinforced at multiple stages. Unsupported requirements remain visible gaps. This should become an invariant in the customized system.

### 5.2 Drafter–reviewer separation

Providing a fresh reviewer with inline drafts reduces anchoring and lets the second agent behave as an adversarial reader. This is more valuable than simply asking one model to “double-check” itself.

### 5.3 Artifact-level quality gates

The workflow verifies the actual PDF, not only LaTeX source. It also verifies the text layer that an ATS may parse. These are practical, testable gates.

### 5.4 Security awareness

The repository recognizes that a job posting is untrusted web content processed beside personal data. It restricts shell permissions, blocks lifecycle-script expansion in CI, protects personal outputs with `.gitignore`, and forbids following embedded instructions or links.

### 5.5 Human oversight

The system assists with judgment and production but does not auto-submit. The AgentConn article emphasizes that this keeps the project in the high-fit, high-signal category rather than turning it into a mass-application bot.

### 5.6 Local ownership and extensibility

Personal data and generated artifacts remain local. Portal and template extensions follow visible conventions. This matches the desire to own and evolve the system.

## 6. Gaps relative to the intended use case

### 6.1 No structured project knowledge base

Current support consists mainly of an `Independent Projects` section, document scanning, GitHub competency discovery, and a comprehensive LaTeX master CV. That is insufficient for projects A–Z.

Needed capabilities include:

- one canonical record per project;
- nested components or workstreams within a project;
- problem, context, constraints, actions, architecture, methods, and outcomes;
- quantified metrics with definitions and provenance;
- skills and domains demonstrated;
- individual contribution versus team result;
- dates, maturity, confidentiality, and public links;
- source documents and evidence excerpts;
- permissible strength of claims;
- candidate bullet variants;
- relevance to each role track and job requirement.

Without this model, project selection will depend on semantic impressions and may repeatedly rediscover or distort the same facts.

### 6.2 No atomic claim ledger

The inherited system verifies drafts against long documents. Your corpus calls for a claim ledger: small, reusable, evidence-backed statements such as:

> Reduced p99 latency by 37% in workload X, measured with method Y.

Each claim should carry evidence, confidence, scope, allowed wording, skills, and related projects. Resume bullets can then be composed from verified claims instead of generated from memory.

### 6.3 No explicit role-track system

The repository has search priorities and role-type writing suggestions, but not stable role-track objects with:

- titles and synonyms;
- must-have and differentiating competencies;
- track-specific scoring weights;
- project-selection rubrics;
- resume section priorities;
- expected seniority signals;
- target-company and industry preferences;
- disqualifiers;
- learning strategy.

These role-track objects are necessary to explain why projects `(A, B, Z, K)` are chosen for distributed systems and `(A, C, M, N)` for ML research.

### 6.4 Project selection is not an optimization stage

The current CV workflow tailors bullets and cuts low-relevance lines to stay within two pages. It does not first generate a candidate evidence set and solve for:

- requirement coverage;
- role-track relevance;
- strength and verifiability;
- diversity of evidence;
- seniority signal;
- recency;
- uniqueness;
- page budget;
- avoiding redundant projects.

Selection and wording should be separate stages. First choose evidence; then write it.

### 6.5 Daily operation is manual

`/scrape`, `/rank`, and report generation are invoked manually. No scheduler runs at the end of each day, no retry policy exists, and no notification mechanism delivers a digest.

A future daily pipeline needs explicit stages, idempotent run records, portal health, rate limits, snapshots of posting text, failure isolation, and a review inbox. Scheduling should be added only after the manual workflow is trustworthy.

### 6.6 “Resume prepared for every job” needs tiering

Producing a fully reviewed, compiled, ATS-checked resume for every discovered job would be expensive and create many low-value artifacts.

A better staged policy is:

- all new jobs: normalize, deduplicate, and quick-score;
- plausible jobs: full track-aware ranking;
- shortlist: generate a role-track resume plan or reusable variant;
- high-priority jobs: create a tailored resume draft;
- user-approved applications: run reviewer, compile, visual QA, and ATS checks.

This preserves responsiveness without spending the highest-quality pipeline on jobs the user will never pursue.

### 6.7 Company and career-quality research is underspecified

The present framework encourages company and culture research but does not define evidence quality, freshness, or confidence. The customized system needs distinct factors such as:

- compensation and total rewards;
- location and working model;
- work authorization;
- manager/team quality signals;
- engineering/research maturity;
- financial durability and market outlook;
- learning and ownership potential;
- brand/network leverage;
- relevance to a future business;
- ethical or mission preferences.

Subjective or weakly observable factors should carry confidence labels and source dates. A job posting alone cannot establish company culture.

### 6.8 Data storage will not scale cleanly

`seen_jobs.json`, `job_search_tracker.csv`, prose profiles, and generated files are adequate for a personal prototype. As job, project, evidence, resume, and outcome histories grow, updates and joins become error-prone.

The spec should decide whether to retain human-readable files with generated indexes or introduce a local database. Either choice must preserve:

- easy backup and export;
- personal-data boundaries;
- schema validation;
- provenance;
- deterministic IDs;
- auditability;
- migration support.

### 6.9 Portal coverage and operational reliability need expansion

The portal-skill architecture is reusable, and health checks are a strong feature. But the installed set is not sufficient for all target geographies and fields. Future discovery should consider company career sites, ATS families, research institutions, biotech boards, and quant-specific sources. Access terms, robots policies, rate limits, and authentication requirements must be reviewed portal by portal.

## 7. Recommended conceptual architecture

This is a direction for the next specification, not a final implementation design.

### Layer 1: Source archive

Store original CVs, project documents, papers, transcripts, GitHub metadata, portfolios, job postings, and application artifacts. Originals are immutable evidence.

### Layer 2: Career evidence graph

Normalize:

- experiences;
- projects and project components;
- atomic claims;
- skills;
- domains;
- methods;
- metrics;
- artifacts/links;
- STAR stories;
- evidence sources;
- confidence and confidentiality.

Every generated claim must trace back to this layer.

### Layer 3: Career strategy

Define role tracks, seniority expectations, locations, compensation rules, target sectors, company preferences, deal-breakers, career leverage, and founder leverage. Track-specific rubrics live here.

### Layer 4: Job intelligence

Ingest and normalize jobs, snapshot postings, deduplicate, classify by track, research companies, score fit, record uncertainty, and track changes or expiration.

### Layer 5: Application composition

Build a requirement-to-evidence matrix, select projects and claims, generate a resume plan, write documents, conduct adversarial review, and enforce factual and layout gates.

### Layer 6: Operations and learning

Schedule discovery, produce daily digests, manage a review queue, track applications and outcomes, prepare interviews, and update ranking calibration from actual results.

## 8. Proposed resume-generation logic

The desired project behavior can be expressed as a transparent pipeline:

1. classify the job into one or more role tracks;
2. extract and prioritize requirements from the posting;
3. retrieve all supported claims related to those requirements;
4. score project components using:
   - requirement coverage;
   - track relevance;
   - evidence strength;
   - impact;
   - seniority signal;
   - recency;
   - uniqueness;
5. select a non-redundant set within the page budget;
6. compose bullets only from selected claims;
7. adjust terminology when truthful, without changing underlying facts;
8. show a requirement-to-evidence matrix and selection rationale;
9. send the result to an independent reviewer;
10. compile, visually inspect, and ATS-test the final artifact.

This separates three decisions that should not be conflated:

- **truth:** what the evidence supports;
- **selection:** what belongs in this application;
- **expression:** how the supported evidence is worded for this audience.

## 9. Important product decisions for the specification phase

The next document should resolve:

1. primary operating environment: Codex, Claude Code, or a runtime-neutral core;
2. initial geography and work-authorization constraints;
3. the first two or three role tracks for an MVP;
4. evidence and project schema;
5. local file storage versus a local database;
6. resume format and whether two pages is always mandatory;
7. what “resume prepared” means at each ranking tier;
8. daily schedule, delivery channel, and required human approvals;
9. portal priorities and acceptable access methods;
10. how salary, culture, company outlook, career leverage, and founder leverage are measured;
11. privacy rules for unpublished projects, employer-confidential work, and personal documents;
12. success metrics: discovery precision, shortlist acceptance, applications, interviews, time saved, and factual-error rate.

## 10. Recommended phased scope

### Phase A: Knowledge foundation

- ingest source documents and project materials;
- define role tracks;
- build project/component and claim schemas;
- create provenance and confidence rules;
- generate and manually approve a master claim library.

### Phase B: Track-specific resume intelligence

- create baseline resume strategies per track;
- implement requirement-to-evidence mapping;
- implement project/component selection;
- generate bullets from approved claims;
- retain the inherited reviewer and PDF/ATS verification loops.

### Phase C: Job intelligence

- add target-market portals;
- normalize and snapshot job records;
- implement track classification and richer ranking;
- separate hard filters, fit, career leverage, and company-quality evidence.

### Phase D: Daily operations

- schedule discovery;
- generate daily digests and a review queue;
- create resume plans or drafts by tier;
- add observability, retry handling, and portal-health reporting.

### Phase E: Outcome learning

- connect applications, interviews, rejections, offers, and user feedback;
- analyze which tracks, evidence, and resume variants perform;
- propose calibration changes for user approval rather than silently changing strategy.

## 11. Risks and guardrails

- **Fabrication risk:** all output claims must resolve to approved evidence.
- **Prompt-injection risk:** job postings and external pages remain untrusted data.
- **Privacy risk:** personal documents, salary information, email-derived status, and application artifacts must remain private by default.
- **Over-automation risk:** do not auto-submit applications; maintain human gates.
- **Ranking false precision:** show factor scores, evidence, and confidence rather than only one number.
- **Culture inference risk:** label weak or subjective evidence; do not present it as fact.
- **Scraper brittleness:** maintain portal contracts, health probes, rate limits, and fallbacks.
- **Model drift:** validate schemas and use deterministic checks around model-generated decisions.
- **Resume dilution:** optimize for a small number of defensible, role-relevant claims, not keyword volume.
- **Feedback bias:** outcomes are sparse and affected by networking, timing, and labor-market conditions; do not overfit ranking weights to a few results.

## 12. Conclusion

The inherited repository is an excellent application-craft framework and a useful starting point, especially for grounded generation, adversarial review, document verification, ATS checks, local ownership, and human oversight.

It should not be extended by simply adding more prose to the candidate profile and more portals to `/scrape`. Your use case needs a stronger center: a structured career evidence system and explicit role-track strategies. Once those exist, the inherited `/apply` pipeline can become the final, high-quality stage of a larger system that discovers jobs, explains rankings, selects the right projects, and produces defensible resumes across several career directions.

The next artifact should be a product specification that converts these findings into:

- goals and non-goals;
- personas and role tracks;
- functional requirements;
- data entities and invariants;
- workflow states;
- ranking and selection behavior;
- human approval points;
- privacy and security requirements;
- MVP boundaries and acceptance criteria.
