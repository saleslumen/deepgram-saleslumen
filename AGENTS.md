# Pipecat and Saleslumen Agent

## Scope and non-ownership

Browser voice demo: Pipecat 1.17.0 SmallWebRTC, Deepgram Flux STT/TTS, Think (OpenRouter optional). Each voice connect builds one **Call** in `run_call` with a **FlowManager** from `pipecat.flows`.

**Owns:** `call/`, `voice/`, `execution/`, `catalog/`, local agents, skills, workflow manifests, and `agents.<name>.tools` modules.

**Does not own:** Saleslumen cloud APIs beyond the typed client, Apps Script hosting, or the Apps Script deployment id. The deployment id is operator-local; never commit it. Replace `scripts/REPLACE_WITH_DEPLOYMENT_ID` only in a private revision copy at publish time.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Call | Per-connect object in `run_call`; owns **Run**, execution **attempt**, **idle** count, all Flows nodes |
| Agent | Directory under `agents/` with `agent.yaml`, `PROMPT.md`, optional `tools.py` |
| Grant tuple | Tools, connectors, workflows on that agent from `agent.yaml` |
| Skill tuple | Skill names granted on that agent from `agent.yaml` |
| Role message | `agents/PROMPT.md` — sole LLM system instruction on agent nodes |
| Task message | `agents/<name>/PROMPT.md` on that agent node |
| Pipeline | `agents/pipeline.yaml` — `start`, explicit outcome routes, and required non-empty `lines.greeting`, `lines.check_in`, `lines.goodbye` (pipeline-spoken, not the model) |
| Outcome | Key on a tool `dict`; routes only when `pipeline.yaml` lists it |
| Flow state | `flow_manager.state`: `account_name`, `passcode_attempts`, `account_misses` only |
| Call key | Execution id base; runner call id or session id when present, else `uuid4` |
| Run | `execution/run.py` handle for one Workflows execution |
| Node | `NodeConfig` built on **Call**; tools use `FlowsFunctionSchema` |
| Step kind | **notice** (`object{}` signal schema) or **question** (any other schema) |

Step nodes come from the remote revision; tools may return runtime nodes.

## Layout and dependency direction

| Path | Role |
| --- | --- |
| `bot.py` | `.env`, single `config.load()`, catalog, `run_call` |
| `config.py` | Typed environment |
| `catalog/` | Load agents, pipeline, skills, workflows (Pydantic frontmatter; no central grants table) |
| `agents/` | Prompts, pipeline, per-agent yaml and `tools.py` |
| `call/flow.py` | **Call**, all Flows node builders |
| `execution/__init__.py` | Empty |
| `execution/client.py` | Workflows HTTP/SSE |
| `execution/steps.py` | Step model, revision join, spoken lines |
| `execution/models.py` | Execution, revision, and watch snapshot models |
| `execution/run.py` | **Run**; start, signal, close |
| `voice/pipeline.py` | Pipecat pipeline and FlowManager |
| `live/` | In-process feed, live routes, Pipecat graph projection, static call view |
| `voice/providers.py` | STT, TTS, LLM providers |
| `voice/think.py` | LLM context for agent nodes |
| `voice/connectors.py` | MCP HTTP client wiring |
| `evals/suite.yaml` | Lists `scripted/calls` and `simulations/callers`; spawns the bot |
| `evals/scripted/calls.yaml` | Every scripted call; each shared turn is one YAML anchor |
| `evals/simulations/callers.yaml` | Impatient and confused personas |

Dependency direction (imports must follow this):

- `catalog/` loads content (stdlib, PyYAML, paths under the demo root). Catalog YAML and **SKILL.md** / **WORKFLOW.md** frontmatter are Pydantic models with `extra` forbid. `catalog.load` joins every agent and pipeline problem into one error that names the file and field.
- `call/` builds **Call** and nodes; may import `catalog/`, `agents.<name>.tools`, `execution/`, and `pipecat.flows`.
- `execution/` imports no other project package; `execution/__init__.py` is empty.
- `live/` owns the feed, routes, Pipecat graph projection, and static page; may import `catalog/` and `execution/`; must not import `voice/`.
- `voice/` wires Pipecat and FlowManager; may import `call/`, `execution/`, and `live/`.
- Only `bot.py` imports `voice/`.

The pipeline attaches **TurnTrackingObserver** (Pipecat’s `PipelineWorker` installs it because turn tracking defaults on), **FunctionCallObserver** with arguments and results omitted, and **UserBotLatencyObserver** logging only latency. Transcription and LLM log observers are not attached—they would print caller and model text. Domain logs that remain are the execution id, the tool outcome name, and one line per accepted execution snapshot: observation version, projected step kind (`running`, `awaiting`, `succeeded`, `failed`), and, when awaiting, the step sequence and step id. That line never includes the label, payload, request, or caller words.

Eval uses the same `bot.py` transport: `transport_params["eval"] = EvalTransportParams(audio_in_enabled=True)`. The dev runner selects it from the harness’s first message.

## Live view

`live/` owns the feed, the routes, the Pipecat graph projection, and the static page. `project.js` maps workflow step paths to view node ids (no Python mapper). Routes: **GET /live** (page), **GET /live/events** (SSE via `EventSource`, no polling), **GET /live/graphs** (pipecat graph plus `workflows` keyed by grant name, each value `definition.steps` from `catalog.workflows.revision_steps(root, name)`, which owns the `workflows/<name>/revision.json` path; `live/` does not build that path), and **/live/static** for page assets.

Events (each carries `call_id`): `call_started`, `call_ended`, `transcript` (`role`, `text`), `flow_node` (`node`, `graph_node` from `flow_highlight_id`, `function`), `workflow_step` (`execution_id`, `version`, `state`, `workflow` grant name from **Call**, and `step_path` plus `sequence` when awaiting).

The pipecat graph includes a **global** node whose caption is the global function name `END_CALL` in `call/flow.py`; the global node has no edges. The workflow-step node kind is `workflow_step`. Each granted agent has one edge to it, labeled `start_workflow / answer_step`, because the round trip shares that branch. The feed keeps **call_started** outside the bounded replay. A full subscriber queue, or a new call, ends that stream so the tab reconnects onto **call_started** plus the replay. **StepListener** in `execution/run.py` is the `on_step` callback type. **Run** emits a workflow step only through that callback at accepted-snapshot time (after `_log_snapshot`, before `_changed.set()`). `voice/pipeline.py` publishes transcript, flow, and call boundary events. `bot.py` calls `register(app, global_names=(END_CALL,))` on `pipecat.runner.run.app` before `main()`. Open `http://localhost:7860/live` in a second tab while a call is up. The page is read-only. Transcript text is not logged.

Dependency note: `voice` may import `live` and `execution`; `live` may import `catalog` and `execution`; `live` must not import `voice`; `execution` must not import `live`.

## Call flow

### Construction

`run_call` constructs **Call** with the **Config** from the single `config.load()` in `bot.py`. **Call** owns **Run**, the execution attempt counter (starting at 1 for `{call key}-1`), and the idle counter. The start node has `respond_immediately` false and no greeting `pre_action`. After `flow_manager.initialize(call.start_node())` returns, `Call.say` queues `lines.greeting` as three LLM frames (`LLMFullResponseStartFrame`, `LLMTextFrame`, `LLMFullResponseEndFrame`) on the worker with no `LLMRunFrame`: `"Hi, thanks for calling Saleslumen. What is the phone number on your account?"` The first idle check-in uses the same `say` path. No LLM run until the caller speaks.

### Verification agent

Tools: `find_account`, `check_passcode` from `agents.verification.tools`. **account_misses** increments when digits do not match what the caller just said or the account is missing; the third miss **refuses**. **passcode_attempts** is separate. The passcode matches only when both the tool argument and the caller’s latest user message are the code. **refused** enters the end node. **verified** routes only if `pipeline.yaml` names it.

### Workflow agent

Tools: `start_workflow(workflow, request)`, granted skill tools, `answer_step`, `end_call`. One execution at a time. First execution id is the call key; later starts use `{call key}-{attempt}`.

- **Question** steps: `tts_say` on the label. The task quotes that label and describes each `answer_step` argument. The model calls `answer_step` only after the caller answers. A side question is answered in the same reply that asks the workflow question again. A question about the product is a skill, then the same question again.
- **Notice** steps: speak the label and acknowledge.
- A workflow result is only what a workflow line said. Agreeing to a question is not the result.

After the execution finishes, return to the workflow agent; speak the outcome line with `tts_say`; the model may offer more help.

### End node

**First idle** (`user_idle_timeout` eight seconds): `Call.on_idle` calls `say` with `lines.check_in` (three LLM frames, no LLM run): `"Are you still there?"`. **end_call**, **refused** verification, and the **second idle** reach the end node through `Call.finish`, which sets the end node then queues `EndFrame` after the node-entered frame. The model says goodbye in the same reply as **end_call**; **end_call** passes no goodbye into `finish`, so the end node has no `tts_say`. **Refused** verification and the **second idle** pass `lines.goodbye` into `finish`, so that line is the `tts_say` pre-action. Pre-actions: a function handler **closes** the execution, then optional `tts_say` when goodbye is not None. `respond_immediately` is false. There is no `end_conversation` post-action. Idle does not run during a function call.

### MCP

Connector tools exist only on nodes for agents that declare the connector in `agent.yaml`, wrapped for Flows as `(args, flow_manager)`.

## Workflows contract

HTTP to `https://workflows.<SALESLUMEN_API_DOMAIN>` with headers:

- `sl-api-key` (required)
- `sl-organization-id` (required)
- `sl-namespace-id` (optional when set)

Operations used by `execution/client.py`:

| Operation | Notes |
| --- | --- |
| Create execution | `executionId` + `requestId` query; replay on `ALREADY_EXISTS` / `ABORTED` |
| `:watch` | SSE snapshots until a terminal state |
| `:signal` | Body includes `targetStepRun`, `executionUid`, `signal`, `payload`, `requestId` |
| `:cancel` | Empty body on disconnect |

Revision definitions are joined in `execution/steps.py`; labels are spoken verbatim. Create, revision, and watch responses are Pydantic models checked in the Workflows client. Unknown response fields are ignored. A malformed response raises **WorkflowError**. Step-join failures stay **StepError**.

## Configuration

Environment keys mirror `.env.example` and `config.py` (see `README.md` table). Optional OpenRouter fields apply when `LLM_PROVIDER=openrouter`.

`config.load` builds one **pydantic-settings** **Config** from the process environment and does not open `.env`. `bot.py` loads `ROOT/.env`, calls `config.load()` once, `catalog.load(ROOT)`, builds the transport, and awaits `run_call` with that **Config**.

## Commands

```bash
cd $REPO_ROOT/sandbox/pipecat-saleslumen && uv run python -m unittest discover -s tests -t .
node --test tests/live/draw.test.js tests/live/project.test.js
uv run python bot.py -t webrtc
```

Pipecat evals (not part of the unit suite). The pipecat CLI is the `cli` extra of `pipecat-ai` 1.17.0 in the dev dependency group, installed by `uv sync`. The judge and the simulation persona LLM are OpenRouter via factory `evals.llm.openrouter` (module `evals/llm.py`), which loads the project `.env` the way `bot.py` does and reads `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from `config.load()`—not from YAML. `evals/scripted/calls.yaml`: `done_after_outcome`, `skill_then_answer`, `yeah_thanks`, `clarify_then_yeah`, `three_wrong_codes`, `wrong_then_right_number`; each shared turn is defined once as a YAML anchor, because Pipecat's `!include` cannot splice turns into a list. `evals/simulations/callers.yaml`: impatient, confused. `evals/suite.yaml` lists those two paths. Text modality default—RTVI send-text, bot skips TTS. Assertions are flow nodes and function calls.

```bash
cd $REPO_ROOT/sandbox/pipecat-saleslumen && uv run pipecat eval suite evals/suite.yaml
```

```bash
cd $REPO_ROOT/sandbox/pipecat-saleslumen && uv run pipecat eval run evals/scripted --bot-url ws://localhost:7860/ws
```

The suite command spawns the bot. For `eval run`, start `cd $REPO_ROOT/sandbox/pipecat-saleslumen && uv run python bot.py` first (no `-t`; the harness connects to the dev runner WebSocket on `ws://localhost:7860/ws`).

## Sharp edges

- Redeploy the workflow after every `revision.json` edit; watch compares the pinned revision.
- Keep `scripts/REPLACE_WITH_DEPLOYMENT_ID` in the committed revision; copy locally with the real deployment id only for publish.
- Workflow start failure logs `Workflow start failed: <code>` (safe code/reason only).
- The running bot keeps the Config from startup; restart after `.env` changes.
- One browser connect is one **Call**; reconnect for another.
- Every routed **outcome** must appear under `pipeline.yaml`; there is no default route to `end`.
- Deepgram Think rejects a request whose messages contain no user message (HTTP 400). `voice/think.py` `think_request` raises `ThinkRequestError` in that case. **`greeting`** and the first idle **`check_in`** go through `Call.say` (three LLM frames, no `LLMRunFrame`); the first Think request is still the caller's turn and therefore has a user message. The model's spoken sign-off is the goodbye on **`end_call`**. **`finish`** sets the end node and queues **`EndFrame`** after the node-entered frame. **`lines.goodbye`** is the end node's `tts_say` only for **refused** and the **second idle**; a silent **`end_call`** just ends with no second spoken goodbye.

## Done definition

- `uv run python -m unittest discover -s tests -t .` passes from `sandbox/pipecat-saleslumen`.
- Import boundary tests pass (`tests/catalog/test_import_rules.py`).
- Docs match **Call** ownership, flow state fields, layout (`call/flow.py`), and dependency direction.
- No blank lines inside function or method bodies in production packages.
