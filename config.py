"""Typed process configuration."""

from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SttProvider = Literal["deepgram-flux"]
TtsProvider = Literal["deepgram-flux"]
LlmProvider = Literal["deepgram", "openrouter"]
Required = Annotated[str, Field(min_length=1)]


class ConfigError(Exception):
    """The process environment does not match the demo configuration."""


class Config(BaseSettings):
    """Closed settings for one voice process."""

    model_config = SettingsConfigDict(env_ignore_empty=True, str_strip_whitespace=True, env_file=None)

    stt_provider: SttProvider = "deepgram-flux"
    tts_provider: TtsProvider = "deepgram-flux"
    llm_provider: LlmProvider = "deepgram"
    deepgram_api_key: Required
    deepgram_voice: str = "flux-alexis-en"
    deepgram_llm_model: str = "think-fast"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4.1"
    saleslumen_api_key: Required
    saleslumen_organization_id: Required
    saleslumen_namespace_id: str = ""
    saleslumen_api_domain: str = "saleslumenapis.com"

    @property
    def workflows_origin(self) -> str:
        return f"https://workflows.{self.saleslumen_api_domain}"

    @field_validator("deepgram_voice")
    @classmethod
    def flux_voice(cls, value: str) -> str:
        if not value.startswith("flux-"):
            raise ValueError("DEEPGRAM_VOICE must be a Flux voice, such as flux-alexis-en")
        return value

    @field_validator("saleslumen_api_domain")
    @classmethod
    def api_domain_is_host(cls, value: str) -> str:
        if value.startswith("https://") or value.startswith("http://") or "/" in value:
            raise ValueError("SALESLUMEN_API_DOMAIN must be a host name, such as saleslumenapis.com")
        return value

    @model_validator(mode="after")
    def openrouter_key_when_selected(self) -> Self:
        if self.llm_provider == "openrouter" and self.openrouter_api_key == "":
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        return self


def load() -> Config:
    """Read configuration from the environment and reject unknown providers."""
    try:
        return Config(_env_file=None)
    except ValidationError as exc:
        raise ConfigError(_validation_message(exc)) from exc


def _validation_message(exc: ValidationError) -> str:
    return "\n".join(_error_line(error) for error in exc.errors())


def _error_line(error) -> str:
    msg = error["msg"].removeprefix("Value error, ")
    if not error["loc"]:
        return msg
    return f"{str(error['loc'][-1]).upper()}: {msg}"