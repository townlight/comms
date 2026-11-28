# Changelog

All notable changes to Townlight Communications are documented here.

## [0.2.1] - Townlight Windows 1.11.0.1

- Added approved Library and Elections source snapshots to the existing Communications workflow. Each source requires its own approval before the separate Communications review and approval.
- Bound Elections publication to the approved title, body, official citation and source version. A correction uses the existing article URL and preserves immutable Records archives for prior revisions.
- See [Townlight for Windows 1.11.0.1](https://github.com/townlight/townlight/releases/tag/townlight-windows-v1.11.0.1) for the signed installer and verification coverage.

## [0.2.0] - Townlight Windows 1.10.0.1

- Added immutable revision approval, ordered correction and withdrawal, and approved source snapshot and attachment capture.
- Added scheduled website and email publication through the host, plus subscriber confirmation, preferences, and unsubscribe.
- Archived published bytes and receipts in Records under the separate Records permission.
- See the [signed Windows release](https://github.com/townlight/townlight/releases/tag/townlight-windows-v1.10.0.1) and its [acceptance record](https://github.com/townlight/townlight/releases/download/townlight-windows-v1.10.0.1/ACCEPTANCE-Windows-1.10.0.1.md).

## [0.1.1] - 2026-04-28

### Added

- Optional SQLAlchemy-backed source review and meeting summary draft records via `CIVICCOMMS_DRAFT_DB_URL`.
- Source review and meeting summary retrieval endpoints for persisted records.

### Changed

- Dependency-alignment release: moved CivicComms to `civiccore==0.3.0` while preserving the existing v0.1.0 runtime foundation behavior.
- Updated CI, verification gates, package metadata, docs, runtime tests, landing page, and public UI labels for the v0.1.1 release.

## [0.1.0] - 2026-04-27

### Added

- FastAPI package/runtime foundation pinned to `civiccore==0.2.0`.
- Source-readiness review helper requiring named source material and citations.
- Meeting summary draft outline helper.
- Plain-language ordinance explainer draft helper.
- Newsletter and FAQ draft scaffold helpers.
- Audience-specific variant draft helper.
- Accessible public sample UI at `/civiccomms` with browser QA coverage.
- Release gate: tests, docs, placeholder import guard, Ruff, and build artifact checks.

### Not Shipped

- Autonomous publication, campaign or advocacy content, legal advice, certified translation, live LLM calls, social media posting, and communications system-of-record integrations.
