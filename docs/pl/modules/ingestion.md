# Moduł: ingestion

> Dotyczy: v0.0.0 (przed pierwszym wydaniem) · Ostatnia aktualizacja: 2026-11-12

## Zakres

Zamienia zapisany plik na części tekstu: rozpoznaje rodzaj pliku po zawartości, uruchamia ekstraktor dla danego formatu, uruchamia OCR dla zeskanowanych stron PDF, wykrywa język każdej części, dzieli długi tekst i pilnuje limitów bezpieczeństwa. Działa w `worker`. Szczegóły: [ingestion-and-ocr.md](../ingestion-and-ocr.md).

## Zależności

Wymaga [documents](documents.md).

## Uprawnienia

Brak własnych. Akcja „przetwórz ponownie” używa uprawnień z istniejącego katalogu (dokładny kod zostanie ustalony w Fazie 2).

## Tabele

Brak w MVP; prefiks `ing_` jest zarezerwowany. Status przetwarzania i ostrzeżenia są w `doc_versions` i aktualizowane przez `documents.services.api`.

## Zdarzenia

- Subskrybuje `document.version.uploaded`: kolejkuje zadanie przetwarzania (Dramatiq).
- Publikuje `document.version.extracted` (`document_id`, `version_id`, `parts`, `metadata`) po zakończeniu wyciągania tekstu. Każda część ma typ (`page`, `body`, `attachment`), nazwę, numer strony, numer fragmentu, język, tekst i flagę OCR. Moduł `search` ją indeksuje.

## Interfejs publiczny

`modules/ingestion/services/api.py`. Planowane (jeszcze niezaimplementowane): ponowne przetworzenie jednej wersji lub wszystkich oczekujących.

## Punkty rozszerzeń

Rejestr ekstraktorów: nowy format pliku to nowy plik w `ingestion/extractors/`, który rejestruje się dla swojego rodzaju; żaden inny plik nie musi się zmieniać.

## Status

Faza 0: manifest i pusty interfejs publiczny. Implementacja w Fazie 2 (ekstraktory, pipeline) i Fazie 4 (OCR).
