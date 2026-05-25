class BaseLLMService:
    """Base class for any service that needs to talk to an LLM via Amazon Bedrock.

    `EmailAssessor` and `EmailResponseWriter` both extend this class. It handles
    the low-level details of sending a prompt and getting a text response back,
    so the subclasses only need to worry about what to ask and how to parse the answer.
    """

    def __init__(self, bedrock_client, model_id: str):
        """Store the Bedrock client and the model to use for all requests.

        Args:
            bedrock_client: A boto3 `bedrock-runtime` client, already configured
                with the correct AWS region and credentials.
            model_id: The Bedrock model identifier string, e.g.
                'eu.amazon.nova-2-lite-v1:0'.
        """
        self._client = bedrock_client
        self._model_id = model_id

    def _invoke(self, prompt: str) -> str:
        """Send a prompt to the LLM and return the raw text response.

        This wraps Bedrock's `converse` API, which works like a single-turn
        chat: we send one user message and get one assistant reply back.

        Args:
            prompt: The full instruction text to send to the model.

        Returns:
            The model's response as a plain string.
        """
        response = self._client.converse(
            modelId=self._model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
        )
        return response["output"]["message"]["content"][0]["text"]

    def _strip_markdown(self, text: str) -> str:
        """Remove markdown code fences from a string if present.

        Some models wrap their JSON output in a markdown code block like:
            ```json
            { ... }
            ```
        This method strips those fences so the result can be parsed as plain JSON.

        Args:
            text: The raw string from the model, possibly wrapped in backticks.

        Returns:
            The string with any leading/trailing code fences removed.
        """
        if text.startswith("```json"):
            text = text.removeprefix("```json").removesuffix("```").strip()
        elif text.startswith("```"):
            text = text.removeprefix("```").removesuffix("```").strip()
        return text
