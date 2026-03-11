# Meeting Recap Bot

Automatically generates meeting recap emails from Otter.ai transcripts. When a meeting transcript is completed in Otter.ai, Zapier forwards it to this service, which summarizes it with OpenAI and sends the recap via Microsoft Graph.

## How It Works

```
Otter.ai transcript completed
  -> Zapier "New Transcript" trigger
  -> POST to /webhook/transcript
  -> Deduplicate, resolve recipients, summarize, send email
```

### Pipeline Steps

1. **Authentication** -- verify the webhook secret (`X-Webhook-Secret` header or `Authorization: Bearer` token).
2. **Validation** -- parse the JSON payload into a Pydantic model (`WebhookPayload`).
3. **Deduplication** -- check `processed_meetings.json` (file-locked) to skip already-handled meetings.
4. **Recipient resolution** -- three-tier fallback (see [Recipient Resolution](#11-recipient-resolution)).
5. **Transcript truncation** -- trim to `MAX_TRANSCRIPT_CHARS` if needed.
6. **Summarization** -- call OpenAI with `instructions.md` as the system prompt (3 retries with exponential backoff).
7. **Email delivery** -- send via Microsoft Graph or save as a draft, depending on `EMAIL_MODE` (3 retries with exponential backoff). On summarization failure, a failure notification is sent to the CC address.
8. **Dedup record** -- mark the meeting as processed only after successful delivery.

---

## Project Structure

```
meeting-recap-bot/
├── main.py                  # Entrypoint: configures logging, validates config, starts uvicorn
├── webhook_server.py        # FastAPI app with /health and /webhook/transcript endpoints
├── pipeline.py              # Orchestrates the full processing pipeline
├── models.py                # Pydantic models (WebhookPayload, Participant)
├── summarizer.py            # OpenAI summarization with retry logic
├── emailer.py               # Microsoft Graph email (send, draft, failure notification)
├── recipient_resolver.py    # Three-tier recipient resolution
├── meeting_type_config.py   # Loads and matches meeting_types.json distro lists
├── storage.py               # File-locked JSON deduplication store
├── config.py                # Environment variable loading and validation
├── instructions.md          # OpenAI system prompt for summary generation
├── meeting_types.json       # Meeting-type -> distribution list mapping
├── processed_meetings.json  # Deduplication state (volume-mounted in Docker)
├── fixtures/
│   └── sample_otter_webhook.json  # Sample payload for testing
├── tests/
│   ├── conftest.py
│   ├── test_webhook_server.py
│   ├── test_pipeline_integration.py
│   ├── test_emailer.py
│   ├── test_summarizer.py
│   ├── test_recipient_resolver.py
│   ├── test_storage.py
│   └── test_meeting_type_config.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── .gitignore
```

---

## Prerequisites

- Python 3.12+ (or Docker)
- An OpenAI API key
- An Azure AD app registration with `Mail.Send` Application permission (see below)
- A Zapier account with the Otter.ai integration enabled
- A tunnel tool to expose your local port to the internet (ngrok or Cloudflare Tunnel)

---

## 1. Azure AD Setup

Before you can send email via Microsoft Graph, you need an Azure AD app registration.

### Create the app registration

1. Go to [portal.azure.com](https://portal.azure.com) -> Azure Active Directory -> App registrations -> New registration.
2. Name it (e.g. `meeting-recap-bot`), select **Accounts in this organizational directory only**, click Register.
3. Copy the **Application (client) ID** and **Directory (tenant) ID** -- you'll need these.

### Add API permissions

1. In your app registration, go to **API permissions** -> Add a permission -> Microsoft Graph -> Application permissions.
2. Search for `Mail.Send` and add it. This is required for sending emails.
3. If you plan to use draft mode (`EMAIL_MODE=draft`), also add `Mail.ReadWrite`.
4. Click **Grant admin consent** for your organization. The app will not be able to send email without it.

### Create a client secret

1. Go to **Certificates & secrets** -> New client secret.
2. Set an expiration and click Add.
3. Copy the secret **value** immediately (it won't be shown again).

### Exchange Online access policy (if applicable)

If your tenant uses Exchange Online application access policies, the registered app must be in scope for the sender mailbox. Contact your Exchange admin and provide the app's client ID to add it to the policy.

To verify the setup before deploying, send a test email manually:

```bash
# Get a token
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token" \
  -d "client_id=<CLIENT_ID>&client_secret=<CLIENT_SECRET>&scope=https://graph.microsoft.com/.default&grant_type=client_credentials" \
  | jq -r .access_token)

# Send a test email
curl -X POST \
  "https://graph.microsoft.com/v1.0/users/<EMAIL_FROM>/sendMail" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "subject": "Test from meeting-recap-bot",
      "body": {"contentType": "Text", "content": "Setup test."},
      "toRecipients": [{"emailAddress": {"address": "<YOUR_EMAIL>"}}]
    },
    "saveToSentItems": true
  }'
```

---

## 2. Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `MS_GRAPH_CLIENT_ID` | Yes | Azure AD app client ID |
| `MS_GRAPH_CLIENT_SECRET` | Yes | Azure AD app client secret |
| `MS_GRAPH_TENANT_ID` | Yes | Azure AD tenant ID |
| `EMAIL_FROM` | Yes | Sender mailbox (must have Graph `Mail.Send` permission) |
| `WEBHOOK_SECRET` | Yes | Shared secret sent by Zapier in `X-Webhook-Secret` header (also accepts `Authorization: Bearer` token) |
| `EMAIL_CC` | No | CC on all emails (default: `bill.johnson@scribendi.com`) |
| `EMAIL_MODE` | No | `send` delivers immediately, `draft` saves to the sender's Drafts folder for manual review (default: `send`). Draft mode requires `Mail.ReadWrite` permission. |
| `MAX_TRANSCRIPT_CHARS` | No | Truncation threshold (default: `100000`) |
| `OPENAI_MODEL` | No | OpenAI model (default: `gpt-4o`) |
| `WEBHOOK_HOST` | No | Bind host (default: `0.0.0.0`) |
| `WEBHOOK_PORT` | No | Bind port (default: `8000`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `STORAGE_FILE` | No | Path to the deduplication JSON file (default: `processed_meetings.json`) |
| `MEETING_TYPES_FILE` | No | Path to the meeting-type distro list file (default: `meeting_types.json`) |

---

## 3. Instructions File

Place your ChatGPT Project system prompt in `instructions.md` in the project root. This file is used as the OpenAI system message for every summarization call.

> **DO NOT MODIFY** this file once the service is running -- it controls summary quality and format. The service logs a startup message when it loads the file.

---

## 4. Meeting Type Distribution Lists (Optional)

To send recaps to specific distribution lists based on meeting type, edit `meeting_types.json`:

```json
{
  "engineering standup": ["engineering-team@company.com"],
  "product sync": ["product-team@company.com", "pm-leads@company.com"],
  "client review": ["client-success@company.com"]
}
```

Matching is case-insensitive substring. A meeting titled "Weekly Engineering Standup" matches the key `"engineering standup"`. The first matching key wins. If the file is empty or no key matches, the email is sent only to Bill (the `EMAIL_CC` address).

---

## 5. Running with Docker

```bash
# Build and start (detached)
docker compose up -d --build

# Verify the container is healthy (wait ~30s after start)
docker compose ps

# Check the health endpoint
curl http://localhost:8000/health

# Tail logs
docker compose logs -f recap-bot

# Stop
docker compose down
```

The `processed_meetings.json` deduplication file is volume-mounted and persists across container restarts. `instructions.md` and `meeting_types.json` are also volume-mounted so you can edit them without rebuilding.

### Running without Docker

```bash
pip install -r requirements.txt
python main.py
```

---

## 6. Exposing to the Internet (for Zapier)

Zapier needs to reach your local endpoint via a public URL. Two options:

### Option A: ngrok (quick start)

```bash
ngrok http 8000
```

ngrok prints a public URL like `https://abc123.ngrok-free.app`. Use this as the Zapier webhook URL.

**Note:** The free tier assigns a new URL on every restart. You'll need to update the Zapier Zap URL each time. Consider a paid plan for a stable subdomain.

### Option B: Cloudflare Tunnel (recommended for always-on use)

1. Install `cloudflared`: [developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. Log in: `cloudflared tunnel login`
3. Create a tunnel: `cloudflared tunnel create meeting-recap-bot`
4. Add a DNS record: `cloudflared tunnel route dns meeting-recap-bot recap.yourdomain.com`
5. Start the tunnel: `cloudflared tunnel run --url http://localhost:8000 meeting-recap-bot`

The tunnel URL (`https://recap.yourdomain.com`) is stable across restarts.

---

## 7. Zapier Setup

1. Create a new Zap in Zapier.
2. **Trigger:** Otter.ai -> New Transcript (connect your Otter.ai account).
3. **Action:** Webhooks by Zapier -> POST.
   - URL: `https://<your-tunnel-url>/webhook/transcript`
   - Payload Type: JSON
   - Data: Map Otter.ai fields to the payload fields (`meeting_id`, `title`, `date`, `participants`, `transcript`).
   - Headers: `X-Webhook-Secret: <your WEBHOOK_SECRET value>`
4. Test the Zap with a real Otter.ai transcript.

### Webhook payload schema

| Field | Type | Required | Notes |
|---|---|---|---|
| `meeting_id` | string | Yes | Unique ID to deduplicate |
| `title` | string | Yes | Used for subject line and distro matching |
| `date` | string | No | ISO 8601 format (e.g. `2026-03-10T14:00:00Z`) |
| `participants` | list or string | No | JSON array of `{name, email, permission}` objects, or Zapier's newline-delimited text block |
| `transcript` | string | Yes | Full meeting transcript |

Extra fields in the payload are accepted and logged at debug level.

The `participants` field supports both a JSON array and Zapier's text format:

```
email: alice@co.com
name: Alice
permission: None

email: bob@co.com
name: Bob
permission: None
```

---

## 8. Payload Capture

The exact JSON shape sent by Zapier's Otter.ai integration must be validated before the production Pydantic model is finalized.

To capture it:

1. Temporarily add a logging-only route to the service that writes the raw body to a file.
2. Run a test meeting in Otter.ai.
3. Trigger the Zap manually in Zapier.
4. Save the captured payload to `fixtures/sample_otter_webhook.json`.
5. Update the `WebhookPayload` model in `models.py` to match the actual field names.

The placeholder payload in `fixtures/sample_otter_webhook.json` is for development and testing only.

---

## 9. Manual Testing with curl

### Verify health

```bash
curl http://localhost:8000/health
# Expected: {"status": "healthy"}
```

### Send the sample webhook

```bash
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your-secret>" \
  -d @fixtures/sample_otter_webhook.json
# Expected: {"status":"processed","meeting_id":"test-meeting-001"}
```

### Verify duplicate rejection

```bash
# Send same payload again
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <your-secret>" \
  -d @fixtures/sample_otter_webhook.json
# Expected: {"status":"duplicate","meeting_id":"test-meeting-001"}
```

### Verify auth rejection

```bash
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: wrong-secret" \
  -d @fixtures/sample_otter_webhook.json
# Expected: 401 Unauthorized
```

### Using Bearer token authentication

```bash
curl -X POST http://localhost:8000/webhook/transcript \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-secret>" \
  -d @fixtures/sample_otter_webhook.json
```

### Reset deduplication for re-testing

Delete the processed entry from `processed_meetings.json`, or delete the file entirely.

---

## 10. Running Tests

```bash
# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=. --cov-report=term-missing
```

### Test dependencies

- **pytest** -- test runner
- **pytest-asyncio** -- async test support for FastAPI endpoint tests
- **respx** -- HTTP request mocking for Microsoft Graph and OpenAI calls

---

## 11. Recipient Resolution

The service uses a three-tier fallback to determine who receives each recap:

1. **Payload participants** -- if Otter.ai/Zapier includes attendee emails, they become the `To` recipients.
2. **Meeting-type distro list** -- if no participant emails are present, the meeting title is matched against `meeting_types.json` and the mapped list is used.
3. **Bill fallback** -- if neither source produces recipients, the email is sent only to `EMAIL_CC` (Bill).

Bill is always CC'd unless he is already a `To` recipient or is the sole recipient (tier 3).

---

## 12. Email Delivery

### Send mode (default)

Emails are sent immediately via the Microsoft Graph `sendMail` endpoint. On success the meeting is marked processed. On failure after 3 retries the request returns a 500 error.

### Draft mode

Set `EMAIL_MODE=draft` to save recaps to the sender's Drafts folder instead of sending. This is useful for manual review before delivery. Requires the `Mail.ReadWrite` Azure AD permission in addition to `Mail.Send`.

### Failure notifications

If OpenAI summarization fails (after 3 retries), the service sends a failure notification email to the CC address so the summary can be generated manually. The meeting is **not** marked as processed, so a retry via re-triggering the webhook will work.

### Email format

- Summaries are converted from Markdown to sanitized HTML using `python-markdown` and `nh3`.
- A hardcoded email signature is appended to every recap.
- Subject line format: `[Meeting Recap] <title> — <date>`

---

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container exits immediately | Missing required env vars | Check `.env`, run `docker compose logs` |
| `instructions.md not found` | Missing file | Create `instructions.md` in project root |
| `401` from webhook | Wrong or missing `WEBHOOK_SECRET` | Verify header matches `.env` value |
| `503` from Microsoft Graph | Permissions not granted | Grant admin consent in Azure AD portal |
| No email received | Exchange Online access policy | Ask Exchange admin to add app to sender mailbox policy |
| Zapier stops sending | Tunnel URL changed | Restart tunnel and update Zapier Zap URL |
| Summary quality degraded | `instructions.md` was modified | Restore original file from version control |
| `422` from webhook | Payload field names don't match model | Check `models.py` field names against actual Zapier payload |

---

## 14. What Is Not in v1

- Otter.ai REST API polling (Zapier webhook only)
- Cloud deployment (local Docker only)
- Web dashboard
- Database storage (uses flat JSON file)
- Monday.com integration
- Multi-pass summarization for very long transcripts
- Automatic `processed_meetings.json` cleanup
