import json

from assistant.email.model import EmailAssessment, EmailMessage
from assistant.shared.base_llm_service import BaseLLMService


class EmailAssessor(BaseLLMService):
    """Decides whether an incoming email needs a personal reply.

    Sends the email's sender, subject, and body preview to the LLM and asks it
    to return a structured JSON verdict. Emails like newsletters, automated
    notifications, or marketing are marked as not needing a response. Personal
    or work emails that ask a question or expect a reply are flagged as needing one.

    Optionally accepts a context string (memories from the MCP server + relevant
    OneDrive documents) which is included in the prompt so the LLM can make a
    more informed decision — for example, recognising a sender it knows about.
    """

    def assess_email(self, email: EmailMessage, context: str = "") -> EmailAssessment:
        """Ask the LLM to triage a single email and return its verdict.

        Builds a prompt with the email details and optional personal context,
        calls the LLM, and parses the JSON response into an `EmailAssessment`.

        Args:
            email: The email to evaluate.
            context: Optional block of text with relevant memories and document
                names to help the LLM make a better decision. Pass an empty
                string to skip (the context section is omitted from the prompt).

        Returns:
            An `EmailAssessment` with `needs_response`, a `reason`, and a `category`.
        """
        context_section = (
            f"\nContext about Adel and this sender (from personal memory):\n{context}\n"
            if context
            else ""
        )

        prompt = f"""
              You are an email triage assistant.
              Decide if this email needs a personal response from Adel.

              Email:
                  Sender: {email.sender}
                  Subject: {email.subject}
                  Body preview: {email.body_preview}
              {context_section}
              Return only valid JSON with this exact shape:
              {{
                  "needs_response": false,
                  "reason": "short explanation",
                  "category": "marketing"
              }}

              Rules:
                  - needs_response must be true or false.
                  - category must be one of: personal, work, marketing, security, notification, unknown.
                  - Return raw JSON only, no markdown, no extra text.
          """
        text = self._invoke(prompt)
        data = json.loads(text)
        return EmailAssessment(
            needs_response=data["needs_response"],
            category=data["category"],
            reason=data["reason"],
        )
