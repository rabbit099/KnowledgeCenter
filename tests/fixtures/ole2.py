"""Minimal writer of OLE2 / Compound File Binary (CFB, version 3) files, for tests only.

No Python library writes new compound files, and real DOC/MSG files must not be committed, so
tests build synthetic ones with this writer. Supported: nested storages, streams of any size
(small ones in the mini stream), up to 109 FAT sectors (about 6.8 MB). Directory trees are
written as sorted right-sibling chains (valid binary search trees; all nodes black), which
readers such as olefile accept.

    build({"WordDocument": b"...", "__attach_version1.0_#00000000/__substg1.0_3707001F": b"..."})
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from itertools import pairwise

SECTOR = 512
MINI = 64
CUTOFF = 4096
FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF
STORAGE, STREAM, ROOT = 1, 2, 5


@dataclass
class _Node:
    name: str
    kind: int
    data: bytes = b""
    children: dict[str, _Node] = field(default_factory=dict)
    sid: int = 0
    start: int = ENDOFCHAIN
    left: int = NOSTREAM
    right: int = NOSTREAM
    child: int = NOSTREAM


def _sort_key(name: str) -> tuple[int, str]:
    return len(name), name.upper()


def _tree(streams: dict[str, bytes]) -> _Node:
    root = _Node("Root Entry", ROOT)
    for path, data in streams.items():
        node = root
        *storages, leaf = path.split("/")
        for part in storages:
            node = node.children.setdefault(part, _Node(part, STORAGE))
        node.children[leaf] = _Node(leaf, STREAM, data)
    return root


def _flatten(root: _Node) -> list[_Node]:
    nodes = [root]
    queue = [root]
    while queue:
        parent = queue.pop(0)
        children = sorted(parent.children.values(), key=lambda n: _sort_key(n.name))
        for child in children:
            child.sid = len(nodes)
            nodes.append(child)
            queue.append(child)
        for current, following in pairwise(children):
            current.right = following.sid
        if children:
            parent.child = children[0].sid
    return nodes


def _chain(table: list[int], start: int, count: int) -> int:
    if count == 0:
        return ENDOFCHAIN
    for i in range(count):
        table[start + i] = start + i + 1 if i < count - 1 else ENDOFCHAIN
    return start


def build(streams: dict[str, bytes]) -> bytes:
    nodes = _flatten(_tree(streams))
    small = [n for n in nodes if n.kind == STREAM and len(n.data) < CUTOFF]
    large = [n for n in nodes if n.kind == STREAM and len(n.data) >= CUTOFF]

    # Mini stream: small streams in 64-byte sectors, chained in the mini FAT.
    minifat: list[int] = []
    ministream = b""
    for node in small:
        count = -(-len(node.data) // MINI) if node.data else 0
        node.start = ENDOFCHAIN if count == 0 else len(minifat)
        minifat.extend([0] * count)
        if count:
            _chain(minifat, node.start, count)
        ministream += node.data.ljust(count * MINI, b"\0")

    def sectors(size: int) -> int:
        return -(-size // SECTOR)

    dir_sectors = sectors(len(nodes) * 128)
    minifat_sectors = sectors(len(minifat) * 4)
    mini_sectors = sectors(len(ministream))
    large_sectors = sum(sectors(len(n.data)) for n in large)
    fat_sectors = 1
    while True:
        total = fat_sectors + dir_sectors + minifat_sectors + mini_sectors + large_sectors
        if sectors(total * 4) <= fat_sectors:
            break
        fat_sectors += 1
    assert fat_sectors <= 109, "file too large for this minimal writer"

    fat = [FREESECT] * (fat_sectors * SECTOR // 4)
    position = 0
    for i in range(fat_sectors):
        fat[i] = FATSECT
    position += fat_sectors
    dir_start = _chain(fat, position, dir_sectors)
    position += dir_sectors
    minifat_start = _chain(fat, position, minifat_sectors)
    position += minifat_sectors
    mini_start = _chain(fat, position, mini_sectors)
    position += mini_sectors
    body = b""
    for node in large:
        count = sectors(len(node.data))
        node.start = _chain(fat, position, count)
        position += count
        body += node.data.ljust(count * SECTOR, b"\0")

    root = nodes[0]
    root.start = mini_start
    directory = b""
    for node in nodes:
        name = node.name.encode("utf-16-le")
        assert len(name) <= 62, f"name too long: {node.name}"
        size = len(ministream) if node.kind == ROOT else len(node.data)
        start = node.start if (node.kind != STORAGE) else 0
        directory += struct.pack(
            "<64sHBBIII16sI8s8sIII",
            name + b"\0\0",
            len(name) + 2,
            node.kind,
            1,  # black
            node.left,
            node.right,
            node.child,
            b"\0" * 16,
            0,
            b"\0" * 8,
            b"\0" * 8,
            start if size or node.kind == ROOT else ENDOFCHAIN,
            size,
            0,
        )
    directory = directory.ljust(dir_sectors * SECTOR, b"\0")
    # Unused directory slots must be marked empty (all zero is "unknown/empty" type 0).

    difat = list(range(fat_sectors)) + [FREESECT] * (109 - fat_sectors)
    header = struct.pack(
        "<8s16sHHHHH6sIIIIIIIII109I",
        bytes.fromhex("D0CF11E0A1B11AE1"),
        b"\0" * 16,
        0x003E,
        0x0003,
        0xFFFE,
        9,
        6,
        b"\0" * 6,
        0,
        fat_sectors,
        dir_start,
        0,
        CUTOFF,
        minifat_start if minifat_sectors else ENDOFCHAIN,
        minifat_sectors,
        ENDOFCHAIN,
        0,
        *difat,
    )
    fat_bytes = struct.pack(f"<{len(fat)}I", *fat)
    minifat_bytes = struct.pack(f"<{len(minifat)}I", *minifat)
    minifat_bytes = minifat_bytes.ljust(minifat_sectors * SECTOR, b"\xff")
    return (
        header
        + fat_bytes
        + directory
        + minifat_bytes
        + ministream.ljust(mini_sectors * SECTOR, b"\0")
        + body
    )
