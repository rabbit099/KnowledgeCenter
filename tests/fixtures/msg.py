"""Synthetic Outlook .msg files for tests (MS-OXMSG on top of the OLE2 writer in ole2.py).

Only invented content and example.com addresses: real Outlook files must never be committed
(their headers name real people and servers). Supported: subject, sender, To/Cc recipients,
date, Message-ID, plain, HTML and compressed RTF bodies, file attachments and attached
(embedded) messages to any depth.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fixtures.ole2 import build

PT_LONG, PT_SYSTIME, PT_UNICODE, PT_BINARY, PT_OBJECT = 0x0003, 0x0040, 0x001F, 0x0102, 0x000D


@dataclass
class Attachment:
    name: str
    data: bytes = b""
    message: Msg | None = None  # an attached e-mail (embedded message)


@dataclass
class Msg:
    subject: str = "Przegląd urządzeń – październik"
    sender_name: str = "Anna Przykładowa"
    sender: str = "anna@example.com"
    to: list[tuple[str, str]] = field(
        default_factory=lambda: [("Biuro", "biuro@example.com"), ("Jan Testowy", "jan@example.com")]
    )
    cc: list[tuple[str, str]] = field(
        default_factory=lambda: [("Archiwum", "archiwum@example.com")]
    )
    date: datetime = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    message_id: str = "<synthetic-msg-0001@example.com>"
    body: str | None = "Treść wiadomości: zażółć gęślą jaźń."
    html: str | None = None
    rtf: bytes | None = None  # uncompressed RTF; stored compressed (compressed_rtf)
    attachments: list[Attachment] = field(default_factory=list)


def _tag(prop_id: int, prop_type: int) -> str:
    return f"{prop_id:04X}{prop_type:04X}"


def _filetime(value: datetime) -> int:
    return int((value - datetime(1601, 1, 1, tzinfo=UTC)).total_seconds() * 10_000_000)


def _entry(prop_id: int, prop_type: int, value: int = 0, size: int = 0) -> bytes:
    tag = (prop_id << 16) | prop_type
    if prop_type in (PT_UNICODE, PT_BINARY, PT_OBJECT):
        return struct.pack("<IIII", tag, 0x06, size, 0)
    return struct.pack("<IIQ", tag, 0x06, value)


class _Props:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.streams: dict[str, bytes] = {}
        self.entries: list[bytes] = []

    def string(self, prop_id: int, value: str) -> None:
        data = value.encode("utf-16-le")
        self.streams[f"{self.prefix}__substg1.0_{_tag(prop_id, PT_UNICODE)}"] = data
        self.entries.append(_entry(prop_id, PT_UNICODE, size=len(data) + 2))

    def binary(self, prop_id: int, data: bytes) -> None:
        self.streams[f"{self.prefix}__substg1.0_{_tag(prop_id, PT_BINARY)}"] = data
        self.entries.append(_entry(prop_id, PT_BINARY, size=len(data)))

    def fixed(self, prop_id: int, prop_type: int, value: int) -> None:
        self.entries.append(_entry(prop_id, prop_type, value))

    def finish(self, header: bytes) -> dict[str, bytes]:
        self.streams[f"{self.prefix}__properties_version1.0"] = header + b"".join(self.entries)
        return self.streams


def _message(msg: Msg, prefix: str, *, embedded: bool) -> dict[str, bytes]:
    props = _Props(prefix)
    props.string(0x001A, "IPM.Note")
    props.string(0x0037, msg.subject)
    props.string(0x0C1A, msg.sender_name)
    props.string(0x0C1F, msg.sender)
    props.string(0x5D01, msg.sender)
    props.string(0x0E04, "; ".join(name for name, _ in msg.to))
    props.string(0x0E03, "; ".join(name for name, _ in msg.cc))
    props.string(0x1035, msg.message_id)
    props.fixed(0x0039, PT_SYSTIME, _filetime(msg.date))
    props.fixed(0x0E06, PT_SYSTIME, _filetime(msg.date))
    if msg.body is not None:
        props.string(0x1000, msg.body)
    if msg.html is not None:
        props.binary(0x1013, msg.html.encode("utf-8"))
    if msg.rtf is not None:
        import compressed_rtf

        props.binary(0x1009, compressed_rtf.compress(msg.rtf))
    streams: dict[str, bytes] = {}
    recipients = [(r, 1) for r in msg.to] + [(r, 2) for r in msg.cc]
    for number, ((name, address), kind) in enumerate(recipients):
        recip = _Props(f"{prefix}__recip_version1.0_#{number:08X}/")
        recip.string(0x3001, name)
        recip.string(0x3003, address)
        recip.string(0x39FE, address)
        recip.string(0x3002, "SMTP")
        recip.fixed(0x0C15, PT_LONG, kind)
        recip.fixed(0x3000, PT_LONG, number)
        streams.update(recip.finish(b"\0" * 8))
    for number, attachment in enumerate(msg.attachments):
        base = f"{prefix}__attach_version1.0_#{number:08X}/"
        att = _Props(base)
        att.string(0x3707, attachment.name)
        att.string(0x3704, attachment.name[:12])
        att.string(0x3001, attachment.name)
        att.fixed(0x0E21, PT_LONG, number)
        if attachment.message is not None:
            att.fixed(0x3705, PT_LONG, 5)
            att.entries.append(_entry(0x3701, PT_OBJECT, size=0xFFFFFFFF))
            streams.update(
                _message(attachment.message, f"{base}__substg1.0_3701000D/", embedded=True)
            )
        else:
            att.fixed(0x3705, PT_LONG, 1)
            att.binary(0x3701, attachment.data)
        streams.update(att.finish(b"\0" * 8))
    counts = struct.pack(
        "<IIII", len(recipients), len(msg.attachments), len(recipients), len(msg.attachments)
    )
    header = b"\0" * 8 + counts + (b"" if embedded else b"\0" * 8)
    streams.update(props.finish(header))
    if not embedded:
        for name in ("00020102", "00030102", "00040102"):
            streams[f"__nameid_version1.0/__substg1.0_{name}"] = b""
    return streams


def msg_bytes(msg: Msg | None = None) -> bytes:
    return build(_message(msg or Msg(), "", embedded=False))
