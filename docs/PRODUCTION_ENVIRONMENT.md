# ADT Production Environment and Secret Contract

## Principle

Production configuration is divided into three boundaries:

1. public frontend build configuration;
2. server runtime configuration;
3. server secrets.

Values must never cross from a more privileged boundary into a less privileged
one.

## Public frontend configuration

The following values are compiled into the browser bundle:

- `VITE_ADT_API_URL`
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_PUBLISHABLE_KEY`

They must be assumed publicly observable.

The Supabase publishable key is not privileged database authority. Security
must continue to rely on authentication, authorization, RLS and backend
authority boundaries.

No password, database connection URL, service-role key, secret key, Telegram
token, private key or exchange secret may use a `VITE_*` variable.

## Backend runtime configuration

Production backend processes receive non-secret runtime configuration including:

- `ADT_ENVIRONMENT=production`
- `ADT_LOG_LEVEL`
- `ADT_CORS_ORIGINS`
- `ADT_API_HOST`
- `ADT_API_PORT`
- `ADT_DATA_DIR`
- optional bounded subsystem settings and database-pool limits

The production container topology fixes `ADT_DATA_DIR=/srv/adt/data`.

Production configuration validation requires non-local HTTPS browser origins
and a non-local HTTPS Supabase URL.

## Publishable Supabase configuration

`SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` are required by the current
backend contract.

The publishable key is intended for public-client use and does not grant
service-role authority. The backend nevertheless wraps its configured value as
sensitive data so that normal representations and configuration errors do not
echo it unnecessarily.

`VITE_SUPABASE_PUBLISHABLE_KEY` may carry the corresponding public-client key
for the frontend build.

## Server secrets

`SUPABASE_DATABASE_URL` is a server secret because it contains PostgreSQL
connection credentials.

It must:

- never be compiled into the frontend;
- never use a `VITE_*` variable;
- never be committed to Git;
- require TLS in production;
- be supplied to the production runtime through the deployment environment or
  a future reviewed secret-management mechanism.

Optional integration credentials such as `TELEGRAM_BOT_TOKEN` are also server
secrets if the integration is enabled.

Supabase secret/service-role keys are not part of the current production
container contract and must not be introduced without a separate reviewed
authority requirement.

## Production validation

The backend already rejects invalid production boundaries, including:

- local or non-HTTPS CORS origins;
- local or non-HTTPS Supabase URLs;
- PostgreSQL URLs that do not require TLS.

Configuration errors must identify invalid variable names without echoing
secret values.

## Local production environment file

`.env.production.example` is tracked and contains placeholders only.

The real deployment file is:

`.env.production`

It is Git-ignored and must remain host-local or be replaced by a reviewed
secret-management facility.

When Docker Compose becomes available, the intended invocation pattern is:

`docker compose --env-file .env.production -f docker-compose.production.yml ...`

The command above defines configuration loading only. It does not itself grant
permission to deploy, start trading processes or mutate production state.

## Safety boundary

Environment provisioning does not grant:

- PAPER activation;
- capital authorization;
- LIVE trading authority;
- exchange-account authority;
- service-role authority.

Secrets and configuration provide connectivity only. Existing application
authority checks remain mandatory.
