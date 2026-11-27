# Townlight Communications 0.2.1 development candidate

Townlight Communications is the development candidate for the Townlight Windows staff Communications screen. The known-good Windows 1.10.0.1 baseline remains separate; the new Library/Elections source extension is an unreleased Windows 1.11.0.1 candidate, and Communications is not newly installed in every deployment. Distribution name: `townlight-comms`; import name: `townlight_comms`; current dependency: Townlight Core 1.3.0.dev2; package pin `townlight-core==1.3.0.dev2` (not `oldciviccore`).

The canonical application is the Communications workspace in the Townlight Windows staff shell, using existing administrator, city-staff and clerk roles, plus a separate Records permission for archive access. The repository's standalone `main.py` remains a prototype for draft helpers. It is not a secured workflow server, and its commands are not production deployment instructions.

## Workflow

- Draft and save content from selected Meetings, Code, Notice, 311 or Parks sources, plus candidate approved Library/Elections sources.
- Run an Access check, review and approve one exact revision, then schedule website and email publication. Library/Elections source approval is a prerequisite, not a substitute for Communications approval. For Elections, Communications binds the approved public title, body, citation and version exactly; changing any of those requires a revised Elections approval plus a separate Communications approval. Normal Library editorial flow is unchanged. A revised source requires a ref refresh and a new Communications revision with separate approval; existing published v1 stays available until an approved correction. No new publisher is introduced.
- Archive the published revision to Records BEFORE editing, correcting, or withdrawing it; Records access requires the separate Records permission.
- Correct or withdraw published content without reviving stale revisions.
- Capture approved Library/Elections source snapshots through the fixed protected `/desktop/v1/{module}/items/{id}/source` provenance endpoint; this endpoint is specific to Library/Elections source provenance, not all sources or attachments. The public article uses the existing actual Communications URL; there is no new Library/Elections public page. Private notes never enter captured source content, and private source content cannot be published. The separate Records permission is unchanged.

Publication is transactional: approved revision snapshots, website and email tasks, and status are durable in PostgreSQL. A host service claims due work with leases, so the native app need not stay open. The website path uses the existing authenticated portal receiver.

Website publication uses the host-configured portal credential and origin. Normal operation uses an unattended host service, not an operator's open window. Email uses city settings shared with 311 SMTP. Subscribers must confirm email and can manage preferences or unsubscribe. Queued means pending, not SMTP accepted. Only sent means SMTP accepted the message, not human receipt; failed and uncertain sends are shown separately, and uncertain sends are never retried automatically.

The Records archive stores actual published bytes and receipts and returns a real identifier. It requires the separate Records role permission.

Status: source-level checks passed for this 0.2.1 development candidate, but installed Windows candidate acceptance is still pending; this is not an installed-acceptance claim. The known-good Windows 1.10.0.1 baseline exists, and the Library/Elections source extension is an unreleased Windows 1.11.0.1 candidate. Do not describe this module as completed or released.

License: code AGPL-3.0-only with the TownLight Public Institution Exception; documentation CC BY 4.0.
