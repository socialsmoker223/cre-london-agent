from openai import APITimeoutError, AuthenticationError, OpenAI, OpenAIError, RateLimitError

from london_monitor.config import Settings
from london_monitor.models import ModelTurn, ToolCall


class ProviderUnavailable(RuntimeError):
    """Safe, user-facing provider failure; never substitute another provider."""


class OpenAICompatibleProvider:
    """Configured OpenAI-compatible provider for the live research graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or Settings.from_env()
        self.client = OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.api_base,
            timeout=120,
            max_retries=0,
        )
        self.model = settings.model
        self.name = settings.provider

    def complete(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelTurn:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        try:
            result = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **({"tools": tools} if tools else {}),
                **({"reasoning_effort": "low"} if self.name == "z.ai" else {}),
                **({"extra_body": {"provider": {
                    "require_parameters": True, "allow_fallbacks": False,
                }}} if self.name == "openrouter" else {}),
                # Claude ignores JSON mode; Gemini tool rounds use prompt-based JSON.
                **({"response_format": {"type": "json_object"}}
                   if self.name != "anthropic" and not (self.name == "gemini" and tools)
                   else {}),
                **({"max_completion_tokens": 8192} if self.name == "openai"
                   else {"max_tokens": 8192}),
                timeout=min(float(timeout), 120),
            )
        except AuthenticationError as exc:
            raise ProviderUnavailable(
                "The selected provider key was rejected. Check the server credentials."
            ) from exc
        except APITimeoutError as exc:
            raise ProviderUnavailable("The configured model timed out. Please retry.") from exc
        except RateLimitError as exc:
            raise ProviderUnavailable(
                "The selected provider account is rate limited. Retry later."
            ) from exc
        except OpenAIError as exc:
            raise ProviderUnavailable(
                "The selected model is unavailable. Check the endpoint and model settings."
            ) from exc
        message = result.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            function = call.function
            calls.append(
                ToolCall(
                    id=call.id,
                    name=function.name,
                    arguments=function.arguments,
                    extra_content=getattr(call, "extra_content", None) or {},
                )
            )
        usage = result.usage
        values = dict(
            content=message.content or "",
            tool_calls=calls,
            usage={"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
            if usage
            else {},
        )
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is not None and "reasoning_content" in ModelTurn.model_fields:
            values["reasoning_content"] = reasoning
        if self.name == "openrouter":
            values["reasoning_details"] = getattr(message, "reasoning_details", None) or []
            values["reasoning_content"] = reasoning or getattr(message, "reasoning", None)
        return ModelTurn(**values)
