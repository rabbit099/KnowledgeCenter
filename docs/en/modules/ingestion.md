# Module: ingestion

> Applies to: v0.0.0 (pre-release) · Last reviewed: 2026-11-12

## Scope

Turns a stored file into text parts: detects the file kind from its content, runs the extractor for that format, runs OCR for scanned PDF pages, detects the language per part, chunks long text and enforces the safety limits. Runs in the `worker`. Details: [ingestion-and-ocr.md](../ingestion-and-ocr.md).

## Dependencies

Requires [documents](documents.md).

## Permissions

None of its own. The "reprocess" action reuses permissions from the catalog (the exact code is decided in Phase 2).

## Tables

None in the MVP; the prefix `ing_` is reserved. The processing status and warnings live in `doc_versions` and are updated through `documents.services.api`.

## Events

- Subscribes to `document.version.uploaded`: enqueues the processing job (Dramatiq).
- Publishes `document.version.extracted` (`document_id`, `version_id`, `parts`, `metadata`) when extraction finished. Each part has type (`page`, `body`, `attachment`), name, page number, chunk number, language, text and the OCR flag. The `search` module indexes it.

## Public interface

`modules/ingestion/services/api.py`. Planned (not implemented yet): reprocess one version or all pending versions.

## Extension points

Extractor registry: a new file format is a new file in `ingestion/extractors/` that registers itself for its kind; no other file has to change.

## Status

Phase 0: manifest and an empty public interface. Implementation comes in Phase 2 (extractors, pipeline) and Phase 4 (OCR).
