# Contributing to the voice demo

Catalog YAML and **SKILL.md** / **WORKFLOW.md** frontmatter are Pydantic models with `extra` forbid. `catalog.load` joins every agent and pipeline problem into one error that names the file and field.

`config.load` builds one **pydantic-settings** **Config** from the process environment and does not open `.env`. `bot.py` loads `.env`.

Create, revision, and watch responses are Pydantic models checked in the Workflows client. Unknown response fields are ignored. A malformed response raises **WorkflowError**. Step-join failures stay **StepError**.

## Add an agent

Create `agents/<name>/agent.yaml` and `agents/<name>/PROMPT.md`, then add an **explicit** route in `agents/pipeline.yaml` for every outcome you expect to handle. If `pipeline.yaml` does not name an outcome, routing does **not** fall through to `end`.

Example `agent.yaml` (Grant tuple: tools, connectors, workflows; Skill tuple via `skills`):

```yaml
tools:
  - find_account
  - check_passcode
connectors:
  - name: billing
    url: https://mcp.example.com/sse
workflows:
  - refund
skills:
  - saleslumen
voice: flux-alexis-en
```

Example `agents/pipeline.yaml`:

```yaml
start: verification
transitions:
  verification:
    verified: workflow
    refused: end
```

## Add a custom tool

List tool names under `tools` in that agent’s `agent.yaml`. Implement each name as an **async** function in **`agents/<name>/tools.py`**, loaded as the module **`agents.<name>.tools`**. Return a `dict`; include **`outcome`** only when `pipeline.yaml` defines a route for that key.

```python
async def find_account(digits: str) -> dict:
    ...
    return {"account_name": "Jordan"}
```

## Add an MCP connector

List `name` and `url` under `connectors` in that agent's `agent.yaml`. MCP tools are registered only on that agent’s Flows nodes, wrapped so Pipecat Flows calls `(args, flow_manager)`.

## Add a skill

Add `skills/<name>/SKILL.md`:

```markdown
---
name: saleslumen
description: Use when the caller asks what Saleslumen is.
---

Short body the model may speak when the skill is selected.
```

Grant the skill name in the agent's `agent.yaml` `skills` list (Skill tuple).

## Add a workflow

A workflow directory holds `WORKFLOW.md`, `revision.json`, and `workflows/<name>/script/`. Grant the workflow name on the workflow agent. Keep `scripts/REPLACE_WITH_DEPLOYMENT_ID` in `revision.json` until you publish Apps Script.

Example `WORKFLOW.md` frontmatter:

```markdown
---
name: refund
description: Billing, charges, refunds, or purchases.
workflow-id-env: SALESLUMEN_WORKFLOW_ID
---
```

Publish script content:

```bash
sl script content update --project SCRIPT_ID --input workflows/refund/script/content.json --json
```

Redeploy after every revision edit before testing watch parity.

## Change pipeline transitions

Edit `agents/pipeline.yaml` only. **`call/flow.py`** applies routes when a tool result includes a named **outcome**; unlisted outcomes do not default to `end`.

## Change STT, TTS, or LLM provider

Set in `.env` (see `config.py`):

| Variable | Values |
| --- | --- |
| `STT_PROVIDER` | `deepgram-flux` |
| `TTS_PROVIDER` | `deepgram-flux` |
| `LLM_PROVIDER` | `deepgram` or `openrouter` |

## Step answer kinds

Kinds come from the **signal schema** on an open `awaitSignal` step (see `execution/steps.py`):

| Signal schema | Kind | Handling |
| --- | --- | --- |
| `object{}` (no fields) | notice | Speak the label; acknowledge when the line finishes |
| Anything else | question | Speak the label with `tts_say`. The task quotes the label and each `answer_step` argument. Call `answer_step` only after the caller answers |

## Verification tools

The verification agent exposes `find_account` and `check_passcode`. **`flow_manager.state`** may hold `account_name`, `passcode_attempts`, and `account_misses` only.

- **`find_account`** — Count an **account miss** when the digits are not what the caller just said or the account is missing. The third account miss returns **refused** (which enters the end node).
- **`check_passcode`** — The passcode matches only when both the tool argument and the caller’s latest user message are the code. **`passcode_attempts`** is a separate counter from **`account_misses`**.

## Live call notes

- Workflow step labels use non-interruptible TTS where the pipeline requires verbatim lines.
- A second `start_workflow` in one call uses execution id `{call key}-{attempt}`; **Call** increments **attempt** from 1.
- The running bot keeps the environment from its single `config.load()`; restart after `.env` edits.
