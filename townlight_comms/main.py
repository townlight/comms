"""FastAPI runtime foundation for Townlight Communications."""

import os

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from townlight_core import __version__ as TOWNLIGHT_CORE_VERSION

from townlight_comms import __version__
from townlight_comms.audience_variants import draft_audience_variant
from townlight_comms.faq import generate_faq
from townlight_comms.meeting_summary import draft_meeting_summary
from townlight_comms.newsletter import draft_newsletter
from townlight_comms.ordinance_summary import draft_ordinance_summary
from townlight_comms.persistence import (
    CommunicationsDraftRepository,
    StoredMeetingSummary,
    StoredSourceReview,
)
from townlight_comms.public_ui import render_public_lookup_page
from townlight_comms.source_review import review_sources

app = FastAPI(
    title='Townlight Communications',
    version=__version__,
    description='Source-backed public explainers, meeting summaries, newsletters, FAQs, and audience-variant draft support for Townlight.',
)

_draft_repository: CommunicationsDraftRepository | None = None
_draft_db_url: str | None = None


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Return an empty favicon response so browser QA has a clean console."""

    return Response(status_code=204)


class SourceReviewRequest(BaseModel):
    source_titles: list[str]
    citations: list[str]


class MeetingSummaryRequest(BaseModel):
    meeting_title: str
    actions: list[str]
    citations: list[str]
    audience: str = "residents"


class OrdinanceSummaryRequest(BaseModel):
    ordinance_id: str
    topic: str
    citations: list[str]


class NewsletterRequest(BaseModel):
    week_of: str
    source_items: list[str]


class FAQRequest(BaseModel):
    topic: str
    source_facts: list[str]


class AudienceVariantRequest(BaseModel):
    base_message: str
    audience: str


@app.get("/")
def root() -> dict[str, str]:
    """Return current product state without overstating unshipped behavior."""

    return {
        "name": "Townlight Communications",
        "version": __version__,
        "status": "standalone draft-helper prototype",
        "message": (
            "This standalone FastAPI server is a legacy draft-helper prototype, not a secured production "
            "workflow server. The canonical Communications workflow is in the authenticated Windows Suite "
            "desktop runtime at /desktop/v1/communications; installed Windows candidate acceptance remains pending."
        ),
        "next_step": (
            "Use the authenticated Windows Suite Communications candidate at /desktop/v1/communications; "
            "the standalone draft helpers are not a production deployment path."
        ),
    }


@app.get("/health")
def health() -> dict[str, str]:
    """Return dependency/version health for deployment smoke checks."""

    return {
        "status": "ok",
        "service": "townlight_comms",
        "version": __version__,
        "townlight_core_version": TOWNLIGHT_CORE_VERSION,
    }


@app.get("/townlight_comms", response_class=HTMLResponse)
def public_townlight_comms_page() -> str:
    """Return the public sample communications support UI."""

    return render_public_lookup_page()


@app.post("/api/v1/townlight_comms/source-review")
def source_review(request: SourceReviewRequest) -> dict[str, object]:
    if _draft_database_url() is not None:
        stored = _get_draft_repository().create_source_review(
            source_titles=request.source_titles,
            citations=request.citations,
        )
        return _stored_source_review_response(stored)

    result = review_sources(
        source_titles=request.source_titles,
        citations=request.citations,
    )
    payload = result.__dict__
    payload["review_id"] = None
    return payload


@app.get("/api/v1/townlight_comms/source-review/{review_id}")
def get_source_review(review_id: str) -> dict[str, object]:
    if _draft_database_url() is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Townlight Communications draft persistence is not configured.",
                "fix": "Set TOWNLIGHT_COMMS_DRAFT_DB_URL to retrieve persisted source review records.",
            },
        )
    stored = _get_draft_repository().get_source_review(review_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Source review record not found.",
                "fix": "Use a review_id returned by POST /api/v1/townlight_comms/source-review.",
            },
        )
    return _stored_source_review_response(stored)


@app.post("/api/v1/townlight_comms/meeting-summary")
def meeting_summary(request: MeetingSummaryRequest) -> dict[str, object]:
    if _draft_database_url() is not None:
        stored = _get_draft_repository().create_meeting_summary(
            meeting_title=request.meeting_title,
            actions=request.actions,
            citations=request.citations,
            audience=request.audience,
        )
        return _stored_meeting_summary_response(stored)

    result = draft_meeting_summary(
        meeting_title=request.meeting_title,
        actions=request.actions,
        citations=request.citations,
        audience=request.audience,
    )
    payload = result.__dict__
    payload["summary_id"] = None
    return payload


@app.get("/api/v1/townlight_comms/meeting-summary/{summary_id}")
def get_meeting_summary(summary_id: str) -> dict[str, object]:
    if _draft_database_url() is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Townlight Communications draft persistence is not configured.",
                "fix": "Set TOWNLIGHT_COMMS_DRAFT_DB_URL to retrieve persisted meeting summary records.",
            },
        )
    stored = _get_draft_repository().get_meeting_summary(summary_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Meeting summary record not found.",
                "fix": "Use a summary_id returned by POST /api/v1/townlight_comms/meeting-summary.",
            },
        )
    return _stored_meeting_summary_response(stored)


@app.post("/api/v1/townlight_comms/ordinance-summary")
def ordinance_summary(request: OrdinanceSummaryRequest) -> dict[str, object]:
    return draft_ordinance_summary(
        ordinance_id=request.ordinance_id,
        topic=request.topic,
        citations=request.citations,
    ).__dict__


@app.post("/api/v1/townlight_comms/newsletter")
def newsletter(request: NewsletterRequest) -> dict[str, object]:
    return draft_newsletter(
        week_of=request.week_of,
        source_items=request.source_items,
    ).__dict__


@app.post("/api/v1/townlight_comms/faq")
def faq(request: FAQRequest) -> dict[str, object]:
    return generate_faq(
        topic=request.topic,
        source_facts=request.source_facts,
    ).__dict__


@app.post("/api/v1/townlight_comms/audience-variant")
def audience_variant(request: AudienceVariantRequest) -> dict[str, object]:
    return draft_audience_variant(
        base_message=request.base_message,
        audience=request.audience,
    ).__dict__


def _draft_database_url() -> str | None:
    return os.environ.get("TOWNLIGHT_COMMS_DRAFT_DB_URL")


def _get_draft_repository() -> CommunicationsDraftRepository:
    global _draft_db_url, _draft_repository
    db_url = _draft_database_url()
    if db_url is None:
        raise RuntimeError("TOWNLIGHT_COMMS_DRAFT_DB_URL is not configured.")
    if _draft_repository is None or db_url != _draft_db_url:
        _dispose_draft_repository()
        _draft_db_url = db_url
        _draft_repository = CommunicationsDraftRepository(db_url=db_url)
    return _draft_repository


def _dispose_draft_repository() -> None:
    global _draft_repository
    if _draft_repository is not None:
        _draft_repository.engine.dispose()
        _draft_repository = None


def _stored_source_review_response(stored: StoredSourceReview) -> dict[str, object]:
    return {
        "review_id": stored.review_id,
        "source_titles": list(stored.source_titles),
        "citations": list(stored.citations),
        "source_count": stored.source_count,
        "ready_for_draft": stored.ready_for_draft,
        "missing_items": list(stored.missing_items),
        "boundary": stored.boundary,
        "created_at": stored.created_at.isoformat(),
    }


def _stored_meeting_summary_response(stored: StoredMeetingSummary) -> dict[str, object]:
    return {
        "summary_id": stored.summary_id,
        "meeting_title": stored.meeting_title,
        "actions": list(stored.actions),
        "title": stored.title,
        "audience": stored.audience,
        "draft_points": list(stored.draft_points),
        "required_review_steps": list(stored.required_review_steps),
        "citations": list(stored.citations),
        "boundary": stored.boundary,
        "created_at": stored.created_at.isoformat(),
    }
