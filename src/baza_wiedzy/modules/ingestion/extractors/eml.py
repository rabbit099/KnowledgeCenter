"""E-mail (RFC 5322 .eml): body as text, headers as metadata, attachments through the pipeline.

- Parsed with the standard library (`email`, policy=default); damaged headers and dates are
  tolerated (the value becomes empty, processing continues).
- Body: text/plain preferred; text/html only when there is no plain text, converted to text
  without executing or fetching anything (html_text.py).
- Attachments: every other leaf part, and attached messages (message/rfc822) as nested .eml
  files. Each one is checked by content like an upload and processed by the extractor of its
  kind, with origin "attachment: name"; unsupported ones are skipped with a note.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from baza_wiedzy.modules.ingestion.extractors.base import (
    DAMAGED,
    Context,
    EmailHeaders,
    PermanentError,
    clean_text,
)
from baza_wiedzy.modules.ingestion.extractors.html_text import html_to_text

log = logging.getLogger(__name__)
MAX_HEADER = 4000


def _header(message: Message, name: str) -> str | None:
    try:
        value = message.get(name)
        text = clean_text(" ".join(str(value).split())) if value is not None else ""
    except Exception:  # malformed header: ignore the value, keep the message
        return None
    return text[:MAX_HEADER] or None


def _date(message: Message) -> datetime | None:
    try:
        raw = message.get("date")
        if raw is None:
            return None
        parsed = getattr(raw, "datetime", None) or parsedate_to_datetime(str(raw))
    except Exception:
        return None
    if not isinstance(parsed, datetime):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def headers(message: Message) -> EmailHeaders:
    return EmailHeaders(
        subject=_header(message, "subject"),
        from_addr=_header(message, "from"),
        to_addrs=_header(message, "to"),
        cc_addrs=_header(message, "cc"),
        sent_at=_date(message),
        message_id=_header(message, "message-id"),
    )


def _text_of(part: Message) -> str:
    try:
        content = part.get_content()  # type: ignore[attr-defined]
        if isinstance(content, str):
            return content
    except Exception:  # unknown charset, broken transfer encoding
        pass
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    return ""


def _body(message: EmailMessage) -> Message | None:
    try:
        return message.get_body(preferencelist=("plain", "html"))
    except Exception:
        return None


def _leaves(part: Message) -> list[Message]:
    """Leaf parts in order, without descending into attached messages."""
    if part.get_content_type() == "message/rfc822" or not part.is_multipart():
        return [part]
    payload = part.get_payload()
    children = payload if isinstance(payload, list) else []
    return [leaf for child in children if isinstance(child, Message) for leaf in _leaves(child)]


def _is_inline_text(part: Message) -> bool:
    """Alternative or inline text bodies (not attachments): text/* without a file name."""
    try:
        name = part.get_filename()
        disposition = part.get_content_disposition()
    except Exception:
        return False
    return part.get_content_maintype() == "text" and not name and disposition != "attachment"


def _attachment_name(part: Message, number: int) -> str:
    try:
        name = part.get_filename()
    except Exception:
        name = None
    if name:
        return str(name)
    content_type = part.get_content_type()
    if content_type == "message/rfc822":
        return f"message-{number}.eml"
    if content_type == "text/plain":
        return f"attachment-{number}.txt"
    return f"attachment-{number}"


def _attachment_bytes(part: Message) -> bytes:
    if part.get_content_type() == "message/rfc822":
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        if isinstance(inner, Message):
            return inner.as_bytes(policy=policy.default)
        return b""
    data = part.get_payload(decode=True)
    return data if isinstance(data, bytes) else b""


def extract(path: Path, ctx: Context) -> None:
    try:
        with path.open("rb") as stream:
            message = BytesParser(policy=policy.default).parse(stream)
    except Exception as exc:
        raise PermanentError(DAMAGED, "eml_parse") from exc
    if not isinstance(message, EmailMessage):
        raise PermanentError(DAMAGED, "eml_parse")
    if ctx.top_level:
        ctx.collector.email = headers(message)

    body = _body(message)
    if body is not None:
        text = _text_of(body)
        if body.get_content_subtype() == "html":
            text = html_to_text(text)
        ctx.add_text(text)

    number = 0
    for leaf in _leaves(message):
        if leaf is body or _is_inline_text(leaf):
            continue
        number += 1
        ctx.attachment(_attachment_name(leaf, number), _attachment_bytes(leaf))
