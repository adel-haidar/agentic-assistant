from pydantic import BaseModel


class EmailMessage(BaseModel):
    """Represents a single email fetched from the inbox.

    This is a lightweight version of the email — it only holds the fields
    we actually need for triage and drafting. The full email body is not
    fetched; we work with just the preview.
    """

    id: str
    """The unique ID assigned by Microsoft to this email. Used when saving a draft reply."""

    body_preview: str
    """The first ~255 characters of the email body, as provided by the Graph API."""

    subject: str
    """The subject line of the email."""

    sender: str
    """The email address of the person who sent the email."""


class EmailDraft(BaseModel):
    """A draft reply ready to be saved to Outlook's Drafts folder."""

    message_id: str
    """The ID of the original email this draft is replying to."""

    subject: str
    """The subject for the reply, e.g. 'Re: Your question'."""

    draft_body: str
    """The full text of the drafted reply."""


class EmailAssessment(BaseModel):
    """The result of asking the LLM whether an email needs a response."""

    needs_response: bool
    """True if the LLM decided a personal reply is needed, False otherwise."""

    reason: str
    """A short explanation of why the LLM made that decision."""

    category: str
    """How the email is classified. One of: personal, work, marketing, security, notification, unknown."""


class EmailSyncResult(BaseModel):
    """A summary returned by the /email/sync endpoint describing what happened during a sync."""

    checked_messages: int
    """Total number of new emails that were fetched and evaluated."""

    needs_response: int
    """How many of those emails the LLM decided needed a reply."""

    drafts_created: int
    """How many draft replies were successfully saved to Outlook."""
