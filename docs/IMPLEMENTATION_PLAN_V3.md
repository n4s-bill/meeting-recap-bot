# Meeting Recap Bot -- Implementation Plan v3

## 1. Architecture Overview

The system is a single-container Python service running locally on Bill's machine. It receives meeting transcripts from Otter.ai via a Zapier webhook, generates structured summaries using OpenAI, and distributes recap emails via Microsoft Graph.

There is **one ingestion mode in v1: Zapier webhook**. Otter.ai REST API polling is deferred to a future version.

### v1 Pipeline

```
Otter transcript completed
  -> Zapier trigger ("New Transcript")
  -> POST to local webhook endpoint (http://localhost:8000/webhook/transcript)
  -> Validate WEBHOOK_SECRET
  -> Deduplicate via processed_meetings.json
  -> Resolve recipients (payload -> distro list -> Bill fallback)
  -> Summarize transcript via OpenAI (instructions.md as system prompt)
  -> Send email via Microsoft Graph (to=recipients, cc=Bill)
  -> Mark meeting as processed
```

### Architecture Diagram

```
+-----------+       +--------+       +----------------------------+
| Otter.ai  | ----> | Zapier | ----> | meeting-recap-bot (Docker) |
+-----------+       +--------+       |                            |
                                     |  webhook_server.py         |
                                     |    |                       |
                                     |    v                       |
                                     |  storage.py (dedupe)       |
                                     |    |                       |
                                     |    v                       |
                                     |  recipient_resolver.py     |
                                     |    |                       |
                                     |    v                       |
                                     |  summarizer.py -> OpenAI   |
                                     |    |                       |
                                     |    v                       |
                                     |  emailer.py -> MS Graph    |
                                     +----------------------------+
                                               |
                                               v
                                     processed_meetings.json
```

---

## 2. Project Structure

```
meeting-recap-bot/
├── config.py                  # Env var loading, defaults, validation
├── main.py                    # Entrypoint: starts FastAPI via uvicorn
├── webhook_server.py          # FastAPI app with POST /webhook/transcript
├── summarizer.py              # OpenAI Chat Completions caller
├── emailer.py                 # Microsoft Graph REST email sender (httpx)
├── recipient_resolver.py      # Tiered recipient resolution logic
├── storage.py                 # JSON file-based dedupe tracking
├── meeting_type_config.py     # Loads and matches meeting_types.json
├── pipeline.py                # Orchestrates: dedupe -> resolve -> summarize -> email
├── instructions.md            # ChatGPT Project system prompt (user-provided, DO NOT MODIFY)
├── meeting_types.json         # Optional distro list mapping by meeting type
├── requirements.txt           # Pinned dependencies
├── .env.example               # Documented env var template
├── Dockerfile                 # Container image definition
├── docker-compose.yml         # Local Docker orchestration
├── README.md                  # Setup, config, and usage guide
├── fixtures/
│   └── sample_otter_webhook.json   # Mock payload for local testing
└── tests/
    ├── __init__.py
    ├── conftest.py             # Shared fixtures (mock clients, sample data)
    ├── test_recipient_resolver.py
    ├── test_meeting_type_config.py
    ├── test_storage.py
    ├── test_webhook_server.py
    ├── test_summarizer.py
    ├── test_emailer.py
    └── test_pipeline_integration.py
```

---

## 3. Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | -- | OpenAI API authentication |
| `MS_GRAPH_CLIENT_ID` | Yes | -- | Azure AD app registration client ID |
| `MS_GRAPH_CLIENT_SECRET` | Yes | -- | Azure AD app registration client secret |
| `MS_GRAPH_TENANT_ID` | Yes | -- | Azure AD tenant ID |
| `EMAIL_FROM` | Yes | -- | Sender mailbox address (must have Graph send permissions) |
| `EMAIL_CC` | No | `bill.johnson@scribendi.com` | CC recipient on all recap emails |
| `WEBHOOK_SECRET` | Yes | -- | Shared secret for authenticating Zapier requests |
| `MAX_TRANSCRIPT_CHARS` | No | `100000` | Maximum transcript length before truncation |
| `OPENAI_MODEL` | No | `gpt-4o` | Model used for summarization |
| `WEBHOOK_HOST` | No | `0.0.0.0` | Host to bind the webhook server |
| `WEBHOOK_PORT` | No | `8000` | Port to bind the webhook server |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

Variables **not** included in v1 (no Otter API polling):
- ~~OTTER_CLIENT_ID~~
- ~~OTTER_CLIENT_SECRET~~
- ~~POLL_INTERVAL_SECONDS~~
- ~~INGESTION_MODE~~

---

## 4. Component Responsibilities

### 4.1 `config.py` -- Configuration

Loads all settings from environment variables using `python-dotenv`. Validates that all required variables are present at startup and fails fast with a clear error message if any are missing.

Responsibilities:
- Load `.env` file if present (local dev convenience).
- Parse and validate all required env vars.
- Provide typed access (e.g., `config.MAX_TRANSCRIPT_CHARS` returns an `int`).
- Expose defaults for optional vars.

### 4.2 `main.py` -- Entrypoint

Starts the FastAPI webhook server via uvicorn. Configures logging (format, level). Validates configuration on startup. Loads `instructions.md` and `meeting_types.json` eagerly so failures are caught immediately.

```
if __name__ == "__main__":
    validate_config()
    load_instructions()
    load_meeting_types()
    uvicorn.run(app, host=WEBHOOK_HOST, port=WEBHOOK_PORT)
```

### 4.3 `webhook_server.py` -- Zapier Webhook Receiver

A FastAPI application with a single endpoint:

**`POST /webhook/transcript`**

Responsibilities:
- Validate the `WEBHOOK_SECRET` (sent as `X-Webhook-Secret` header or `Authorization: Bearer <secret>`).
- Parse the incoming JSON payload into a `WebhookPayload` Pydantic model.
- Call `pipeline.process_meeting()` with the parsed data.
- Return `200 OK` on success (with `{"status": "processed"}`), `200 OK` if already processed (with `{"status": "duplicate", "meeting_id": "<id>"}`), `401 Unauthorized` if auth fails, `422 Unprocessable Entity` if payload is malformed.

Note: 409 is semantically correct REST but Zapier treats all non-2xx responses as failures and retries them. Returning 200 for duplicates prevents Zapier from retrying an already-handled meeting indefinitely.

**`GET /health`**

Returns `200 OK` with `{"status": "healthy"}`. Used to verify the container is running.

### 4.4 `summarizer.py` -- OpenAI Summarizer

Loads `instructions.md` once at module initialization. Calls the OpenAI Chat Completions API.

API call construction (must match existing ChatGPT Project behavior exactly):
- **System message:** Full contents of `instructions.md`, unmodified.
- **User message:** `"generate meeting summary for this as per the instructions without citations\n\n"` followed by the transcript text (truncated if necessary per Section 8).
- **Model:** Value of `OPENAI_MODEL` (default `gpt-4o`).
- **Temperature:** `0.3` for deterministic output.

Responsibilities:
- Load and cache instructions from file.
- Apply transcript size policy (see Section 8).
- Call OpenAI API with retry logic (3 attempts, exponential backoff).
- Return the model's response content as a string.
- Raise a clear exception if summarization fails after retries.

### 4.5 `emailer.py` -- Microsoft Graph Email Sender

Uses `azure-identity` for token acquisition and `httpx` for direct REST calls to the Microsoft Graph API. Does **not** use the `msgraph-sdk` package.

**Authentication flow:**
1. Create a `ClientSecretCredential` from `azure-identity` using `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, and `MS_GRAPH_TENANT_ID`.
2. Call `credential.get_token("https://graph.microsoft.com/.default")` to obtain a bearer token.
3. Include the token in the `Authorization: Bearer <token>` header on all Graph REST requests.

**Token caching implementation pattern:**

The `ClientSecretCredential` must be instantiated once and reused across all requests. Do not instantiate it per webhook call. Use a module-level lazy singleton:

```python
_credential = None

def _get_credential():
    global _credential
    if _credential is None:
        _credential = ClientSecretCredential(
            tenant_id=config.MS_GRAPH_TENANT_ID,
            client_id=config.MS_GRAPH_CLIENT_ID,
            client_secret=config.MS_GRAPH_CLIENT_SECRET,
        )
    return _credential
```

The credential must not be constructed at module import time, as `config.py` may not have been initialized yet. Always initialize via the getter function, which is first called during pipeline execution after startup validation has completed.

**Send email via REST:**

```
POST https://graph.microsoft.com/v1.0/users/{EMAIL_FROM}/sendMail
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": {
    "subject": "[Meeting Recap] <Title> — <Date>",
    "body": {
      "contentType": "HTML",
      "content": "<sanitized HTML summary>"
    },
    "toRecipients": [
      {"emailAddress": {"address": "recipient@example.com"}}
    ],
    "ccRecipients": [
      {"emailAddress": {"address": "bill.johnson@scribendi.com"}}
    ]
  },
  "saveToSentItems": true
}
```

**HTML rendering and sanitization:**

The OpenAI model returns markdown. Before sending:
1. Convert markdown to HTML using the `markdown` library.
2. Sanitize the HTML using `nh3` to strip any unsafe tags or attributes. Allow a safe subset: `p`, `h1`-`h6`, `ul`, `ol`, `li`, `strong`, `em`, `a`, `br`, `blockquote`, `code`, `pre`. Allow `href` on `a` tags only. Use `nh3.clean(html, tags={...}, attributes={...})`.

**Azure AD prerequisites (must be validated before implementation):**
1. An Azure AD app registration must exist with `Mail.Send` **Application** permission.
2. Admin consent must be granted for the `Mail.Send` permission.
3. The `EMAIL_FROM` mailbox must permit the registered app to send on its behalf. If the tenant uses Exchange Online application access policies, the app must be included in the policy scope for the sender mailbox.

Email construction:
- **Subject:** `[Meeting Recap] <Meeting Title> — <Date as MMM DD, YYYY>`
- **Body:** Sanitized HTML-formatted summary (markdown -> HTML -> nh3).
- **To:** Resolved recipient list (see Section 6).
- **CC:** `EMAIL_CC` (defaults to `bill.johnson@scribendi.com`).
- **From:** `EMAIL_FROM` (configured sender mailbox, used in the REST URL path).

Responsibilities:
- Acquire and cache Graph API token (refresh when expired).
- Build the sendMail JSON payload with proper recipient structure.
- POST to `https://graph.microsoft.com/v1.0/users/{EMAIL_FROM}/sendMail` via `httpx`.
- Handle errors with retry logic (3 attempts, exponential backoff).
- Send failure-notification email to Bill if summarization fails (see Section 7).

### 4.6 `recipient_resolver.py` -- Recipient Resolution

Implements a three-tier fallback strategy for determining email recipients.

**Resolution order (must follow exactly):**

1. **Payload participants:** If the webhook payload contains participant email addresses, use them as the `To` recipients.
2. **Meeting-type distro list:** If participant emails are absent or empty, check the meeting title against `meeting_types.json`. If a match is found, use the mapped distribution list as `To` recipients.
3. **Bill fallback:** If neither source produces recipients, send only to `EMAIL_CC` (Bill) as the sole `To` recipient.

Bill is **always** CC'd regardless of which tier resolves the recipients. If Bill is the sole `To` recipient (tier 3), he is not also added as CC (avoid duplicate delivery).

Input: meeting title (string), participant emails (list, possibly empty).
Output: `ResolvedRecipients` with `to: list[str]` and `cc: list[str]`.

### 4.7 `meeting_type_config.py` -- Meeting Type Distro List

Loads `meeting_types.json` at startup and provides keyword matching against meeting titles.

**`meeting_types.json` format:**

```json
{
  "engineering standup": ["engineering-team@company.com"],
  "product sync": ["product-team@company.com", "pm-leads@company.com"],
  "client review": ["client-success@company.com"]
}
```

**Matching logic:**
- Case-insensitive substring match.
- The meeting title is checked against each key in `meeting_types.json`.
- First match wins (keys are checked in file order).
- If no key matches, return an empty list.

Example: A meeting titled "Weekly Engineering Standup" matches the key `"engineering standup"` and returns `["engineering-team@company.com"]`.

The file is optional. If `meeting_types.json` does not exist or is empty, this tier is skipped silently.

### 4.8 `storage.py` -- Duplicate Prevention

Tracks processed meetings in a local JSON file (`processed_meetings.json`).

**File format:**

```json
{
  "abc123": {"processed_at": "2026-03-10T15:00:00Z", "title": "Weekly Standup"},
  "def456": {"processed_at": "2026-03-10T16:30:00Z", "title": "Sprint Review"}
}
```

The key is the meeting ID from the webhook payload.

Methods:
- `is_processed(meeting_id: str) -> bool`
- `mark_processed(meeting_id: str, title: str) -> None`

File locking: Use `filelock` library for safe concurrent access (even though v1 is single-instance, this guards against overlapping webhook requests).

Auto-creates the file if it does not exist. If the file is corrupted (invalid JSON), logs a critical error, backs up the corrupted file as `processed_meetings.json.corrupt.<timestamp>`, and starts fresh.

### 4.9 `pipeline.py` -- Shared Orchestration

The core workflow that ties all components together. Called by the webhook handler.

```
def process_meeting(payload: WebhookPayload) -> ProcessingResult:
    1. Check storage.is_processed(payload.meeting_id)
       -> If yes: log "skipping duplicate" and return DUPLICATE status
    2. Resolve recipients via recipient_resolver
    3. Apply transcript size policy (truncate if over MAX_TRANSCRIPT_CHARS; see Section 8)
    4. Call summarizer.generate_summary(transcript_text)
       -> If fails: send failure notification to Bill, return FAILED status
    5. Call emailer.send_recap(recipients, meeting_title, date, summary)
       -> If fails: do NOT mark processed, return FAILED status
    6. Call storage.mark_processed(payload.meeting_id, payload.title)
    7. Return SUCCESS status
```

**Key invariant:** A meeting is only marked as processed AFTER the email is successfully sent. Failures at any step leave the meeting unprocessed so it can be retried on the next webhook delivery.

**Async/sync boundary:** `pipeline.process_meeting()` is defined as a synchronous function (`def`, not `async def`). FastAPI automatically runs synchronous route handler dependencies in a thread pool, so there is no need to wrap storage calls in `asyncio.to_thread()`. The storage methods in `storage.py` use synchronous file I/O with `filelock`, which is correct for a sync context. Do not make `process_meeting()` async -- doing so would block the event loop on file I/O without additional wrapping.

---

## 5. Webhook Payload Validation Strategy

### Mandatory first implementation step

**Before writing the payload parser, capture the actual Zapier payload.**

The exact JSON shape sent by Zapier's Otter.ai integration is not documented with certainty. The implementation must begin with:

1. Deploy a bare-minimum webhook endpoint that logs the raw request body to a file.
2. Configure a Zapier Zap: Otter.ai "New Transcript" trigger -> Webhooks by Zapier "POST" action pointing to the local endpoint.
3. Run a test meeting in Otter.ai and capture the payload.
4. Save the captured payload as `fixtures/sample_otter_webhook.json`.
5. Only then define the Pydantic model in `webhook_server.py` to match the actual payload.

### Assumed payload structure (to be validated)

Based on Zapier's Otter.ai integration documentation, the payload likely contains:

```json
{
  "meeting_id": "string",
  "title": "string",
  "date": "ISO 8601 datetime string",
  "participants": ["email@example.com"],
  "transcript": "Full transcript text..."
}
```

Field names, nesting, and presence of participant emails are all assumptions that **must be validated** against the real Zapier output before finalizing the Pydantic model.

The Pydantic model should use `Optional` types with defaults for any fields that might be absent (especially `participants`), and the webhook handler should log unexpected fields for future debugging.

### Fixture file

After capturing the real payload, save it as:

```
fixtures/sample_otter_webhook.json
```

This fixture serves three purposes:
- Reference for the Pydantic model definition.
- Input for unit and integration tests.
- Manual testing with `curl`.

---

## 6. Recipient Resolution Logic

### Three-tier fallback

```
Input: payload.participants, payload.title
                |
                v
     +---------------------+
     | Participants exist   |---Yes---> To: participants, CC: Bill
     | in payload?          |
     +---------------------+
                |
               No
                v
     +---------------------+
     | Meeting title        |---Yes---> To: distro list, CC: Bill
     | matches distro list? |
     +---------------------+
                |
               No
                v
     To: Bill (EMAIL_CC), CC: (none)
```

### Edge cases

- If `participants` is present but all entries are empty strings or invalid, treat as absent (fall through to tier 2).
- If Bill's email appears in the `To` list, do not also add him to CC.
- If `meeting_types.json` is missing or empty, tier 2 is skipped.
- Email addresses are normalized to lowercase before comparison and deduplication.

---

## 7. Error Handling Strategy

### Per-component error handling

| Component | Error | Action |
|---|---|---|
| **Webhook auth** | Invalid/missing secret | Return 401 immediately. Log warning with `meeting_id` if available. |
| **Payload parsing** | Malformed JSON or missing required fields | Return 422. Log the raw payload (excluding transcript body for size). |
| **Duplicate check** | Storage file read error | Log critical error. Attempt to recreate file. Process the meeting (fail open rather than dropping meetings). |
| **Recipient resolution** | `meeting_types.json` parse error | Log warning. Skip tier 2, fall through to tier 3 (Bill fallback). |
| **Transcript size** | Exceeds `MAX_TRANSCRIPT_CHARS` | Truncate and append marker. Log warning. Continue processing (see Section 8). |
| **OpenAI API** | Rate limit (429), server error (5xx) | Retry 3 times with exponential backoff (1s, 4s, 16s). |
| **OpenAI API** | All retries exhausted | Log error. Send failure notification email to Bill. Return 500 to Zapier (Zapier will retry). Do NOT mark processed. |
| **Microsoft Graph** | Auth failure (401/403) | Log critical error. Return 500. Do NOT mark processed. |
| **Microsoft Graph** | Send failure (4xx/5xx) | Retry 3 times. If all fail, log error. Return 500. Do NOT mark processed. |

### Failure notification email

If summarization fails after all retries, send a short notification email to Bill:

- **To:** `EMAIL_CC` (Bill)
- **Subject:** `[Meeting Recap - FAILED] <Meeting Title> — <Date>`
- **Body:** "Automatic summarization failed for this meeting. The transcript was received but could not be processed. Please generate the summary manually. Error: <error message>"

This ensures Bill knows a meeting was missed and can handle it manually.

### Zapier retry behavior

Zapier retries webhooks that return non-2xx responses. By returning 500 on transient failures (and not marking the meeting as processed), the system relies on Zapier's built-in retry mechanism as an additional recovery layer. The deduplication check prevents double-processing when a retry succeeds.

---

## 8. Transcript Size Policy

### v1 approach: truncate if over threshold, always process

Full transcript chunking and multi-pass summarization is out of scope for v1. The policy is simple: truncate and continue. A meeting is never skipped solely because the transcript is long.

**Policy:**

1. **Threshold:** `MAX_TRANSCRIPT_CHARS` (default: `100000` characters, approximately 25,000 words or a ~2.5 hour meeting).
2. **At or under threshold:** Process the transcript as-is with no modifications.
3. **Over threshold:**
   - Log a warning: `"[meeting_id] Transcript for '<title>' is <N> chars (threshold: <MAX>). Truncating."`
   - Truncate the transcript to `MAX_TRANSCRIPT_CHARS` characters.
   - Append the marker: `\n\n[Transcript truncated due to size limit]`
   - Continue processing normally with the truncated transcript.

The truncated transcript (with appended marker) is passed to the summarizer as the user message content. The summarizer and email pipeline treat it identically to a normal-sized transcript.

### Token estimation

100,000 characters is approximately 25,000 tokens. GPT-4o supports 128k context tokens. With the system prompt (`instructions.md`) likely under 3,000 tokens, this leaves ample room. The default threshold is conservative and can be raised if needed.

---

## 9. Testing Strategy

### Directory structure

```
tests/
├── __init__.py
├── conftest.py                       # Shared fixtures, mock factories
├── test_recipient_resolver.py        # Unit: resolution tiers + edge cases
├── test_meeting_type_config.py       # Unit: keyword matching, missing file
├── test_storage.py                   # Unit: dedupe, file creation, corruption
├── test_webhook_server.py            # Unit: auth validation, payload parsing
├── test_summarizer.py                # Unit: request construction, size policy
├── test_emailer.py                   # Unit: email payload, sanitization, Graph REST call
└── test_pipeline_integration.py      # Integration: full and partial flows
```

### Unit tests

**`test_recipient_resolver.py`**
- Participants present in payload -> returns them as To, Bill as CC.
- Participants empty -> falls through to distro list.
- Participants absent -> falls through to distro list.
- Distro list match found -> returns distro list as To, Bill as CC.
- No distro list match -> Bill as sole To, no CC.
- Bill in participants list -> not duplicated in CC.
- Invalid email strings in participants -> filtered out, fall through if none remain.

**`test_meeting_type_config.py`**
- Exact substring match (case-insensitive).
- First-match-wins ordering.
- No match returns empty list.
- Missing `meeting_types.json` returns empty list.
- Malformed JSON logs warning and returns empty list.

**`test_storage.py`**
- `is_processed` returns False for new meeting ID.
- `is_processed` returns True after `mark_processed`.
- File auto-created if missing.
- Corrupted file handled gracefully (backup + fresh start).
- Concurrent access does not corrupt file (test with threading).

**`test_webhook_server.py`**
- Valid secret -> 200.
- Missing secret header -> 401.
- Wrong secret -> 401.
- Malformed JSON body -> 422.
- Missing required fields -> 422.
- Duplicate meeting -> 200 with `{"status": "duplicate"}`.

**`test_summarizer.py`**
- System message is exactly the contents of `instructions.md`.
- User message starts with the required prefix followed by transcript.
- Model parameter matches `OPENAI_MODEL` config.
- Transcript under threshold -> passed unmodified.
- Transcript over threshold -> truncated with `[Transcript truncated due to size limit]` marker appended.

**`test_emailer.py`**
- Subject format: `[Meeting Recap] <Title> — <MMM DD, YYYY>`.
- To recipients match resolved list.
- CC contains Bill's email.
- Body is HTML (markdown converted).
- HTML body is sanitized (no script tags, no event handlers, only allowed tags/attributes survive).
- Graph REST call is a POST to the correct URL with correct Authorization header.
- sendMail JSON payload matches the expected Graph API schema.
- Failure notification email has correct subject and body.

### Integration tests

**`test_pipeline_integration.py`**

These tests use mocked external services (OpenAI, Microsoft Graph) but exercise the full internal pipeline.

- **Happy path:** Webhook with valid payload -> summary generated -> email sent -> meeting marked processed.
- **Duplicate meeting:** Same meeting ID posted twice -> first succeeds with `{"status": "processed"}`, second returns 200 with `{"status": "duplicate"}`.
- **Missing participant emails:** Payload without participants -> falls through to distro list or Bill.
- **Invalid webhook secret:** Returns 401, no processing occurs.
- **OpenAI API failure:** Mock OpenAI to return 500 -> failure notification sent to Bill -> meeting NOT marked processed.
- **Microsoft Graph failure:** Mock Graph REST endpoint to return 500 -> meeting NOT marked processed.
- **Oversized transcript:** Transcript over threshold -> truncated with marker, processed normally, email sent.

### Test tooling

- **Framework:** `pytest` with `pytest-asyncio` for async tests.
- **OpenAI mocking:** Use `unittest.mock.patch` to mock the OpenAI client directly. Do **not** use `respx` for OpenAI calls. The OpenAI SDK wraps `httpx` internally in a way that makes transport-level interception unreliable for testing retry logic and response handling. Patch at the client level instead, e.g. `unittest.mock.patch('summarizer.openai_client')` or the equivalent module-level client reference.
- **Graph REST mocking:** Use `respx` to mock the `httpx` calls made directly in `emailer.py` to `https://graph.microsoft.com`. This is straightforward to intercept since `emailer.py` owns the `httpx` client directly.
- **FastAPI test client:** `httpx.AsyncClient` with `ASGITransport` for testing the webhook endpoint directly.
- **Fixtures:** All tests use the payload from `fixtures/sample_otter_webhook.json` as the baseline, with per-test modifications. Note: this file must exist (Phase 1 stop point) before test implementation begins.

---

## 10. Pinned Dependencies

### `requirements.txt`

```
openai==2.26.0
azure-identity==1.25.2
fastapi==0.135.1
uvicorn[standard]==0.41.0
python-dotenv==1.2.2
Markdown==3.10.2
nh3==0.3.3
httpx==0.28.1
filelock==3.17.0
pydantic==2.11.1
```

**Note:** `msgraph-sdk` is not used. Email sending calls the Microsoft Graph REST API directly via `httpx`, with `azure-identity` handling token acquisition.

### `requirements-dev.txt` (test dependencies)

```
pytest==9.0.2
pytest-asyncio==0.25.3
respx==0.22.0
```

---

## 11. Local Docker Deployment Strategy

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"]
CMD ["python", "main.py"]
```

The `HEALTHCHECK` instruction polls the `/health` endpoint every 30 seconds. Docker marks the container as unhealthy if the check fails 3 times in a row, which makes problems visible via `docker ps` and `docker compose ps`.

### docker-compose.yml

```yaml
services:
  recap-bot:
    build: .
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - ./processed_meetings.json:/app/processed_meetings.json
      - ./instructions.md:/app/instructions.md
      - ./meeting_types.json:/app/meeting_types.json
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"]
      interval: 30s
      timeout: 5s
      start_period: 10s
      retries: 3
```

The `processed_meetings.json` volume mount ensures the deduplication state persists across container restarts. The health check is defined in both the Dockerfile (for standalone `docker run`) and docker-compose.yml (for compose-based usage).

### Bill's workflow

1. Clone the repo.
2. Copy `.env.example` to `.env` and fill in credentials.
3. Place ChatGPT Project instructions in `instructions.md`.
4. Optionally configure `meeting_types.json`.
5. Run: `docker compose up -d`
6. Verify health: `docker compose ps` (should show "healthy" status after ~30s).
7. Verify endpoint: `curl http://localhost:8000/health`
8. Logs: `docker compose logs -f recap-bot`
9. Stop: `docker compose down`

### Exposing to Zapier

Since the service runs locally, Bill needs to expose port 8000 to the internet for Zapier to reach it. Options:

- **ngrok:** `ngrok http 8000` provides a public URL. Free tier is sufficient. The ngrok URL changes on restart (use a paid plan for a stable subdomain, or update the Zapier Zap URL after each restart).
- **Cloudflare Tunnel:** `cloudflared tunnel` provides a stable public URL mapped to localhost:8000. Free and more reliable than ngrok for always-on use.

The README must document both options with step-by-step instructions.

---

## 12. Local Manual Test Strategy

### Purpose

Before wiring up Zapier, the entire pipeline should be testable locally using `curl` and the sample fixture. This workflow validates that config is correct, OpenAI returns a summary, and Graph sends an email -- without needing Otter.ai or Zapier.

### Sample fixture

`fixtures/sample_otter_webhook.json` (placeholder until real payload is captured):

```json
{
  "meeting_id": "test-meeting-001",
  "title": "Weekly Engineering Standup",
  "date": "2026-03-10T14:00:00Z",
  "participants": [
    "alice@scribendi.com",
    "bob@scribendi.com"
  ],
  "transcript": "Alice: Good morning everyone. Let's go through the standup updates.\n\nBob: I worked on the API refactor yesterday. Today I'm continuing with the test coverage.\n\nAlice: Great. Any blockers?\n\nBob: No blockers at the moment.\n\nAlice: Perfect. Let's wrap up. Thanks everyone."
}
```

### Step-by-step local test workflow

**1. Start the service:**

```bash
# Option A: Docker (recommended for final validation)
docker compose up --build

# Option B: Direct Python (faster iteration during development)
pip install -r requirements.txt
python main.py
```

**2. Verify the server is running:**

```bash
curl http://localhost:8000/health
# Expected: {"status": "healthy"}
```

**3. Send the sample webhook:**

```bash
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret-here" \
  -d @fixtures/sample_otter_webhook.json
```

Expected response (first run):
```json
{"status": "processed", "meeting_id": "test-meeting-001"}
```

**4. Verify duplicate rejection:**

```bash
# Send the same payload again
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret-here" \
  -d @fixtures/sample_otter_webhook.json
```

Expected response (second run):
```json
{"status": "duplicate", "meeting_id": "test-meeting-001"}
```

**5. Verify auth rejection:**

```bash
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: wrong-secret" \
  -d @fixtures/sample_otter_webhook.json
```

Expected: `401 Unauthorized`

**6. Check the logs:**

Look for the full processing sequence in the console or `docker compose logs`:
- `Webhook received for meeting: Weekly Engineering Standup (id: test-meeting-001)`
- `Recipients resolved via payload: to=[alice@scribendi.com, bob@scribendi.com], cc=[bill.johnson@scribendi.com]`
- `Summary generated for: Weekly Engineering Standup`
- `Recap email sent for: Weekly Engineering Standup to 2 recipients`
- `Meeting marked processed: Weekly Engineering Standup (id: test-meeting-001)`

**7. Verify email delivery:**

Check that the recap email arrived in the `EMAIL_FROM` mailbox's Sent Items, in the participants' inboxes, and in Bill's inbox.

**8. Reset for re-testing:**

To test the same meeting again, delete the entry from `processed_meetings.json` or delete the file entirely.

### Running automated tests

```bash
# Install dev dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## 13. Logging

All logging uses Python's built-in `logging` module.

**Format:** `%(asctime)s %(levelname)s [%(name)s] %(message)s`

**Level:** Configurable via the `LOG_LEVEL` environment variable (default: `INFO`).

**Key rule:** Every log line emitted after payload parsing must include `meeting_id` so logs can be correlated to a specific meeting. Use the pattern `[meeting_id] message` in log messages.

### Required log points

| Event | Level | Example message |
|---|---|---|
| Server started | INFO | `Webhook server started on 0.0.0.0:8000` |
| Webhook received | INFO | `[test-meeting-001] Webhook received for meeting: Weekly Engineering Standup` |
| Auth failed | WARNING | `Webhook auth failed from 192.168.1.10` |
| Duplicate skipped | INFO | `[test-meeting-001] Skipping duplicate meeting: Weekly Engineering Standup` |
| Recipients resolved | INFO | `[test-meeting-001] Recipients resolved via payload: to=[alice@co.com], cc=[bill@co.com]` |
| Transcript truncated | WARNING | `[test-meeting-001] Transcript for 'Weekly Standup' is 120000 chars (threshold: 100000). Truncating.` |
| Summary generated | INFO | `[test-meeting-001] Summary generated for: Weekly Engineering Standup (4200 tokens used)` |
| Summary failed | ERROR | `[test-meeting-001] OpenAI API error for 'Weekly Standup': RateLimitError. Attempt 2/3.` |
| Email sent | INFO | `[test-meeting-001] Recap email sent for: Weekly Engineering Standup to 2 recipients` |
| Email failed | ERROR | `[test-meeting-001] Microsoft Graph error for 'Weekly Standup': 403 Forbidden. Attempt 1/3.` |
| Failure notification | WARNING | `[test-meeting-001] Failure notification sent to Bill for: Weekly Engineering Standup` |
| Meeting processed | INFO | `[test-meeting-001] Meeting marked processed: Weekly Engineering Standup` |

---

## 14. Data Flow (Detailed)

### Happy path sequence

```
Zapier POST -> webhook_server.py
  |
  +--> Validate X-Webhook-Secret header
  |      (fail: 401)
  |
  +--> Parse JSON body into WebhookPayload (Pydantic)
  |      (fail: 422)
  |
  +--> pipeline.process_meeting(payload)
         |
         +--> storage.is_processed(meeting_id)
         |      (if True: return 200 {"status": "duplicate"})
         |
         +--> recipient_resolver.resolve(
         |        participants=payload.participants,
         |        title=payload.title
         |    )
         |    -> ResolvedRecipients(to=[...], cc=[...])
         |
         +--> Check transcript length against MAX_TRANSCRIPT_CHARS
         |      (if over: truncate and append "[Transcript truncated due to size limit]")
         |
         +--> summarizer.generate_summary(transcript_text)
         |      System: contents of instructions.md
         |      User: "generate meeting summary for this as per the instructions
         |             without citations\n\n{transcript}"
         |      -> summary string
         |      (fail after retries: notify Bill, return 500)
         |
         +--> emailer.send_recap(
         |        to=resolved.to,
         |        cc=resolved.cc,
         |        subject="[Meeting Recap] {title} — {date}",
         |        body=sanitize(markdown_to_html(summary))
         |    )
         |    -> POST https://graph.microsoft.com/v1.0/users/{EMAIL_FROM}/sendMail
         |    (fail after retries: return 500, do NOT mark processed)
         |
         +--> storage.mark_processed(meeting_id, title)
         |
         +--> return 200 {"status": "processed"}
```

---

## 15. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **Zapier payload shape differs from assumption** | Webhook parser breaks | High | Mandatory first step: capture real payload before coding parser. Use permissive Pydantic model with Optional fields. |
| **Otter.ai does not include participant emails in Zapier payload** | Cannot send to attendees | Medium | Tier 2 (distro list) and tier 3 (Bill fallback) handle this. Bill can manually forward if needed. |
| **ngrok/tunnel URL changes** | Zapier stops reaching the webhook | Medium | Document Cloudflare Tunnel as the preferred stable option. Include URL update instructions for Zapier in README. |
| **Microsoft Graph `Mail.Send` permission not granted** | Emails fail | Medium | Validate permissions before implementation. Include step-by-step Azure AD setup in README. Test with a manual Graph API call first. |
| **Exchange Online application access policy blocks sending** | Emails fail silently or with 403 | Medium | Document the requirement to add the app to the access policy scope for the sender mailbox. Test with a simple send before building the full system. |
| **OpenAI rate limits on long transcripts** | Summarization blocked | Low | Retry with backoff. Transcript size policy limits input size. `gpt-4o` has generous rate limits. |
| **`processed_meetings.json` grows indefinitely** | Disk usage (minor) | Low | v1 acceptable for months of use (each entry is ~100 bytes). Future enhancement: add a cleanup that removes entries older than 90 days. |
| **Bill's machine restarts / Docker stops** | Zapier webhooks fail | Low | `restart: unless-stopped` in docker-compose. Zapier retries failed webhooks. Meetings not marked processed will be retried. |
| **`instructions.md` accidentally modified** | Summary quality degrades | Low | Mark file as read-only. Add a startup check that logs a hash of the file. Document "DO NOT MODIFY" prominently. |

---

## 16. Implementation Order

Implementation is split into two phases with a mandatory human stop-point between them.

### Phase 1 -- Implement before payload is captured (Cursor)

1. **Scaffold project:** Create file structure, `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`.
2. **`config.py`:** Env var loading and validation.
3. **`storage.py`:** JSON file dedupe (testable in isolation).
4. **`meeting_type_config.py`:** JSON loader and keyword matcher (testable in isolation).
5. **`recipient_resolver.py`:** Three-tier resolution (testable in isolation, depends on meeting_type_config).

### Phase 1 stop point -- human step required before Phase 2

> **STOP. Do not proceed to Phase 2 until the real Zapier payload has been captured and committed.**
>
> To capture the payload:
> 1. Deploy a bare-minimum webhook endpoint that logs the raw request body to a file.
> 2. Configure a Zapier Zap: Otter.ai "New Transcript" trigger -> Webhooks by Zapier "POST" pointing to the local endpoint.
> 3. Run a test meeting in Otter.ai and capture the raw JSON output.
> 4. Save it as `fixtures/sample_otter_webhook.json`.
> 5. Only then proceed to Phase 2.
>
> The fixture file is the source of truth for the Pydantic model and all tests. The placeholder fixture in Section 12 is for documentation only and must not be used as the basis for implementation.

### Phase 2 -- Implement after payload is captured (Cursor)

6. **`webhook_server.py`:** FastAPI endpoint with auth and Pydantic model (based on captured fixture).
7. **`summarizer.py`:** OpenAI integration with size policy.
8. **`emailer.py`:** Graph REST integration via httpx with HTML sanitization.
9. **`pipeline.py`:** Wire everything together.
10. **`main.py`:** Entrypoint with startup validation.
11. **Unit tests:** All test files in `tests/`.
12. **Integration tests:** `test_pipeline_integration.py`.
13. **`README.md`:** Full setup guide.
14. **End-to-end manual test:** Full Zapier -> webhook -> email flow.

---

## 17. What is NOT in v1

- Otter.ai REST API polling
- Cloud Run / hosted deployment
- Database storage
- Web dashboard
- Search index
- Monday.com integration
- Action-item extraction to external systems
- Full transcript chunking / multi-pass summarization
- Automatic `processed_meetings.json` cleanup
- `msgraph-sdk` (using direct REST calls instead)
