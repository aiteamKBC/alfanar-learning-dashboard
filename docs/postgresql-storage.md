# PostgreSQL storage without changing analytics logic

The application continues using its existing Django models, migrations, queries,
identity rules, attendance reconciliation and transcript calculations. PostgreSQL
only replaces the SQLite storage location. No new sync, source import, identity
restoration or production website deployment is triggered by this change.

## Connection

Set these privately in the project `.env` or the production service environment:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
DATABASE_SCHEMA=alfanar_dashboard
```

Do not commit the real URL. Leave `DATABASE_URL` empty to keep using local SQLite.
Leaving `DATABASE_SCHEMA` empty preserves the original PostgreSQL search path.
Schema names accept lowercase letters, digits and underscores only.

For a dedicated schema, Neon pooler hostnames are resolved to the corresponding
direct hostname for the same endpoint, database and role. This supports the
session search path used by Django; no credentials or database names are changed.
Neon describes the limitation in its [connection pooling documentation](https://neon.com/docs/connect/connection-pooling).

Install `backend/requirements.txt` locally or `backend/requirements-production.txt`
on the production host. Both include the PostgreSQL driver.

## One-time transfer

Run from the repository root with the configured destination URL:

```powershell
.\.venv\Scripts\python backend/manage.py transfer_sqlite_to_postgres
.\.venv\Scripts\python backend/manage.py transfer_sqlite_to_postgres --apply
.\.venv\Scripts\python backend/manage.py transfer_sqlite_to_postgres --verify-only
```

The default is a dry run. `--source PATH` selects a different SQLite source;
otherwise the source is `backend/db.sqlite3`. The source must already have all
current migrations applied. Every run creates a consistent, read-only-sourced
SQLite backup under ignored `data/database-transfer/`, with a private verification
report containing counts and hashes rather than row contents.

Applying requires an empty dedicated schema and refuses `public`. It creates the
schema and applies the existing Django migrations, then copies all managed model
tables, including M2M membership tables, current account/permission records,
historical evidence, attendance, lecture resources and transcript text. Original
primary keys, UUIDs, timestamps, JSON values and nulls are preserved. Model save
callbacks and source normalization are not invoked. Video files remain on their
original Microsoft storage; their existing access references are retained.

Foreign keys are checked and integer sequences are reset. Every copied row's
complete values are hashed and compared with the snapshot before commit. Schema
creation, migrations and data writes share one PostgreSQL transaction: failure
rolls back the destination. The source SQLite database is never modified.

`--apply` never overwrites an occupied schema. `--verify-only` compares it without
writing. Once normal application activity changes PostgreSQL, a comparison with
an older SQLite snapshot may correctly differ; do not overwrite live data to make
the old comparison pass.

## Production application

The remote database can be prepared before the website is deployed. Deploy the
code through the normal reviewed server process, install production requirements,
set the same `DATABASE_URL` and `DATABASE_SCHEMA` in the actual service environment,
apply any subsequent migrations and restart the existing backend service.
Do not rerun the SQLite transfer over a live populated schema. Keep the existing
Google and Microsoft credentials configured privately for future sync and video
playback. This storage transfer does not activate or reconfigure schedulers.
