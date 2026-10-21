<a id="civiccomms-agent-board"></a>

# Townlight Communications Agent Board

## Operating Contract

- Upstream suite spec: `townlight/townlight/docs/CivicSuiteUnifiedSpec.md`, especially the Townlight Communications catalog entry and suite-wide non-negotiables.
- Townlight Communications supports source-backed public explainers, meeting summaries, ordinance summaries, newsletters, FAQs, and audience-specific draft variants.
- AGPL-3.0-only for code (with the TownLight Public Institution Exception for qualifying deployments), CC BY 4.0 for documentation.
- Every change must keep docs, tests, browser QA evidence, and version surfaces in sync.

## Hard Boundaries

- Townlight Communications never publishes autonomously, creates campaign or advocacy content, provides legal advice, certifies translations, posts to social media, or updates a communications system of record.
- Townlight Communications v0.1.1 must not call live LLMs or live publishing/social-media/communications systems.
- Every generated artifact is a draft requiring staff review, citation verification, approval, and publication through official city channels.
- Townlight Communications depends on Townlight Core; Townlight Core must never depend on Townlight Communications.
- Townlight Communications may reference Townlight Meetings, Townlight Code, and Townlight Access concepts only through released APIs or deterministic sample data in v0.1.1.

## Verification

Run before push or release:

```bash
bash scripts/verify-release.sh
```

Browser QA evidence is required for `docs/index.html` and `/civiccomms` whenever visible UI changes.
