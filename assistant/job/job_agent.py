import json
import logging

from assistant.shared.base_llm_service import BaseLLMService

logger = logging.getLogger(__name__)


class JobAgent(BaseLLMService):
    def analyse_job_status(self, context: str = "") -> dict:
        prompt = self.create_job_analysis(statement, context)
        raw = self._strip_markdown(self._invoke(prompt, max_tokens=8192))
        return json.loads(raw)

    def create_job_analysis(
        self,
        statement: str,
        context: str = "",
    ) -> str:
        context_section = (
            f"\n<memory-context>\n"
            f"Context from Adel's personal memory (prior analyses, certificates, trainings, experties, goals and cv):\n"
            f"{context}\n"
            f"</memory-context>\n"
            if context
            else ""
        )

        statement_payload = statement

        prompt = f"""You are a job hunting personal assistant.

    """
