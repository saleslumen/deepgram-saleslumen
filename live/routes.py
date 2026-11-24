"""Live call view HTTP routes on the Pipecat dev server."""

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.staticfiles import StaticFiles

from catalog import load as load_catalog
from catalog.workflows import revision_steps
from live.feed import STREAM_END, Feed, feed as default_feed
from live.pipecat_graph import pipecat_graph

_LIVE_DIR = Path(__file__).resolve().parent
_DEFAULT_ROOT = _LIVE_DIR.parent


def register(
    app: FastAPI,
    *,
    global_names: tuple[str, ...],
    source: Feed | None = None,
    root: Path | None = None,
) -> None:
    event_source = default_feed if source is None else source
    project_root = _DEFAULT_ROOT if root is None else root
    static_dir = _LIVE_DIR / "static"
    @app.get("/live", response_class=HTMLResponse)
    async def live_page() -> HTMLResponse:
        return HTMLResponse((static_dir / "live.html").read_text(encoding="utf-8"))
    @app.get("/live/events", response_class=EventSourceResponse)
    async def live_events() -> AsyncIterator[ServerSentEvent]:
        queue = event_source.subscribe()
        try:
            while True:
                item = await queue.get()
                if item is STREAM_END:
                    return
                yield ServerSentEvent(data=item.model_dump(mode="json"))
        finally:
            event_source.unsubscribe(queue)
    @app.get("/live/graphs")
    async def live_graphs() -> JSONResponse:
        catalog = load_catalog(project_root)
        graph = pipecat_graph(catalog, global_names)
        workflows: dict[str, list] = {}
        seen: set[str] = set()
        for agent in catalog.agents.values():
            for grant in agent.grants:
                if grant.name in seen:
                    continue
                seen.add(grant.name)
                workflows[grant.name] = revision_steps(project_root, grant.name)
        return JSONResponse({
            "pipecat": graph.model_dump(mode="json", by_alias=True),
            "workflows": workflows,
        })
    app.mount("/live/static", StaticFiles(directory=static_dir), name="live_static")