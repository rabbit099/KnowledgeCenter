# PostgreSQL 16 with the Polish Hunspell dictionary prepared for full-text search.
# The image only ships the files; Alembic migrations create the dictionary and
# text-search configurations (see docs/en/search.md, ADR-016).
FROM postgres:18.4-trixie

# Pinned Debian package (source: SJP.PL via libreoffice-dictionaries, licence chosen: Apache-2.0).
ARG HUNSPELL_PL_VERSION=1:25.2.3-1
ARG PL_AFF_SHA256=7c37b9bde78054e43365b488a13859094c88bc66664b5b7a7bb073626454b38e
ARG PL_DIC_SHA256=215fd73aa47b11e7fdd2e4d655e9fe37be4acdae16ff833badcfdfce79110aad

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends "hunspell-pl=${HUNSPELL_PL_VERSION}"; \
    echo "${PL_AFF_SHA256}  /usr/share/hunspell/pl_PL.aff" | sha256sum -c -; \
    echo "${PL_DIC_SHA256}  /usr/share/hunspell/pl_PL.dic" | sha256sum -c -; \
    TS="$(pg_config --sharedir)/tsearch_data"; \
    TMP="$(mktemp -d)"; \
    # PostgreSQL reads tsearch_data files as UTF-8; the package ships ISO-8859-2.
    iconv -f ISO-8859-2 -t UTF-8 /usr/share/hunspell/pl_PL.aff \
        | sed 's/^SET ISO8859-2/SET UTF-8/' > "$TMP/pl_pl.affix"; \
    iconv -f ISO-8859-2 -t UTF-8 /usr/share/hunspell/pl_PL.dic > "$TMP/pl_pl.dict"; \
    # postgresql-common (pg_updatedicts) links pl_pl.* into tsearch_data while the package
    # is installed; remove the package and its links, then install plain files.
    apt-get purge -y --auto-remove hunspell-pl; \
    rm -f "$TS/pl_pl.affix" "$TS/pl_pl.dict"; \
    install -m 0644 "$TMP/pl_pl.affix" "$TMP/pl_pl.dict" "$TS/"; \
    rm -rf "$TMP"; \
    rm -rf /var/lib/apt/lists/*

COPY dictionary/polish.stop dictionary/LICENSE-pl_pl-dictionary.txt /usr/share/postgresql/16/tsearch_data/

# Bootstrap only: creates the non-superuser application role and database on an empty volume.
COPY --chmod=0755 initdb/10-app-role.sh /docker-entrypoint-initdb.d/
