"""Outlook message (.msg) with extract-msg (GPL-3.0, internal use only: ADR-015).

- Headers of the top-level message (subject, sender, To, Cc, date, Message-ID) become the
  e-mail metadata, like for EML; damaged values stay empty.
- Body: plain text; else HTML converted to text (html_text.py); else RTF: HTML encapsulated in
  RTF through extract-msg (RTFDE), plain RTF with rtf_text.py.
- Attachments go through the same registry as e-mail attachments (type checked by content,
  origin "załącznik: nazwa", depth limit); attached Outlook messages are processed directly
  as nested messages. Other attachment kinds (OLE objects, links) are skipped with a note.

The libraries log at their own discretion (possibly names or text): their loggers are
silenced here, so nothing from a message reaches the worker log.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from baza_wiedzy.modules.ingestion.extractors.base import (
    DAMAGED,
    Context,
    EmailHeaders,
    PermanentError,
    TransientError,
    clean_text,
    format_addresses,
)
from baza_wiedzy.modules.ingestion.extractors.html_text import html_to_text
from baza_wiedzy.modules.ingestion.extractors.rtf_text import rtf_to_text

log = logging.getLogger(__name__)
for _name in ("extract_msg", "RTFDE", "compressed_rtf", "olefile", "msoffcrypto"):
    logging.getLogger(_name).setLevel(logging.CRITICAL + 1)

MAX_HEADER = 4000
TO, CC = 1, 2  # PR_RECIPIENT_TYPE (MAPI_TO, MAPI_CC; BCC is not kept)


def _safe(read: Any) -> Any:
    try:
        return read()
    except Exception:  # a damaged property: empty value, processing continues
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = clean_text(" ".join(str(value).split()))[:MAX_HEADER]
    return text or None


def _decode_html(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, bytes):
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250", errors="replace")


def _recipients(message: Any, kind: int) -> str | None:
    recipients = _safe(lambda: message.recipients) or []
    pairs = [
        (str(_safe(lambda r=r: r.name) or ""), str(_safe(lambda r=r: r.email) or ""))
        for r in recipients
        if _safe(lambda r=r: int(r.type)) == kind
    ]
    return format_addresses(pairs)


def headers(message: Any) -> EmailHeaders:
    sent = _safe(lambda: message.date)
    if isinstance(sent, datetime) and sent.tzinfo is None:
        sent = sent.replace(tzinfo=UTC)
    return EmailHeaders(
        subject=_text(_safe(lambda: message.subject)),
        from_addr=format_addresses(getaddresses([str(_safe(lambda: message.sender) or "")])),
        to_addrs=_recipients(message, TO),
        cc_addrs=_recipients(message, CC),
        sent_at=sent if isinstance(sent, datetime) else None,
        message_id=_text(_safe(lambda: message.messageId)),
    )


def body_text(message: Any) -> str:
    body = _safe(lambda: message.body)
    if isinstance(body, str) and body.strip():
        return body
    html = _safe(lambda: message.htmlBody)  # also HTML de-encapsulated from RTF
    if html:
        return html_to_text(_decode_html(html))
    rtf = _safe(lambda: message.rtfBody)
    if isinstance(rtf, bytes) and rtf:
        return rtf_to_text(rtf)
    return ""


def _message(message: Any, ctx: Context) -> None:
    if ctx.top_level:
        ctx.collector.email = headers(message)
    ctx.add_text(body_text(message))
    attachments = _safe(lambda: message.attachments) or []
    for number, attachment in enumerate(attachments, start=1):
        name = (
            _safe(lambda a=attachment: a.longFilename)
            or _safe(lambda a=attachment: a.shortFilename)
            or _safe(lambda a=attachment: a.displayName)
        )
        data = _safe(lambda a=attachment: a.data)
        if isinstance(data, bytes):
            ctx.attachment(str(name or f"attachment-{number}"), data)
        elif data is not None and hasattr(data, "attachments"):  # an attached message
            child = ctx.nested(str(name or f"message-{number}.msg"))
            if child is not None:
                _message(data, child)
        else:
            skipped = ctx.nested(str(name or f"attachment-{number}"))
            if skipped is not None:
                skipped.note("skipped", reason="unsupported")


def extract(path: Path, ctx: Context) -> None:
    import extract_msg

    try:
        message = extract_msg.openMsg(str(path))
    except Exception as exc:
        raise PermanentError(DAMAGED, "msg_parse") from exc
    try:
        _message(message, ctx)
    except (PermanentError, TransientError):
        raise
    except Exception as exc:
        raise PermanentError(DAMAGED, "msg_parse") from exc
    finally:
        _safe(message.close)
