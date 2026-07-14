import json
from typing import Any, TypeVar

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings


SchemaType = TypeVar("SchemaType", bound=BaseModel)


class DeepSeekServiceError(RuntimeError):
    """Raised when a DeepSeek request cannot be completed safely."""


class DeepSeekService:
    def __init__(self) -> None:
        self.settings = get_settings()

        self.client = OpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
        )

    def list_models(self) -> list[str]:
        """Return model identifiers currently available to the API key."""

        try:
            response = self.client.models.list()
            return sorted(model.id for model in response.data)
        except Exception as exc:
            raise DeepSeekServiceError(
                f"Could not retrieve DeepSeek models: {exc}"
            ) from exc

    def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        selected_model = model or self.settings.deepseek_default_model

        try:
            response = self.client.chat.completions.create(
                model=selected_model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=temperature,
                max_tokens=max_tokens or self.settings.max_output_tokens,
                stream=False,
            )

            content = response.choices[0].message.content

            if not content:
                raise DeepSeekServiceError(
                    "DeepSeek returned an empty response."
                )

            return content.strip()

        except RateLimitError as exc:
            raise DeepSeekServiceError(
                "DeepSeek rate limit reached."
            ) from exc

        except APIConnectionError as exc:
            raise DeepSeekServiceError(
                "Could not connect to DeepSeek."
            ) from exc

        except APIStatusError as exc:
            raise DeepSeekServiceError(
                f"DeepSeek returned HTTP {exc.status_code}."
            ) from exc

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        selected_model = model or self.settings.deepseek_default_model

        json_system_prompt = (
            f"{system_prompt}\n\n"
            "Return only one valid JSON object. "
            "Do not use Markdown fences or add commentary."
        )

        try:
            response = self.client.chat.completions.create(
                model=selected_model,
                messages=[
                    {
                        "role": "system",
                        "content": json_system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens or self.settings.max_output_tokens,
                stream=False,
            )

            content = response.choices[0].message.content

            if not content:
                raise DeepSeekServiceError(
                    "DeepSeek returned empty JSON content."
                )

            parsed = json.loads(content)

            if not isinstance(parsed, dict):
                raise DeepSeekServiceError(
                    "DeepSeek JSON response was not an object."
                )

            return parsed

        except json.JSONDecodeError as exc:
            raise DeepSeekServiceError(
                "DeepSeek returned invalid JSON."
            ) from exc

        except (
            RateLimitError,
            APIConnectionError,
            APIStatusError,
        ) as exc:
            raise DeepSeekServiceError(
                f"DeepSeek request failed: {exc}"
            ) from exc

    def generate_structured(
        self,
        *,
        schema: type[SchemaType],
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> SchemaType:
        raw_data = self.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
        )

        try:
            return schema.model_validate(raw_data)
        except ValidationError as exc:
            raise DeepSeekServiceError(
                f"DeepSeek output failed schema validation: {exc}"
            ) from exc