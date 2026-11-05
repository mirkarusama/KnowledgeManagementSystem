# Railway: Postgres with the audit/memory tables script built in (runs once, on an empty volume).
FROM postgres:16-alpine
COPY db/init.sql /docker-entrypoint-initdb.d/01-init.sql
# Data in a sub-folder: a Railway volume's top folder has a "lost+found" entry that stops Postgres starting.
ENV PGDATA=/var/lib/postgresql/data/pgdata
