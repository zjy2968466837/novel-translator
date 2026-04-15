# Retry & Correction Spec

## Quality checks
- Empty output
- Output too short relative to source
- Mojibake replacement characters
- HTML structure breakage signals
- Glossary violations

## Retry stages
1. `LightFix`: same prompt with lightweight constraints.
2. `StrictRetranslate`: stronger constraints and format enforcement.
3. `Fallback`: last attempt strategy and manual review handoff.

## Retry policy
- Max retries configurable.
- Exponential backoff: `base_backoff * 2^attempt`.
- Chapter-level status persisted for resume.

## Manual intervention
- Failed chapters remain queued for manual retry from UI retry center.
