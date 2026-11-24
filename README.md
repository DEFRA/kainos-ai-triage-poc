# AI triage PoC

Experiments supporting the planning consultation caseworker journey.

The project requires Python 3.12 or newer; local development is pinned to
Python 3.12.12. Use `uv sync` to create the project environment and install
dependencies.

## Run a consultation

The API currently runs case intake, document screening, spatial screening, and
the consultation decision in order. Case intake is a placeholder that passes
the submitted JSON through.
The configured required fields are in
[orchestrator.yaml](environment/configs/orchestrator.yaml); the current intake
requires Field objects for `lpa_reference_id` and `lpa_website`, plus the seven
case-intake signals used by the decision stage.

Optional `model_configs` entries in [orchestrator.yaml](environment/configs/orchestrator.yaml)
select separate model YAML files for `case_intake`, `document_screening`, and
`spatial_screening`. Use absolute paths or paths relative to the repository root;
`null` or omitted entries use the orchestrator's existing `model_config_path`.
The local API receives case intake as input, and the decision stage is deterministic.

1. Install the project with Python 3.12.12 and `uv sync`.
2. Edit one of the model templates in `environment/model_configs/` with a real
   model ID and endpoint or AWS region. The tracked files ending in
   `-placeholder.yaml` are examples and are not directly runnable.
3. Set `AI_TRIAGE_MODEL_CONFIG` to the absolute path of that model config, then
   start the API from the repository root:

```sh
export AI_TRIAGE_MODEL_CONFIG="$PWD/environment/model_configs/your-model-config.yaml"
uv run --python 3.12.12 uvicorn ai_triage.api:app --reload
```

4. Submit a case JSON object to `POST /consultations`, for example:

```json
{
  "lpa_reference_id": {
    "value": "25/03341/CDPN",
    "status": "stated",
    "confidence": "high",
    "reason": "Reference from the consultation.",
    "evidence": []
  },
  "lpa_website": {
    "value": "https://planning.example.gov.uk/online-applications/",
    "status": "stated",
    "confidence": "high",
    "reason": "Planning authority website.",
    "evidence": []
  },
  "signals": {
    "eia_screening": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "eia_scoping": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "conditions": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "pre_application": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "prior_approval": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "minerals_waste_existing": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []},
    "minerals_waste_new": {"classification": "no_signal", "confidence": "high", "reason": "No signal identified.", "evidence": []}
  }
}
```

The response includes a `run_id`, each stage's output, and any errors. A failed
stage stops the flow and returns the completed outputs and error details so far.
Retrieve a saved run with `GET /consultations/{run_id}`. Run records and stage
files are written under `backend/data/runs/<run_id>/` by default.

The decision stage applies the current consultation checklist and returns a
`necessary` or `unnecessary` classification with its checklist evidence. This
is a PoC decision aid; it does not send a response. Runs are synchronous and
saved to the local filesystem, so this storage is not durable across ephemeral
deployments.
For container deployments, install current CA certificates in the image and
add any organization-specific roots required to reach external planning sites.

The current Python package layout is under `backend/`; stage-specific
configuration is under `environment/configs/`, and model connection settings
are under `environment/model_configs/`.

## Component references

## Synchronous LLM client

The [LLM client](backend/src/utilities/llm_client.py) supports:

- **Ollama**, using its OpenAI-compatible Chat Completions endpoint.
- **Amazon Bedrock**, using the `bedrock-runtime` Converse API.

Install dependencies with `uv sync`. Run the following from the repository root,
in a Python script or notebook using the project environment:

```python
from pathlib import Path

from backend.src.utilities.llm_client import LLMClient

system_prompt = Path(
  "backend/src/spatial_screening/prompts/answer_irz_category.md"
).read_text(encoding="utf-8")

with LLMClient("environment/model_configs/ollama-placeholder.yaml") as client:
    answer = client.generate(
        "Question: ...\nEvidence: ...",
        system_prompt=system_prompt,
    )
    print(answer)
```

The example prompt is a starting point for experimentation, not a validated
decision policy. Answers remain subject to caseworker review.

### Model configuration

Change the config contents or pass another config path to switch providers;
the calling screening logic stays the same. Config and prompt paths are explicit:
relative paths resolve against the current working directory. Prompt loading and
templating stay with the caller; the client accepts text with optional local
file attachments and keeps no chat history.

See the [Ollama template](environment/model_configs/ollama-placeholder.yaml) and
[Bedrock template](environment/model_configs/bedrock-placeholder.yaml).

| Field                        | Meaning                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `provider` / `api_style` | Supported pairs: `ollama` / `openai`, `bedrock` / `converse`.                |
| `model`                    | Ollama model name, or Bedrock model/inference profile ID.                            |
| `base_url`                 | Required for Ollama, including `/v1`; optional Bedrock runtime endpoint override.  |
| `api_key_env`              | Environment variable holding an Ollama endpoint key; defaults to `OLLAMA_API_KEY`. |
| `region`                   | Required AWS region for Bedrock.                                                     |
| `aws_profile`              | Optional AWS profile for Bedrock.                                                    |
| `max_output_tokens`        | Positive output token limit; defaults to 1500. Thinking may consume this budget.     |
| `thinking_level`           | Optional `low`, `medium`, or `high`; see limitations below.                    |

Unknown fields, invalid settings, and missing configured secrets raise errors.
Provider SDK errors propagate with their original details. Empty responses,
truncation, tool-call responses, and blocked responses raise errors rather than
being returned as complete answers. Only final answer text is returned, not
reasoning blocks.

### Optional attachments

Pass an ordered sequence of local file paths using `attachments`. Omitting it,
passing `None`, or passing an empty list preserves text-only requests:

```python
with LLMClient("environment/model_configs/ollama-placeholder.yaml") as client:
    answer = client.generate(
        "Extract the requested facts from these pages.",
        system_prompt=system_prompt,
        attachments=[Path("page-1.png"), Path("page-2.jpg")],
    )
```

- **Ollama:** JPEG (`.jpg`/`.jpeg`), PNG, and WebP images, sent as base64
  image content through the OpenAI-compatible endpoint. Select a vision-capable
  model such as Gemma 3 12B; the currently configured Llama 3.1 8B is text-only.
  Raw PDFs are not supported here: the caller must render pages into images.
- **Bedrock:** JPEG, PNG, GIF, and WebP images, plus PDF, CSV, DOC, DOCX, XLS,
  XLSX, HTML, TXT, and Markdown documents, subject to the selected model's
  support. Documents receive neutral names (`Attachment 1`, etc.) in input
  order, rather than potentially instruction-bearing filenames. PDF document
  citations are enabled to request visual PDF processing for compatible Claude
  models. Answer text from citation blocks is returned, not citation metadata.

All attachments accompany the prompt in a single request. Formats are selected
from file suffixes, not content-signature checks. Unsupported formats, empty
files, and unreadable files raise before sending the request; no files are
silently skipped. Provider errors for model capabilities, context, payload size,
or file/image-count limits propagate unchanged. The client does not estimate
multimodal context usage, split requests, extract PDF text, or render PDF pages.
Task prompts and any output parsing remain the caller's responsibility.

The LLM client is shared by the screening stages. Existing model YAML fields,
including `api_style` and `thinking_level`, are unchanged.

### Ollama setup

Start your Ollama server and pull the model named in the config, for example
`ollama pull llama3.2`. The default URL is local to the machine running Python;
change it for a remote server. Set `OLLAMA_API_KEY` in `environment/.env` (or
set `api_key_env` to another variable name in your model config). For local
unauthenticated Ollama, any non-empty value works; for a remote authenticated
endpoint, use the key provided by that service. See
`environment/.env.example` for the variable format.

The OpenAI SDK requires a non-empty key even when the local Ollama server does
not authenticate requests.

### Bedrock setup

Authenticate through your normal AWS credential chain (for example, an AWS
profile configured through SSO, environment credentials, or an IAM role).
Enable access to the target model and permissions for Bedrock inference.
Edit `model` and `region` to match your account; the example is not guaranteed
to be available in every region/account.

Bedrock uses AWS authentication, not an Anthropic API key or an OpenAI endpoint.
No credentials belong in model YAML. The LLM client loads `.env` from the
directory above the model config; process environment variables take
precedence. These local files are git-ignored. The LPA client reads its pacing
settings from the tracked document-screening YAML described below.

### Thinking controls

If `thinking_level` is absent (or null), **no thinking parameters are sent**.
This preserves the model/server default; it does not disable thinking.

- **Ollama:** sends `reasoning_effort`. Use only with a server/model that supports
  the requested levels. The bundled `llama3.2` example deliberately omits it.
  Ollama support and interpretation vary by version/model; the client cannot
  guarantee that a server honours this setting.
- **Bedrock:** sends Claude-specific `thinking: {type: adaptive}` and
  `output_config: {effort: <level>}` through `additionalModelRequestFields`.
  This is only for Claude models supporting both adaptive thinking and effort.
  Bedrock rejects unsupported combinations; the client does not approximate
  levels with arbitrary token budgets. Other Converse models can be used with
  the field omitted.

Levels are not directly comparable across models. Fixed thinking budgets,
explicit thinking-disable controls, other providers, streaming, and async
requests are outside this initial implementation.

Prompts stay under [spatial_screening/prompts](backend/src/spatial_screening/prompts/), not in
environment configuration. The broader [proposed structure](structure.md)
describes future stages; this implementation uses the current source layout.

## PDF document preparation

The [PDF parser](backend/src/utilities/document_parser.py) uses
`pypdfium2` for PDF text extraction and page rendering, with Pillow for PNG
encoding. It does not call an LLM, perform OCR, or orchestrate screening.

```python
from backend.src.utilities.document_parser import parse_pdf

pages = parse_pdf("application.pdf", "rendered-pages", dpi=150)
for page in pages:
  print(page.source_path, page.page_number, page.text, page.image_path)

attachments = [page.image_path for page in pages]
```

Every page is returned in source order as a `ParsedPage` containing its absolute
source path, one-based page number, extracted text, and absolute PNG path. Images
are named `<source-stem>-page-0001.png`, etc. Source PDFs are not modified.
Pages with no embedded text, including image-only scans, still produce an image
and return an empty text string. Extraction does not reconstruct table layout or
guarantee semantic reading order. Resolution defaults to 150 DPI and can be
increased for small print or drawings, at the cost of image size and memory.

Rendering is sequential, with PDF/page/bitmap resources released after use.
Existing output files are never overwritten; use a new directory when rerunning
or when processing different sources with the same filename stem. Missing,
invalid, password-protected, or unreadable PDFs raise rather than being skipped.
If processing fails partway through, previously written images remain on disk.
Document selection, prompt assembly, attachment submission, and saving model
results belong to the document agent below.

## Document screening

The [original document-agent implementation](backend/src/document_screening/implementations/document_agent.py)
resolves a consultation reference, runs the LPA client's retrieval flow, prepares
PDF pages, and submits all evidence together through `LLMClient`. Select a
vision-capable model in the model YAML before processing PDF page images or other
images.

### Council Request Pacing

`PublicAccessClient` reads these settings from
`environment/configs/document_screening.yaml` when created. Exported `LPA_*`
process variables can override the YAML values; changing the YAML requires a new
client instance.

```yaml
lpa:
  request_interval_seconds: 0
  retry_increment_seconds: 0
  max_retries: 0
  max_wait_seconds: 60
  refresh_session_on_429: true
  session_refresh_wait_seconds: 60
  max_session_refreshes: 3
```

An initial interval of `0` or `None` disables routine waits. A retry increment
of `0` disables increasing the interval, and `LPA_MAX_RETRIES=0` disables
same-session retries. All three settings can be zero at once.

With `LPA_REFRESH_SESSION_ON_429=true`, each 429 immediately triggers a session
refresh instead of same-session retries. The client waits
`LPA_SESSION_REFRESH_WAIT_SECONDS` before replacing the session. A longer valid
`Retry-After` (seconds or HTTP date) takes precedence. The interval then resets
to `LPA_REQUEST_INTERVAL_SECONDS`, and the first request in the new session has
no additional pacing delay. Document downloads re-search the application when
a reference is available and rediscover the failed document by ID. Metadata
requests are retried with the fresh session as well.

The allowance is no longer shared across an application: each document or
metadata request can recover independently. `LPA_MAX_SESSION_REFRESHES` caps
consecutive refreshes for a failing operation (default 3); this separate guard
prevents endless recovery when the site remains blocked. Zero disables these
refresh attempts. Logs record refresh attempts and recovered downloads.

With session refresh disabled, the previous same-session retry approach remains
available: without a usable `Retry-After`, the interval increases by
`LPA_RETRY_INCREMENT_SECONDS` on each 429. Three retries mean four attempts.
Invalid headers warn and fall back to the configured wait for the selected mode.
Any required wait greater than `LPA_MAX_WAIT_SECONDS` raises an HTTP error rather
than shortening a server-requested cooldown. Other HTTP failures remain fatal
apart from the separate one-time document 404 rediscovery. The original
implementation downloads sequentially; prioritized screening uses its configured
global concurrency limit. Neither path adds resumability or resets server-side
rate limits.

```python
from datetime import date

from backend.src.document_screening.implementations.document_agent import run_document_screening

result = run_document_screening(
  reference="25/03341/CDPN",
  base_url="https://planningapps.sheffield.gov.uk/online-applications/",
  model_config_path="environment/model_configs/gemma3-12b.yaml",
  output_directory="data/temp/25-03341-CDPN",
  context_limit=32768,
  ignore_after=date(2026, 10, 21),
)
print(result["final_output"])
```

`output_directory` is the case directory itself, not a parent directory for all
cases. Downloads and the LPA metadata manifest are saved there. PDFs are rendered
under `rendered-pages`; both extracted text and page images are sent to the model.
`ignore_after` is optional; when set, documents published after that date are
excluded, while documents published on the date are included. Documents with
missing or unrecognized publication dates are retained with a warning.
Other files are passed directly as attachments, subject to the selected provider
and model's supported inputs. No documents/pages are silently dropped, no
automatic splitting or model retries are added, and all evidence uses one model
call. Council HTTP requests use the bounded 429 retries described above.

The default [screening prompt](backend/src/document_screening/prompts/screen_documents.md)
requests a JSON object of development and environmental evidence with source
references rather than a final consultation decision. Pass `prompt_path` to use
another prompt, or edit the default file. The agent parses the response as JSON
and merges its top-level keys with the LPA metadata. Invalid JSON or overlapping
top-level keys raise an error instead of silently dropping or overwriting data.
One enclosing Markdown JSON fence is accepted. The agent checks the expected
top-level sections and their basic container types, but does not validate every
finding field. Invalid responses are saved as
`<application-key>-document-screening-response.txt`; they are not automatically
repaired or retried.

### Prioritized document screening

The separate [prioritized implementation](backend/src/document_screening/implementations/prioritized_document_agent.py)
retrieves and saves application metadata first, asks the model to rank each
document, downloads selected documents concurrently, then extracts and screens
only successful downloads. The ranking prompt is
[prioritize_documents.md](backend/src/document_screening/prompts/prioritize_documents.md).
Model or invalid-ranking failures raise before downloading. Uncertain rankings
are forced to `must`. Per-document download failures warn and are recorded in
metadata as `downloaded: false` with a `download_error`; they do not stop other
downloads.

The implementation settings are in
[document_screening.yaml](environment/configs/document_screening.yaml). The global
`max_concurrent_downloads` limits simultaneous downloads, and `include_maybe`
controls whether `maybe` records are selected (default `false`). `ignore_after`
is optional and accepts a YAML date or `YYYY-MM-DD` string; later documents are
excluded, while same-day documents are included. Documents with missing or
unrecognized dates are retained with a warning.

```python
from backend.src.document_screening.implementations.prioritized_document_agent import (
  run_prioritized_document_screening,
)

result = run_prioritized_document_screening(
  reference="25/03341/CDPN",
  base_url="https://planningapps.sheffield.gov.uk/online-applications/",
  model_config_path="environment/model_configs/gemma3-12b.yaml",
  output_directory="data/temp/25-03341-CDPN-prioritized",
)
```

The prioritized run saves a distinct run record and final output so it can be
compared with the original implementation. Both final outputs use the same
document-findings contract.

The returned dictionary retains the original LPA `metadata`, parsed
`document_findings`, and combined `final_output`, along with source-document IDs,
one-based page/attachment mappings, raw model response text, provider/model, and
the prompt path. The run record is saved as
`<application-key>-prioritized-document-screening.json`; the merged stage handoff
is saved separately as `<application-key>-prioritized-final-output.json`.
Existing outputs are not overwritten; use a fresh directory for a new run.
Failures propagate and leave earlier files available for inspection.

`dpi` defaults to 150 for PDF rendering. Optional `context_limit` should match the
configured model runtime context, not merely its advertised maximum. A warning
is emitted when the rough text estimate (one token per four characters) plus the
reserved output budget reaches 80% of that limit. This estimate excludes images
and document attachments and can underestimate actual usage. It is not a full
multimodal context check. Provider context, image-count, and payload-size errors
still propagate; no oversized request is silently truncated by the agent.

### S3 document screening

The standalone [S3 implementation](backend/src/document_screening/implementations/s3_document_agent.py)
performs the same prioritized screening sequence and stores its generated artifacts
in S3. It does not change the document screening selected by the API orchestrator.

```python
from backend.src.document_screening.implementations.s3_document_agent import (
  run_s3_document_screening,
)
from backend.src.utilities.s3_utils import create_s3_client

result = run_s3_document_screening(
  reference="25/03341/CDPN",
  base_url="https://planningapps.sheffield.gov.uk/online-applications/",
  model_config_path="environment/model_configs/gemma3-12b.yaml",
  bucket="my-consultation-bucket",
  prefix="consultations/example/run-001",
  s3_client=create_s3_client(region="eu-west-2", profile_name="my-profile"),
)
print(result["final_output"])
print(result["storage"]["objects"])
```

The S3 client is optional; when omitted, it uses the default AWS credential chain
and `eu-west-2`. The caller needs permission to write objects to the chosen bucket.
Choose a unique, non-empty prefix per run: existing objects with the same keys
are overwritten, and a reused prefix can also retain stale objects from earlier
runs. S3 bucket versioning, if enabled, is independent of this implementation.

Under the prefix, the implementation stores the metadata JSON, successfully
downloaded documents, `rendered-pages/` images, the full prioritized run record,
and the final-output JSON. Relative `saved_as` names in the metadata and sources
are relative to that prefix. The returned `storage.objects` maps each relative
artifact path to its bucket, key, S3 URI and version; the persisted run record
retains the screening result without that storage mapping. Prompt and config paths
are recorded as local provenance, not uploaded files.

Processing still requires temporary local disk for downloads and rendered pages.
Files are uploaded after screening, one at a time; each is read into memory for
upload. If screening fails, already generated files, including an invalid model
response where available, are uploaded on a best-effort basis before temporary
cleanup. Upload errors are reported, and partially uploaded objects may remain;
there is no rollback or automatic retry of the run. Documents and extracted
evidence may contain sensitive information, so use an appropriately controlled
bucket and retention policy.

### S3 spatial screening

The standalone [S3 spatial implementation](backend/src/spatial_screening/implementations/s3_spatial_agent.py)
performs the same IRZ spatial screening sequence as the
[IRZ code agent](backend/src/spatial_screening/implementations/irz_code_agent.py)
and stores the complete result in S3. The orchestrator still uses the local IRZ
code agent.

```python
from backend.src.spatial_screening.implementations.s3_spatial_agent import (
  run_s3_spatial_screening,
)
from backend.src.utilities.s3_utils import create_s3_client

result = run_s3_spatial_screening(
  polygon=polygon,
  context=context,
  model_config_path="/absolute/path/to/model.yaml",
  bucket="my-consultation-bucket",
  prefix="consultations/example/run-001",
  s3_client=create_s3_client(region="eu-west-2", profile_name="my-profile"),
)
print(result["human_review_required"])
print(result["storage"]["s3_uri"])
```

The S3 client is optional; when omitted, it uses the default AWS credential chain
and `eu-west-2`. The result is written to `<prefix>/spatial_screening.json` without
its `storage` entry, and the returned `storage` gives the bucket, key, version and
S3 URI. Existing objects at that key are overwritten, so use a unique prefix per run.

Only the result is stored in S3. The local GIS layers and reference files under
`data/` are still required, as is access to the internal IRZ endpoint. The
context, polygon and model configuration remain caller inputs. If screening fails,
nothing is uploaded; if the upload fails, a `RuntimeError` names the destination
and chains the original cause.

### S3 decision

The standalone [S3 decision implementation](backend/src/decision/implementations/s3_decision_agent.py)
runs the same deterministic rules as the [decision module](backend/src/decision/implementations/decision_module.py)
and writes `<prefix>/decision.json` to S3. Its inputs are the stage outputs passed by the caller.

```python
from backend.src.decision.implementations.s3_decision_agent import run_s3_decision

result = run_s3_decision(
  stage_outputs,
  bucket="my-bucket",
  prefix="consultations/26-00123/decision",
)
```

### Orchestrators

Two standalone orchestrators run the consultation flow in sequence and stop on the
first stage error. Both return a run record with `run_id`, `consultation_id`,
`stage_outputs` and `errors`. A failure to save the run is added to `errors` and does
not raise.

- [Local orchestrator](backend/orchestrator/implementations/orchestrator.py): `run_consultation(case_data, model_config_path, output_directory)`
  accepts the case intake output directly, runs document screening, spatial screening and
  decision, and saves `run.json` to `output_directory`.
- [S3 orchestrator](backend/orchestrator/implementations/s3_orchestrator.py): `run_s3_consultation(...)`
  runs case intake from an email file, then the three later stages, and saves each stage
  under its own prefix. The run record is saved as `<prefix>/run.json`.

S3 layout for the S3 orchestrator:

```text
<prefix>/case_intake/
<prefix>/document_screening/        # artifacts named per application, plus the result JSON
<prefix>/spatial_screening/spatial_screening.json
<prefix>/decision/decision.json
<prefix>/run.json
```

Shared helpers for each module live in its `tools/` folder. Case intake uses its own
S3 client, so the `s3_client` passed to `run_s3_consultation` is not used for intake.

## Tests

Run commands from the repository root.

Backend orchestration tests:
`uv run python -m unittest discover -s tests/backend -p test_orchestrator.py -v`

Backend S3 tests:
`uv run python -m unittest discover -s tests/backend -p 'test_s3_*.py' -v`

Frontend tests:
`uv run python -m unittest discover -s tests/frontend -v`

Some legacy integration tests may require additional setup; avoid running them
against live services unintentionally.
