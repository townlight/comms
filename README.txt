# Townlight Communications 0.2.1

Townlight Communications 0.2.1 comes with Townlight for Windows 1.11.0.1 (https://github.com/townlight/townlight/releases/tag/townlight-windows-v1.11.0.1). It adds exact approved source snapshots for Library and Elections. The distribution is `townlight-comms`, the import is `townlight_comms`, and the dependency is Townlight Core 1.3.0.dev2 (`townlight-core==1.3.0.dev2`). Follow the Townlight staff integration guide (https://github.com/townlight/townlight/blob/main/docs/windows-utilities-library-elections.md) for module handoffs, backup, and recovery.

The canonical application is the Communications workspace in the Townlight Windows staff shell, using existing administrator, city-staff and clerk roles, plus a separate Records permission for archive access. The repository's standalone `main.py` remains a prototype for draft helpers. It is not a secured workflow server, and its commands are not production deployment instructions.

## Workflow

Library publication requires an approved Library source and the existing Communications editorial review. It also requires separate Communications approval of the exact revision. The Elections approval binds the title, the body, the official HTTPS citation and the source version. Any change requires new source and Communications approvals. Corrections keep the existing public URL and preserve prior Records archives.

- Draft and save content from selected Meetings, Code, Notice, 311 or Parks sources, plus approved Library and Elections sources.
- Run an Access check, review and approve one exact revision, then schedule website and email publication. Refresh a revised source reference and approve the new Communications revision. The existing public article remains available until the approved correction publishes.
- Archive the published revision to Records BEFORE editing, correcting, or withdrawing it; Records access requires the separate Records permission.
- Correct or withdraw published content without reviving stale revisions.
- Capture approved Library/Elections source snapshots through the fixed protected `/desktop/v1/{module}/items/{id}/source` provenance endpoint; this endpoint is specific to Library/Elections source provenance, not all sources or attachments. The public article uses the existing actual Communications URL; there is no new Library/Elections public page. Private notes never enter captured source content, and private source content cannot be published. The separate Records permission is unchanged.

Publication is transactional: approved revision snapshots, website and email tasks, and status are durable in PostgreSQL. A host service claims due work with leases, so the native app need not stay open. The website path uses the existing authenticated portal receiver.

Website publication uses the host-configured portal credential and origin. Normal operation uses an unattended host service, not an operator's open window. Email uses city settings shared with 311 SMTP. Subscribers must confirm email and can manage preferences or unsubscribe. Queued means pending, not SMTP accepted. Only sent means SMTP accepted the message, not human receipt; failed and uncertain sends are shown separately, and uncertain sends are never retried automatically.

The Records archive stores actual published bytes and receipts and returns a real identifier. It requires the separate Records role permission.

## Installed verification

Signed fresh hosted Windows tests verified Library and Elections publication. The same tests verified that the corrected Elections article kept the article URL. On two physical PCs, the Library article reached the public portal, a matching email reached the controlled SMTP receiver, and the exact Records archive verified after the desktop closed. The full two-PC workflow, Elections upgrade approval, and the subsequent profile-preserving uninstall remained incomplete.

Communications 0.2.0 remains documented in Townlight for Windows 1.10.0.1 (https://github.com/townlight/townlight/releases/tag/townlight-windows-v1.10.0.1). Its release acceptance record (https://github.com/townlight/townlight/releases/download/townlight-windows-v1.10.0.1/ACCEPTANCE-Windows-1.10.0.1.md) covers the 311, Communications, and Parks workflows, including a scheduled portal post, controlled SMTP delivery and Records retrieval after the desktop closed.

License: code AGPL-3.0-only with the TownLight Public Institution Exception; documentation CC BY 4.0.
