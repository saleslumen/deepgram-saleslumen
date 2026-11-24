"""Speech and model services. Each provider name is a closed choice."""

import httpx
from openai.types.chat import ChatCompletionChunk
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.utils.types import is_given

from config import Config
from voice.think import THINK_URL, ThinkAssembler, think_request


def build_stt(settings: Config) -> DeepgramFluxSTTService:
    if settings.stt_provider != "deepgram-flux":
        raise RuntimeError(f"unsupported speech-to-text provider {settings.stt_provider}")
    return DeepgramFluxSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramFluxSTTService.Settings(keyterm=["Saleslumen", "Deepgram", "Pipecat", "workflow"], numerals=True),
    )


def build_tts(settings: Config) -> DeepgramFluxTTSService:
    if settings.tts_provider != "deepgram-flux":
        raise RuntimeError(f"unsupported text-to-speech provider {settings.tts_provider}")
    return DeepgramFluxTTSService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramFluxTTSService.Settings(voice=settings.deepgram_voice),
    )


def build_llm(settings: Config):
    """Return the configured language model. Deepgram Think is the default."""
    if settings.llm_provider == "deepgram":
        return DeepgramThinkLLMService(
            deepgram_api_key=settings.deepgram_api_key,
            settings=DeepgramThinkLLMService.Settings(model=settings.deepgram_llm_model),
        )
    if settings.llm_provider == "openrouter":
        return OpenRouterLLMService(
            api_key=settings.openrouter_api_key,
            settings=OpenRouterLLMService.Settings(model=settings.openrouter_model),
        )
    raise RuntimeError(f"unsupported language model provider {settings.llm_provider}")


class DeepgramThinkLLMService(OpenAILLMService):
    """Think at `POST /v1/think`.

    This subclass sends Think's streaming chat completion. Think is Early Access,
    requires an enrolled Deepgram contract, and accepts a fixed set of top-level fields.
    """

    supports_response_schema = False

    def __init__(self, *, deepgram_api_key: str, settings: OpenAILLMService.Settings) -> None:
        super().__init__(api_key=deepgram_api_key, settings=settings)
        self._deepgram_api_key = deepgram_api_key
        self._http_client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=120, write=10, pool=10))
        return self._http_client

    async def cleanup(self) -> None:
        client = self._http_client
        self._http_client = None
        try:
            await super().cleanup()
        finally:
            if client is not None:
                await client.aclose()

    async def get_chat_completions(self, context):
        adapter = self.get_llm_adapter()
        params = await adapter.get_llm_invocation_params(
            context,
            system_instruction=_instruction(self._settings.system_instruction),
            convert_developer_to_user=False,
        )
        tools = params.get("tools")
        if not is_given(tools) or _is_unset(tools):
            tools = None
        temperature = self._settings.temperature
        if not is_given(temperature) or _is_unset(temperature):
            temperature = None
        body = think_request(model=str(self._settings.model), messages=list(params["messages"]), tools=tools, temperature=temperature)
        return _ThinkChunks(self._http(), self._deepgram_api_key, body)


class _ThinkChunks:
    """Async stream of OpenAI chunks parsed from a Think response."""

    def __init__(self, client: httpx.AsyncClient, deepgram_api_key: str, body: dict) -> None:
        self._client = client
        self._deepgram_api_key = deepgram_api_key
        self._body = body
        self._generator = None

    def __aiter__(self):
        self._generator = self._iterate()
        return self._generator

    async def aclose(self) -> None:
        if self._generator is not None:
            await self._generator.aclose()

    async def close(self) -> None:
        await self.aclose()

    async def _iterate(self):
        assembler = ThinkAssembler()
        headers = {"Authorization": f"Token {self._deepgram_api_key}", "Content-Type": "application/json"}
        async with self._client.stream("POST", THINK_URL, headers=headers, json=self._body) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode(errors="replace")[:300]
                raise RuntimeError(f"Think request failed with HTTP {response.status_code}: {detail}")
            async for line in response.aiter_lines():
                item = assembler.feed(line)
                if item == "ignore":
                    continue
                if item == "done":
                    break
                yield ChatCompletionChunk.model_validate(item)
        assembler.close()


def _instruction(value) -> str:
    if not is_given(value) or value is None or _is_unset(value):
        return ""
    return str(value)


def _is_unset(value) -> bool:
    return type(value).__name__ == "NotGiven"
