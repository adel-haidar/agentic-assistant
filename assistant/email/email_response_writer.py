import json

from assistant.email.model import EmailDraft, EmailMessage
from assistant.shared.base_llm_service import BaseLLMService


class EmailResponseWriter(BaseLLMService):
    """Writes a draft reply to an email using the LLM.

    Given an email that has already been assessed as needing a response, this
    class asks the LLM to compose a friendly, professional reply in the same
    language as the original (English or German).

    Optionally accepts personal memory context and a list of OneDrive documents
    so the LLM can write a more personalised reply and reference relevant files
    the user may want to attach or link to.
    """

    def write_response_draft(
        self,
        email: EmailMessage,
        context: str = "",
        relevant_docs: list[str] | None = None,
    ) -> EmailDraft:
        """Ask the LLM to draft a reply to the given email.

        Builds a prompt with the email, optional memory context, and optional
        document names. The LLM is instructed to reference relevant documents
        naturally when they exist. The subject is automatically prefixed with
        'Re: ' if it isn't already.

        Args:
            email: The email to reply to.
            context: Optional plain-text block of personal memories about the
                sender and related topics. Pass an empty string to omit.
            relevant_docs: Optional list of OneDrive document name strings (e.g.
                ['Q1 Budget.xlsx', 'Project Brief.docx']). When provided, the
                LLM is told about these files and can reference them in the draft.

        Returns:
            An `EmailDraft` containing the reply subject and body text, ready
            to be saved to Outlook's Drafts folder.
        """
        context_section = (
            f"\nContext about Adel and this sender (from personal memory):\n{context}\n"
            if context
            else ""
        )

        docs_section = ""
        if relevant_docs:
            doc_list = "\n".join(f"  - {doc}" for doc in relevant_docs)
            docs_section = (
                f"\nRelevant documents found in Adel's OneDrive:\n{doc_list}\n"
                "If any of these are relevant to the reply, reference them naturally "
                "(e.g. 'I'll attach the X document'). Do not invent documents not listed.\n"
            )

        prompt = f"""
              You are Adel's personal email assistant. Write a draft response to the following email.
              The response must be friendly and professional.

              Sender: {email.sender}
              Subject: {email.subject}
              Body:
              {email.body_preview}
              {context_section}{docs_section}
              Rules:
                  - Match the language of the original email (German or English).
                  - Keep it concise.
                  - Do not include a subject line in the response.
                  - Return only valid JSON with this exact shape:
                  {{
                      "response_draft": "your draft here"
                  }}
                  - Return raw JSON only, no markdown, no extra text.
          """
        text = self._invoke(prompt)
        data = json.loads(text)
        subject = (
            email.subject
            if email.subject.lower().startswith("re:")
            else f"Re: {email.subject}"
        )
        return EmailDraft(
            message_id=email.id, subject=subject, draft_body=data["response_draft"]
        )
