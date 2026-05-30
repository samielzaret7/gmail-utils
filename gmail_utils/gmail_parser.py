"""
gmail_utils/gmail_parser.py

Reusable Gmail parsing utilities for multiple projects.

Supported use cases (current):
- FreeCodeCamp: query unread emails, parse body (raw or full), return items with gmail_message_id.
  Typically: format="raw", mark_as_read=False, and downstream marks read after successful POSTs.
- Journie: query unread emails, parse HTML body, extract Date header, convert to PDF.
  Typically: format="full", mark_as_read=True, and use EmailDate for folder naming.

Key features:
- Works with Gmail API message formats: "raw" and "full"
- Returns normalized items: {"From","Subject","Body","gmail_message_id","EmailDate"}
- Optional mark-as-read behavior:
    - mark_as_read=True: mark messages as read immediately after fetch/parse
    - mark_as_read=False: defer to caller (use mark_messages_as_read)
- Robust body extraction (prefers html then plain by default)
- Robust header handling (no brittle header index references)

Notes:
- Expects OAuth files in the current working directory by default:
    - credentials.json
    - token.json
  If you run scripts from project roots, each project can keep its own credentials/token.
"""

from __future__ import annotations

import os
import base64
import logging
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from email import policy
from email.parser import BytesParser
from email.utils import parseaddr


# -----------------------
# Credential / service
# -----------------------

def validate_credentials(
    scopes: list[str],
    *,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    logger: Optional[logging.Logger] = None,
) -> Credentials:
    """
    Loads/refreshes credentials and returns a Credentials object.

    Args:
        scopes: Google OAuth scopes.
        credentials_path: Path to OAuth client credentials json.
        token_path: Path to token json (created after first auth).
        logger: Optional logger.

    Returns:
        Credentials
    """
    log = logger or logging.getLogger(__name__)
    creds: Optional[Credentials] = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Gmail token...")
            creds.refresh(Request())
        else:
            log.info("Running Gmail OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


def build_gmail_service(
    scopes: list[str],
    *,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    logger: Optional[logging.Logger] = None,
):
    """
    Convenience helper to build the Gmail API service client.
    """
    creds = validate_credentials(
        scopes,
        credentials_path=credentials_path,
        token_path=token_path,
        logger=logger,
    )
    return build("gmail", "v1", credentials=creds)


# -----------------------
# Gmail API helpers
# -----------------------

def get_email_ids(
    query: str,
    service,
    *,
    max_results: Optional[int] = None,
    user_id: str = "me",
    logger: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    """
    Calls Gmail messages.list with a query. Returns the raw response dict.

    Args:
        query: Gmail search query.
        service: Gmail service client.
        max_results: Optional max results per call (Gmail default ~100 if not specified).
        user_id: "me" or a specific userId.
        logger: Optional logger.

    Returns:
        Dict with keys like {"messages":[{"id":...,"threadId":...}], "nextPageToken":...}
    """
    log = logger or logging.getLogger(__name__)
    try:
        req = service.users().messages().list(userId=user_id, q=query)
        if max_results is not None:
            req = service.users().messages().list(userId=user_id, q=query, maxResults=max_results)
        return req.execute()
    except HttpError as error:
        log.exception("Gmail API error on messages.list: %s", error)
        raise


def mark_messages_as_read(
    scopes: list[str],
    message_ids: list[str],
    *,
    user_id: str = "me",
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Marks Gmail messages as read by removing the UNREAD label.

    This is intended for "deferred marking" patterns (A2):
    only call this after downstream processing has succeeded.

    Args:
        scopes: Must include gmail.modify to modify labels.
        message_ids: List of Gmail message IDs to mark as read.
        user_id: Gmail userId (default "me")
        credentials_path/token_path: OAuth files
        logger: Optional logger
    """
    log = logger or logging.getLogger(__name__)

    if not message_ids:
        return

    # Ensure needed scope is present
    if "https://www.googleapis.com/auth/gmail.modify" not in scopes:
        log.warning("mark_messages_as_read called without gmail.modify scope. No-op.")
        return

    service = build_gmail_service(
        scopes,
        credentials_path=credentials_path,
        token_path=token_path,
        logger=log,
    )

    ok = 0
    for mid in message_ids:
        try:
            service.users().messages().modify(
                userId=user_id,
                id=mid,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            ok += 1
        except HttpError as error:
            log.exception("Failed to mark message as read. id=%s error=%s", mid, error)

    log.info("Marked as read: %s/%s", ok, len(message_ids))


# -----------------------
# Parsing helpers
# -----------------------

def _headers_from_full_payload(email_obj: dict[str, Any]) -> dict[str, str]:
    """
    Converts payload.headers array into a dict for easy access.
    """
    headers_list = email_obj.get("payload", {}).get("headers", []) or []
    headers: dict[str, str] = {}
    for h in headers_list:
        name = h.get("name")
        value = h.get("value")
        if name and value is not None:
            headers[name] = value
    return headers


def _parse_raw_rfc822(raw_b64url: str):
    """
    Decode Gmail 'raw' (base64url) and parse into an EmailMessage.
    """
    raw_bytes = base64.urlsafe_b64decode(raw_b64url.encode("utf-8"))
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def _wrap_plain_as_html(text: str) -> str:
    return f"<html><body>{text}</body></html>"


def _extract_best_body_from_email_message(
    msg,
    *,
    prefer: tuple[str, ...] = ("html", "plain"),
) -> str:
    """
    Extract body from an EmailMessage (parsed from raw RFC822).

    prefer: ("html","plain") by default.
    Returns HTML string; wraps plain text as minimal HTML.
    """
    # policy.default EmailMessage supports get_body
    if hasattr(msg, "get_body"):
        if "html" in prefer:
            html_part = msg.get_body(preferencelist=("html",))
            if html_part:
                try:
                    return html_part.get_content()
                except Exception:
                    pass

        if "plain" in prefer:
            plain_part = msg.get_body(preferencelist=("plain",))
            if plain_part:
                try:
                    return _wrap_plain_as_html(plain_part.get_content())
                except Exception:
                    pass

    # Fallback: walk parts
    for part in getattr(msg, "walk", lambda: [])():
        ctype = getattr(part, "get_content_type", lambda: "")()
        if ctype == "text/html" and "html" in prefer:
            try:
                return part.get_content()
            except Exception:
                continue
        if ctype == "text/plain" and "plain" in prefer:
            try:
                return _wrap_plain_as_html(part.get_content())
            except Exception:
                continue

    return "<html><body>No content found</body></html>"


def _extract_best_body_from_full_payload(
    email_obj: dict[str, Any],
    *,
    prefer: tuple[str, ...] = ("html", "plain"),
) -> str:
    """
    Extract body from Gmail 'full' format payload.
    Searches nested parts for text/html then text/plain.
    """
    payload = email_obj.get("payload", {}) or {}

    def decode_body(data_b64url: str) -> str:
        return base64.urlsafe_b64decode(data_b64url.encode("utf-8")).decode("utf-8", errors="replace")

    candidates: list[tuple[str, str]] = []

    def walk(p: dict[str, Any]) -> None:
        mime = p.get("mimeType", "") or ""
        body = p.get("body", {}) or {}
        data = body.get("data")
        if data:
            candidates.append((mime, decode_body(data)))
        for child in (p.get("parts") or []):
            walk(child)

    # Sometimes the payload itself has body.data
    payload_body = payload.get("body", {}) or {}
    if payload_body.get("data"):
        candidates.append((payload.get("mimeType", "") or "", decode_body(payload_body["data"])))

    for p in (payload.get("parts") or []):
        walk(p)

    if "html" in prefer:
        for mime, content in candidates:
            if mime == "text/html":
                return content

    if "plain" in prefer:
        for mime, content in candidates:
            if mime == "text/plain":
                return _wrap_plain_as_html(content)

    return "<html><body>No content found</body></html>"


def _extract_from_email_address(from_value: str) -> str:
    """
    Normalize From into email address when possible.
    """
    if not from_value:
        return ""
    parsed = parseaddr(from_value)[1]
    return parsed or from_value


# -----------------------
# Public API: get_email_items_main
# -----------------------

def get_email_items_main(
    SCOPES: list[str],
    query: str,
    format: str = "raw",
    *,
    body_preference: tuple[str, ...] = ("html", "plain"),
    max_results: Optional[int] = None,
    mark_as_read: bool = False,
    user_id: str = "me",
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    logger: Optional[logging.Logger] = None,
) -> list[dict[str, str]]:
    """
    Returns a list of dicts shaped like:
        {
          "From": "...",
          "Subject": "...",
          "Body": "<html>...</html>",
          "gmail_message_id": "...",
          "EmailDate": "Tue, 26 Dec 2025 09:41:22 -0500"
        }

    Works for both "raw" and "full" formats.

    Args:
        SCOPES: OAuth scopes.
        query: Gmail search query.
        format: "raw" or "full".
        body_preference: ("html","plain") default.
        max_results: Optional max number of messages to retrieve.
        mark_as_read: If True, mark messages read immediately after parsing.
                     For A2 pipelines, set False and call mark_messages_as_read later.
        user_id: Gmail userId (default "me").
        credentials_path/token_path: OAuth files.
        logger: Optional logger.

    Returns:
        List of parsed email items.
    """
    log = logger or logging.getLogger(__name__)

    if format not in ("raw", "full"):
        raise ValueError("format must be 'raw' or 'full'")

    service = build_gmail_service(
        SCOPES,
        credentials_path=credentials_path,
        token_path=token_path,
        logger=log,
    )

    results = get_email_ids(
        query,
        service,
        max_results=max_results,
        user_id=user_id,
        logger=log,
    )

    messages = results.get("messages", []) or []
    items: list[dict[str, str]] = []

    for m in messages:
        msg_id = m.get("id")
        if not msg_id:
            continue

        try:
            email_obj = service.users().messages().get(
                userId=user_id,
                id=msg_id,
                format=format,
            ).execute()
        except HttpError as error:
            log.exception("Gmail API error on messages.get. id=%s error=%s", msg_id, error)
            continue

        from_email = ""
        subject = ""
        email_date = ""

        if format == "raw":
            raw = email_obj.get("raw", "")
            msg = _parse_raw_rfc822(raw)
            from_email = _extract_from_email_address(msg.get("From", "") or "")
            subject = msg.get("Subject", "") or ""
            # raw RFC822 has Date header too
            email_date = msg.get("Date", "") or ""
            body = _extract_best_body_from_email_message(msg, prefer=body_preference)

        else:
            headers = _headers_from_full_payload(email_obj)
            from_email = _extract_from_email_address(headers.get("From", "") or "")
            subject = headers.get("Subject", "") or ""
            email_date = headers.get("Date", "") or ""
            body = _extract_best_body_from_full_payload(email_obj, prefer=body_preference)

        items.append(
            {
                "From": from_email,
                "Subject": subject,
                "Body": body,
                "gmail_message_id": msg_id,
                "EmailDate": email_date,
            }
        )

    # Mark as read immediately (convenience mode)
    if mark_as_read and items:
        if "https://www.googleapis.com/auth/gmail.modify" not in SCOPES:
            log.warning("mark_as_read=True but gmail.modify scope not present. Skipping mark-as-read.")
        else:
            ids_to_mark = [it["gmail_message_id"] for it in items if it.get("gmail_message_id")]
            try:
                for mid in ids_to_mark:
                    service.users().messages().modify(
                        userId=user_id,
                        id=mid,
                        body={"removeLabelIds": ["UNREAD"]},
                    ).execute()
                log.info("Marked as read (immediate mode): %s", len(ids_to_mark))
            except HttpError as error:
                log.exception("Failed to mark messages as read (immediate mode): %s", error)

    return items


# Optional legacy compatibility name (if some scripts used old function)
def get_email_Ids(query: str, service) -> dict[str, Any]:
    """
    Backwards-compatible wrapper for older code.
    Prefer using get_email_ids().
    """
    return get_email_ids(query=query, service=service)

