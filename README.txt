# Townlight Communications 0.2.0 development candidate

Townlight Communications is the development candidate for the Townlight Windows staff Communications screen. It is not included in the released Windows 1.9.3.2 suite. Distribution name: `townlight-comms`; import name: `townlight_comms`; current dependency: Townlight Core 1.3.0.dev2 (`townlight-core 1.3.0.dev2`).

The canonical application is the Communications workspace in the Townlight Windows staff shell, using existing administrator, city-staff and clerk roles, plus a separate Records permission for archive access. The repository's standalone `main.py` remains a prototype for draft helpers. It is not a secured workflow server, and its commands are not production deployment instructions.

## Workflow

- Draft and save content from selected Meetings, Code, Notice or 311 sources. The Parks adapter remains pending until the Parks phase.
- Run an Access check, review and approve one exact revision, then schedule website and email publication.
- Archive the published revision to Records BEFORE editing, correcting, or withdrawing it; Records access requires the separate Records permission.
- Correct or withdraw published content without reviving stale revisions.
- Capture approved source snapshots and public attachments. Private source content cannot be published.

Publication is transactional: approved revision snapshots, website and email tasks, and status are durable in PostgreSQL. A host service claims due work with leases, so the native app need not stay open. The website path uses the existing authenticated portal receiver.

Website publication uses the host-configured portal credential and origin. Normal operation uses an unattended host service, not an operator's open window. Email uses city settings shared with 311 SMTP. Subscribers must confirm email and can manage preferences or unsubscribe. Queued means pending, not SMTP accepted. Only sent means SMTP accepted the message, not human receipt; failed and uncertain sends are shown separately, and uncertain sends are never retried automatically.

The Records archive stores actual published bytes and receipts and returns a real identifier. It requires the separate Records role permission.

Status: source-level checks passed for this development candidate, but installed Windows candidate acceptance is still pending. Do not describe this module as completed or released. The existing download at https://github.com/townlight/townlight/releases/latest explicitly does not include Communications.

License: code AGPL-3.0-only with the TownLight Public Institution Exception; documentation CC BY 4.0.
