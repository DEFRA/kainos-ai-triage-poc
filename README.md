# AI triage PoC

Experiments supporting the planning consultation caseworker journey.

The project requires Python 3.12 or newer; local development is pinned to
Python 3.12.12. Use `uv sync` to create the project environment and install
dependencies.

## Consultation API

The synchronous FastAPI service runs intake, document screening, and spatial
screening. Set `AI_TRIAGE_MODEL_CONFIG` to a model configuration path, then run
the API from the repository root:

```sh
export AI_TRIAGE_MODEL_CONFIG=environment/model_configs/your-model-config.yaml
uv run --python 3.12.12 uvicorn ai_triage.api:app --reload
```

Submit case JSON to `POST /consultations`. The response contains the stage
outputs, warnings, errors, and a run ID. Retrieve a saved response with
`GET /consultations/{run_id}`. Run records and stage files are written under
`data/runs/`. The current result structure is provisional; consultation
classification rules and evidence fields have not yet been defined.

## Synchronous LLM client

The [shared LLM client](src/shared/llm_client.py) supports:

- **Ollama**, using its OpenAI-compatible Chat Completions endpoint.
- **Amazon Bedrock**, using the `bedrock-runtime` Converse API.

Install dependencies with `uv sync`. Run the following from the repository root,
in a Python script or notebook using the project environment:

```python
from pathlib import Path

from shared.llm_client import LLMClient

system_prompt = Path(
  "src/spatial_screening/prompts/answer_irz_question.md"
).read_text(encoding="utf-8")

with LLMClient("environment/model_configs/ollama.yaml") as client:
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

See [Ollama config](environment/model_configs/ollama.yaml) and
[Bedrock config](environment/model_configs/bedrock.yaml).

| Field | Meaning |
| --- | --- |
| `provider` / `api_style` | Supported pairs: `ollama` / `openai`, `bedrock` / `converse`. |
| `model` | Ollama model name, or Bedrock model/inference profile ID. |
| `base_url` | Required for Ollama, including `/v1`; optional Bedrock runtime endpoint override. |
| `api_key_env` | Environment variable holding an Ollama endpoint key; defaults to `OLLAMA_API_KEY`. |
| `region` | Required AWS region for Bedrock. |
| `aws_profile` | Optional AWS profile for Bedrock. |
| `max_output_tokens` | Positive output token limit; defaults to 1500. Thinking may consume this budget. |
| `thinking_level` | Optional `low`, `medium`, or `high`; see limitations below. |

Unknown fields, invalid settings, and missing configured secrets raise errors.
Provider SDK errors propagate with their original details. Empty responses,
truncation, tool-call responses, and blocked responses raise errors rather than
being returned as complete answers. Only final answer text is returned, not
reasoning blocks.

### Optional attachments

Pass an ordered sequence of local file paths using `attachments`. Omitting it,
passing `None`, or passing an empty list preserves text-only requests:

```python
with LLMClient("environment/model_configs/ollama.yaml") as client:
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

Imports now use `from shared.llm_client import LLMClient`; the former
screening-specific module has been removed. Existing model YAML fields,
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

Prompts stay under [spatial_screening/prompts](src/spatial_screening/prompts/), not in
environment configuration. The broader [proposed structure](structure.md)
describes future stages; this implementation uses the current source layout.

## PDF document preparation

The [shared PDF parser](src/shared/document_parser.py) uses
`pypdfium2` for PDF text extraction and page rendering, with Pillow for PNG
encoding. It does not call an LLM, perform OCR, or orchestrate screening.

```python
from shared.document_parser import parse_pdf

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

The [original document-agent implementation](src/document_screening/implementations/document_agent.py)
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

from document_screening.implementations.document_agent import run_document_screening

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

The default [screening prompt](src/document_screening/prompts/screen_documents.md)
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

The separate [prioritized implementation](src/document_screening/implementations/prioritized_document_agent.py)
retrieves and saves application metadata first, asks the model to rank each
document, downloads selected documents concurrently, then extracts and screens
only successful downloads. The ranking prompt is
[prioritize_documents.md](src/document_screening/prompts/prioritize_documents.md).
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
from document_screening.implementations.prioritized_document_agent import (
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
`<application-key>-document-screening.json`; the merged stage handoff is saved
separately as `<application-key>-final-output.json`. Existing outputs are not
overwritten; use a fresh directory for a new run. Failures propagate and leave
earlier files available for inspection.

`dpi` defaults to 150 for PDF rendering. Optional `context_limit` should match the
configured model runtime context, not merely its advertised maximum. A warning
is emitted when the rough text estimate (one token per four characters) plus the
reserved output budget reaches 80% of that limit. This estimate excludes images
and document attachments and can underestimate actual usage. It is not a full
multimodal context check. Provider context, image-count, and payload-size errors
still propagate; no oversized request is silently truncated by the agent.

## Tests

Run `uv run python -m unittest discover -s tests -v`.
Tests use an in-memory Ollama HTTP transport and Bedrock SDK stubs; they require
neither a running model server nor AWS credentials and make no inference calls.