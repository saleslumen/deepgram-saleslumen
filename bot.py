"""One voice call. Entrypoint only."""

from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from pipecat.evals.transport import EvalTransportParams
from pipecat.runner.run import app, main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams

from call.flow import END_CALL
from catalog import Catalog, load as load_catalog
from config import Config, load
from live.routes import register
from voice.pipeline import run_call

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    "eval": lambda: EvalTransportParams(audio_in_enabled=True),
}


@cache
def _runtime() -> tuple[Config, Catalog]:
    return load(), load_catalog(ROOT)


async def bot(runner_args: RunnerArguments) -> None:
    settings, catalog = _runtime()
    transport = await create_transport(runner_args, transport_params)
    await run_call(transport, runner_args, catalog, settings)


register(app, global_names=(END_CALL,))

if __name__ == "__main__":
    main()
