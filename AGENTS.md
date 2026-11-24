<a id="civiccomms-agent-board"></a>

# Townlight Communications Agent Board

## Current contract

- Townlight Communications `0.2.0` is a development candidate. Scope is defined by `COMMUNICATIONS-DESIGN.md`; this file replaces superseded `0.1.1` prototype mandates.
- Windows only. Hold software tag and software publication until explicitly authorized.
- Canonical verification is the Windows suite runtime plus the named affected checks. Do not add extra gates.
- The standalone `main.py` is a prototype, not the production workflow server.

## Publication authority

- Unattended publication is authorized ONLY for the exact staff-approved revision, through the host, to website and email.
- Do not add social, advertising/campaign, legal-advice, or translation-certification capabilities.
- No silent fallback: missing, private, stale, mismatched, or unapproved content must remain blocked and visibly reported.
- Reuse existing administrator, city-staff, and clerk roles and native authentication.

## Source and history boundaries

- Preserve the private/public source boundary. Source eligibility is verified at runtime against the actual module; user-supplied eligibility flags cannot authorize publication.
- Revisions are immutable with SHA256 provenance. Editing clears approval and correction preserves prior revisions.
- Records archive must contain the exact published revision, source references, attachments, and receipts, and return a real identifier before any correction.
- Use explicit migrations and preserve existing affected headless checks.
- Preserve ordered correction and withdrawal sequences; stale content must not be resurrected.

## Architecture and workflow

- Preserve dependency direction: Townlight Core never depends on Townlight Communications.
- DeepSeek1017 performs low-risk implementation; Codex coordinates integration and review.
- Keep documentation, tests, and version surfaces synchronized with approved scope.
- Code is AGPL-3.0-only with the TownLight Public Institution Exception; documentation is CC BY 4.0.
