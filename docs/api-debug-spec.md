# API Debug Spec

## Scope
Capture API request/response for troubleshooting and replay-safe audits.

## Data fields
- request_id
- task_id
- chapter_id
- provider
- status_code
- duration_ms
- usage_tokens
- created_at
- request payload JSON
- response payload JSON
- optional error

## Storage model
- Raw JSON entry files: `./.data/api_logs/<task_id>/<timestamp>_<request_id>.json`
- Indexed metadata in SQLite `api_logs` table

## Redaction levels
- `Raw`: only for local trusted debugging.
- `Redacted`: default; masks api_key/token/authorization and long text fragments.

## Export
- Export all task logs into ZIP by `task_id`.
- Include redacted JSON by default.
