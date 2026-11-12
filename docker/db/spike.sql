-- Diagnostic script for the Polish dictionary (Phase 0 spike). NOT run automatically:
-- migrations create the real objects. Run as the application user (not a superuser):
--   psql -v ON_ERROR_STOP=1 -f spike.sql
-- Everything happens in a transaction that is rolled back.
\timing on
BEGIN;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TEXT SEARCH DICTIONARY spike_pl_hunspell (
    TEMPLATE = ispell, DictFile = pl_pl, AffFile = pl_pl, StopWords = polish
);
CREATE TEXT SEARCH CONFIGURATION spike_pl (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION spike_pl
    ALTER MAPPING FOR asciiword, word, hword, hword_part, hword_asciipart, asciihword
    WITH spike_pl_hunspell, simple;

-- First use loads the dictionary into this backend: time it.
SELECT ts_lexize('spike_pl_hunspell', 'faktura') AS first_call;

SELECT w AS word, ts_lexize('spike_pl_hunspell', w) AS lexemes
FROM unnest(ARRAY['faktura','faktury','fakturze','fakturą','umowa','umowie','umów','żółw',
                  'zaświadczenie','zaświadczeń','pracownika','pracownikom','urlop','urlopu',
                  'ZAMÓWIENIE','umowach','zawarł','zawarta','nie','xyzqw']) AS w;

SELECT to_tsvector('spike_pl', 'Faktury za umowy zawarte z pracownikami w 2024 r., nr FV/2024/0012')
    AS vector;
SELECT to_tsvector('spike_pl', 'Umowa o dzieło') @@ websearch_to_tsquery('spike_pl', 'umowie')
    AS inflected_match;
ROLLBACK;
