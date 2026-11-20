# Moduł: documents

> Dotyczy: v0.4.1 · Ostatnia aktualizacja: 2026-11-20

## Zakres

Jest właścicielem dokumentów, ich wersji, kategorii i cyklu życia (szkic → opublikowany → zarchiwizowany), przechowywania plików oryginalnych i pochodnych, pobierania oraz reguły widoczności dokumentów. Walidacja uploadu należy tutaj; wyciąganie tekstu nie (zobacz [ingestion](ingestion.md)).

## Zależności

Nie wymaga żadnego innego modułu. Wymagany przez [ingestion](ingestion.md) i [search](search.md).

## Uprawnienia

`documents.read`, `documents.upload`, `documents.publish`, `documents.publish_any`, `documents.read_drafts`, `documents.edit_any`, `documents.delete`, `categories.manage`. Znaczenie każdego kodu: [auth-and-permissions.md](../auth-and-permissions.md).

## Tabele

`doc_categories`, `doc_documents`, `doc_versions` (w tym `processing_status` i metadane przetwarzania), `doc_email_metadata`. Zobacz [data-model.md](../data-model.md).

## Zdarzenia

- Publikuje `document.version.uploaded` (`document_id`, `version_id`) po zapisaniu nowej wersji.
- Publikuje `document.published` (`document_id`, `version_id`) po publikacji dokumentu (Faza 3, po zatwierdzeniu transakcji). Na razie bez subskrybentów: wyszukiwanie czyta widoczność na bieżąco z `documents`, więc publikacja, archiwizacja i przywrócenie nie wymagają reindeksu.
- Niczego nie subskrybuje.

## Interfejs publiczny

`modules/documents/services/api.py`, jedyna część, którą mogą importować inne moduły. Zaimplementowane (Faza 2, etap A; Faza 3): `visibility_clause(user)` (warunek SQL do każdego zapytania, `visibility.py`: własne dokumenty we wszystkich stanach dla właściciela, szkice dla `documents.read_drafts`, opublikowane dla `documents.read`, zarchiwizowane dla `documents.edit_any`), `can_see_authors(user)` (kto widzi autorów cudzych dokumentów, ADR-030), `list_documents(..., owner_id=)` (filtr autora dla nich), `Document.current_version_id()` (opublikowana wersja dokumentów opublikowanych i zarchiwizowanych, najnowsza wersja szkiców), stałe stanów `STATUS_DRAFT`, `STATUS_PUBLISHED`, `STATUS_ARCHIVED`, `get_visible_document`, `versions_of`, `list_documents`, `visible_duplicates` (tylko dokumenty widoczne dla użytkownika). Dla `ingestion` (etap B): `claim`, `finish`, `fail`, `retry_later`, `reset_for_reprocess`, `recover_stuck`, `ids_with_status` (status przetwarzania, każde przejście pod blokadą wiersza i tylko dla próby, która przejęła wersję, zobacz `processing.py`), `save_email_metadata`, `apply_subject_title` (temat maila jako domyślny tytuł), `email_metadata_of`, `get_visible_version`, `can_reprocess(user, document)`, `original_path(storage_dir, key)` oraz kontrole z wgrywania `sanitize_filename` i `detect_kind` (używane też dla załączników maili). Później: zapis pliku pochodnego (Faza 4).

## Punkty rozszerzeń

Magazyn plików: pliki oryginalne i pochodne są dostępne przez interfejs magazynu (najpierw dysk lokalny), więc można dodać inny backend bez zmian u wywołujących.

## Status

Faza 2, etap A: tabele `doc_documents`, `doc_versions`, `doc_email_metadata` (`documents_0002`); magazyn plików na wolumenie `storage`; wgrywanie z rozpoznawaniem typu po zawartości (ADR-021), limitem rozmiaru, oczyszczonymi nazwami plików i SHA-256; szkice z widocznością w SQL; strony „Moje dokumenty” (`/documents`, uprawnienie `documents.upload` albo `documents.read_drafts`), „Wgraj dokument” i strona dokumentu; ostrzeżenie o duplikacie; audyt `document.upload`; polecenie `baza-wiedzy storage-cleanup`. Etap B: przejścia statusu przetwarzania i metadane maila dla `ingestion`; strona dokumentu wczytuje wyodrębnione części z `ingestion`, gdy ten moduł jest włączony. Faza 3, etap A (ADR-025): akcje cyklu życia na stronie dokumentu — Publikuj, Cofnij publikację, Archiwizuj, Przywróć (`lifecycle.py`, `POST /documents/<id>/lifecycle`, audyt `document.publish|unpublish|archive|restore`), kolumny `published_at`, `published_by`, `archived_at` i warunek „opublikowany wymaga opublikowanej wersji” (`documents_0004`), właściciel widzi swoje dokumenty we wszystkich stanach, kolumna stanu w „Moich dokumentach”, ostrzeżenie, gdy treści nie można (w pełni) wyszukać; pobieranie oryginału (`download.py`, `GET /documents/<id>/download`, audyt `document.download`); `documents.publish_any` i `documents.edit_any` wymagają w roli `documents.read_drafts`. Wersje użytkownika, kategorie, obsługa duplikatów i podgląd w Fazie 5.
