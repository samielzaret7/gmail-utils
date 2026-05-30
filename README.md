# gmail-utils

A reusable Python library for fetching and parsing Gmail messages via the Gmail API.

## What It Does

- Authenticates with Google OAuth (credentials + token flow)
- Queries Gmail for messages by search query
- Parses email bodies from both `raw` (RFC 822) and `full` (Gmail API payload) formats
- Extracts normalized items: `From`, `Subject`, `Body` (HTML), `gmail_message_id`, `EmailDate`
- Supports two mark-as-read patterns:
  - **Immediate:** mark messages read right after fetching
  - **Deferred (A2):** caller marks read only after downstream processing succeeds

## Projects Using This Library

- **[gasoline-receipts](https://github.com/samielzaret7/gasoline-receipts)** — Fetches Journie gas receipts from Gmail, converts to PDF. Uses `format="full"` with immediate mark-as-read.
- **[Free-Code-Camp-Email-Parser](https://github.com/samielzaret7/Free-Code-Camp-Email-Parser)** — Parses FreeCodeCamp newsletter emails into course items. Uses `format="raw"` with deferred mark-as-read.
- **[UpWork-CRM](https://github.com/nlewism/UpWork-CRM)** — Backfills historical Upwork job emails into Supabase. Uses `build_gmail_service` and `get_email_ids` directly.

## Installation

```bash
# Install from GitHub
pip install git+https://github.com/samielzaret7/gmail-utils.git

# Or install locally for development
cd gmail-utils
pip install -e .
```

## Setup

1. Create a Google Cloud project and enable the Gmail API.
2. Download OAuth client credentials as `credentials.json`.
3. Place `credentials.json` in your project's working directory.
4. On first run, the library opens a browser window for OAuth consent and saves `token.json`.

## Usage

```python
from gmail_utils import get_email_items_main, mark_messages_as_read

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Fetch and parse emails
items = get_email_items_main(
    SCOPES=SCOPES,
    query="from:sender@example.com is:unread",
    format="raw",          # or "full"
    mark_as_read=False,    # defer marking until downstream succeeds
)

for item in items:
    print(item["Subject"], item["From"], item["EmailDate"])
    # Process item["Body"] (HTML string)...

# After successful processing, mark as read
ids = [item["gmail_message_id"] for item in items]
mark_messages_as_read(SCOPES, ids)
```

## API Reference

| Function | Description |
|---|---|
| `get_email_items_main()` | Main entry point — query, fetch, parse, and optionally mark as read |
| `mark_messages_as_read()` | Mark specific messages as read (for deferred/A2 pattern) |
| `build_gmail_service()` | Build a Gmail API service client |
| `get_email_ids()` | Low-level: list message IDs matching a query |
| `validate_credentials()` | Load/refresh OAuth credentials |

## Dependencies

- `google-auth`
- `google-auth-oauthlib`
- `google-api-python-client`

Requires Python 3.10+.
