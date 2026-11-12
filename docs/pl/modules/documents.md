# Moduł: documents

> Dotyczy: v0.1.0 · Ostatnia aktualizacja: 2026-11-12

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
- Publikuje `document.published` (`document_id`, `version_id`), gdy wersja staje się widoczna dla czytelników. W MVP bez subskrybentów.
- Niczego nie subskrybuje.

## Interfejs publiczny

`modules/documents/services/api.py`, jedyna część, którą mogą importować inne moduły. Planowana zawartość (jeszcze niezaimplementowana): odczyt wersji, aktualizacja statusu i metadanych przetwarzania, zapis metadanych maila, otwarcie pliku oryginalnego i zapis pliku pochodnego oraz fragment SQL z regułą widoczności dokumentów dla `search`.

## Punkty rozszerzeń

Magazyn plików: pliki oryginalne i pochodne są dostępne przez interfejs magazynu (najpierw dysk lokalny), więc można dodać inny backend bez zmian u wywołujących.

## Status

Faza 0: manifest, uprawnienia, pozycja menu i pusty interfejs publiczny. Implementacja w Fazie 2 (upload) i Fazie 5 (cykl życia, wersje, kategorie).
