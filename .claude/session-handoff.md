# Session Handoff — 2026-02-26 (Ultra Audit Implementation)

## Deployment State

| Field | Value |
|-------|-------|
| Cloud Run service | `sacto-wcag-api` |
| Region | `us-central1` |
| Last deployed revision | `sacto-wcag-api-00034-vfn` (pre-audit — NOT YET REDEPLOYED) |
| GCP Project | `report-conciliation-487916` |
| Service URL | `https://sacto-wcag-api-738802459862.us-central1.run.app` |
| Frontend URL | `https://hitl-dashboard.vercel.app` |
| Git branch | `main` |
| DB backend | SQLite (ephemeral on Cloud Run — no Postgres configured) |
| DB schema version | N/A (no migrations — SQLite auto-creates) |

## IMPORTANT: Changes NOT Yet Deployed

All changes below are LOCAL ONLY. A new deploy + verify_deploy.py run is required before claiming anything is fixed in production.

## Test State

- **647 passed, 4 skipped, 1 xpassed** (verified after all 3 waves combined)
- Test command: `python -m pytest tests/ -x -q`
- Zero regressions across all waves

## Ultra Audit Summary

A 9-stream deep-dive audit examined all ~120 files / 58,000+ lines. Found 220 raw findings, consolidated to **185 unique findings** after deduplication. Implementation was done in 3 waves of parallel agents.

### Wave 1 — ~50 findings fixed (CI/CD, WCAG Checker, Gates, API Clients, Frontend)

**Agent 1A — CI/CD & Infrastructure:**
- CRITICAL-7.1: `--set-env-vars` → `--update-env-vars` in CI (prevents credential wipe)
- CRITICAL-7.2: Added verify_deploy.py step to GitHub Actions CI
- HIGH-7.10: Deploy concurrency lock (cancel-in-progress: false)
- HIGH-7.7: Removed `|| true` from mypy in Cloud Build
- HIGH-7.9: Pinned all 27 Python dependencies to exact versions
- MEDIUM-7.11: Added HEALTHCHECK to root Dockerfile
- MEDIUM-7.13: Coverage threshold raised to 80%

**Agent 1B — WCAG Checker:**
- CRITICAL-5.1: check_2_4_3_focus_order now returns PASS when sequential
- CRITICAL-5.2: check_1_4_3_contrast returns NOT_APPLICABLE (can't check at IR stage)
- CRITICAL-5.3: check_2_4_7_focus_visible returns NOT_APPLICABLE
- CRITICAL-5.5: Decorative check runs BEFORE F30/F65 (no more contradictions)
- HIGH-5.8: Removed "line" from decorative alt regex
- HIGH-5.7: check_2_4_4 checks ALL block types (not just paragraphs)
- HIGH-5.12: check_3_3_3 returns descriptive NA for forms
- MEDIUM-5.15: Single-page docs with 10+ blocks require headings
- MEDIUM-5.19: Detects filename stems as invalid titles
- MEDIUM-5.21: Alt text >150 chars severity CRITICAL → MODERATE

**Agent 1C — Gate System:**
- CRITICAL-5.4: G4 fallback uses pikepdf for /StructTreeRoot, /MarkInfo, /Lang. No validator = hard_fail
- HIGH-5.9: G3 checks EACH table individually (not just "any table has headers")
- HIGH-5.13: VeraPDF unavailability bypass when Adobe G4 passed
- HIGH-2.7: axe-core exception handler narrowed (no more bugs hidden as unavailability)
- HIGH-2.8: Adobe checker exception handler narrowed
- MEDIUM-5.20: _RE_IMG_EMPTY_SRC matches both quote styles
- MEDIUM-5.17: Coverage matrix shows contrast as manual review

**Agent 1D — API Clients:**
- CRITICAL-4.1: Adobe Extract timeout (240s)
- CRITICAL-4.2: Adobe Auto-Tag timeout (240s)
- CRITICAL-4.3: GCS singleton client + timeout=120 on all operations
- CRITICAL-4.4: Document AI retry + timeout=180
- HIGH-4.6: Fixed backoff formula + jitter
- HIGH-4.7: Adobe Checker retry, score 0.5→0.0, file handle fix
- HIGH-4.8: Pub/Sub timeout=30
- HIGH-4.11: VeraPDF retry for 5xx
- HIGH-4.12: Adobe Checker module-level availability check
- MEDIUM-4.13: axessense is_available checks status==200
- MEDIUM-4.14: verapdf is_available checks status==200
- MEDIUM-4.18: Adobe retry catches network errors
- MEDIUM-4.21: Retry for HTTP 429

**Agent 1E — Frontend:**
- CRITICAL-6.1: `/api/documents` → `/api/v1/documents`
- CRITICAL-6.2: fetchDocuments normalizes document_id → id
- CRITICAL-6.3: Reads all 3 response headers (X-Task-Id, X-Pipeline-Metadata, X-Remediation-Delta)
- CRITICAL-6.4: DecisionBadge crash guard
- HIGH-6.5: Empty approved_ids sends "[]"
- HIGH-6.7: sessionStorage persistence for analysis store
- HIGH-6.8: 401 interception with redirect
- HIGH-6.9: Uses criterion instead of finding_id
- HIGH-6.10: Skips proposal creation for sync conversions
- MEDIUM-6.11: Stale closure fix with functional state update
- MEDIUM-6.15: WCAGCriterion changed to string type
- MEDIUM-6.16: reviewed_by uses AuthContext userId

### Wave 2 — ~41 findings fixed (Router, Converter, Remediator, Database, AI Drafting)

**Agent 2A — Router:**
- CRITICAL-1.3: Semaphore TOCTOU race fixed — atomic acquire with asyncio.wait_for
- CRITICAL-1.4: ZIP double-pipeline documented with dedup guard explanation
- CRITICAL-1.2: Event cache now thread-safe with threading.Lock
- HIGH-1.7: get_db() called with correct settings.db_path
- HIGH-9.14: X-Remediation-Delta now includes before_failed/before_passed
- MEDIUM-1.14: PDF magic-byte validation (%PDF check)
- MEDIUM-1.11: IR non-persistence documented as design constraint
- MEDIUM-9.26: Delta header present on error responses

**Agent 2B — Converter:**
- HIGH-1.5: AI alt text degraded status now logged with warning
- HIGH-1.8: drop_running_artifacts threshold requires 3+ occurrences (was 8)
- HIGH-1.9: VeraPDF regression gate non-blocking documented with reasoning
- MEDIUM-1.10: stage_validate returns "degraded" when validation_blocked=True
- MEDIUM-1.13: Fallback page default documented with DEBUG logging
- MEDIUM-2.25: Event cache exception logged instead of swallowed
- MEDIUM-9.18: VeraPDF unavailability logged at WARNING

**Agent 2C — Deterministic Remediator:**
- CRITICAL-5.10: Language detection from PDF metadata + content heuristic (CJK, de, fr, es)
- HIGH-5.11: Table header promotion validated with confidence scoring (vetoes numeric rows)
- MEDIUM-5.16: Heading hierarchy normalizes docs starting at H3+ down to H1
- MEDIUM-9.19: Reading order y-rounding improved (nearest 5 units instead of 10)

**Agent 2D — Database + Auth:**
- CRITICAL-3.2: SQLiteBackend thread-safe with threading.Lock on all methods
- CRITICAL-3.4: Token expiry field now checked during authentication
- CRITICAL-2.3: Migration bare except → specific handling (re-raises unexpected errors)
- HIGH-3.8: Review decision update uses optimistic concurrency (WHERE clause)
- HIGH-3.9: Alembic migration detection at startup (warns if alembic_version table exists)
- HIGH-3.10: Postgres DDL updated with missing columns (hash_algorithm, token_expires_at)
- HIGH-2.4: Auth DB lookup failures logged at WARNING (not DEBUG)
- HIGH-2.6: JSON decode failure logged with table, field, truncated value
- MEDIUM-2.28: migrations/env.py settings import failure logged
- MEDIUM-3.15: SecretStr deferred — vulnerability documented in comments

**Agent 2E — AI Drafting + Vertex:**
- CRITICAL-2.1: Pub/Sub publish failure sets document to "failed" (not stalled forever)
- CRITICAL-2.2: Empty elements JSON logged with warning (zero-work case now observable)
- HIGH-4.5: Vertex AI timeout now passed to generate_content()
- HIGH-4.9: vertexai.init() moved to one-time _do_init() (not in retry loop)
- HIGH-9.12: Placeholder changed to "Image requires manual alt text description"
- HIGH-9.13: Alt text quality check (detects regurgitation, generic, too short)
- HIGH-2.10: Telemetry persistence failure logged at ERROR with data
- MEDIUM-2.16: Vertex AI re-initialization on next call if startup failed
- MEDIUM-2.17: JSON parse failure already logged (no change needed)
- MEDIUM-2.19: get_current_trace_id catches Exception not BaseException
- MEDIUM-4.20: Model creation documented (per-call is intentional for system_instruction)
- MEDIUM-4.22: Retry params extracted to named class constants

### Wave 3 — ~26 findings fixed (Silent failures, Session continuity, Frontend misc)

**Agent 3A — Silent Failures + Misc:**
- MEDIUM-2.18: OTLP exporter failure warning improved
- MEDIUM-2.20: OCR profiling failure defaults to has_extractable_text=False
- MEDIUM-2.21: PDF open failure warning improved
- MEDIUM-2.27: Enhancement error flags enhancement_failed=true in metadata
- MEDIUM-4.16: Pub/Sub ack ordering documented (already correct)
- MEDIUM-4.17: axe-core Playwright evaluate timeout=30s
- MEDIUM-4.23: Pub/Sub push handler validates message structure
- MEDIUM-5.18: skip_level rule warns when prev_level absent
- LOW-2.30/2.31/2.32: chunker error handlers upgraded to WARNING

**Agent 3B — Session Continuity + Infrastructure:**
- CRITICAL-7.4: Terraform GCS state backend uncommented
- CRITICAL-9.1: Canary test PDF upgraded with structured content (headings, paragraphs)
- HIGH-8.13: Invariant enforcement added to verify_deploy.py as Category 9
- MEDIUM-8.23: DB backend check actually validates probe status
- MEDIUM-7.12: Adobe credential placeholders documented with warnings
- MEDIUM-8.21: Execution ledger marked as superseded
- MEDIUM-8.24: Netlify Windows paths → Unix paths
- LOW-7.18: Terraform variable validation added for container_image_tag

**Agent 3C — Frontend + Misc:**
- MEDIUM-6.12: PDFDocument GCS path fields marked optional
- MEDIUM-6.13: PipelineMetadata type unified between api.ts and upload/page.tsx
- MEDIUM-6.14: batchApproveAltText error reconciliation (reverts optimistic update)
- HIGH-6.6: batchApprove documented (not dead code — backend exists)
- LOW-6.18: Pending review count heuristic documented
- LOW-2.33: clause_fixers exception handler logs instead of silently continuing

## Known Invariants (MUST remain true)

1. **NO SQLite fallback in prod**: `database.py:get_db()` — if `db_backend=postgres` is set, connection failure MUST raise
2. **NO silent fallback on user persistence**: `auth.py:_seed_user_to_db_and_cache()` — DB write failure raises RuntimeError
3. **CORS exposes all custom headers**: main.py expose_headers MUST include X-Task-Id, X-Pipeline-Version, X-Pipeline-Metadata, X-Remediation-Delta
4. **Gate fail-closed**: Validation tool unavailability MUST report soft_fail/P1/flag_hitl, never pass silently
5. **StageNoOpError for zero-work stages**: AI alt text stage raises StageNoOpError when Vertex AI unavailable
6. **Thread-safe database**: SQLiteBackend wraps all operations with threading.Lock
7. **Token expiry enforced**: Auth checks token_expires_at during verification
8. **Atomic semaphore**: Pipeline semaphore acquired atomically with asyncio.wait_for

## Remaining Findings (NOT fixed — deferred or out of scope)

### Deferred (require architectural decisions):
- CRITICAL-3.1: SQLite on ephemeral disk (needs Cloud SQL or GCS FUSE decision)
- CRITICAL-3.3: Pipeline endpoints authentication (needs API key infrastructure)
- HIGH-7.5: Rate limiting for public endpoints (needs Cloud Run IAM or API Gateway)
- HIGH-7.8: No approval gate before production deploy (needs GitHub Environment setup)
- HIGH-9.17: reportlab PDF output not PDF/UA (needs alternative PDF library)
- MEDIUM-3.15: SecretStr for credentials (deferred — would break all callers)

### LOW findings not fixed (30 items):
Most LOW findings are documentation, style, or edge cases documented in `.claude/audit/consolidated-backlog.md` Batch 13.

## Next Steps (In Priority Order)

1. **Deploy to Cloud Run** and run `verify_deploy.py --revision <NEW_REV>` to verify all changes in production
2. **Address CRITICAL-3.1** (SQLite persistence) — requires infra decision: Cloud SQL vs GCS FUSE vs Cloud Storage JSON backend
3. **Address CRITICAL-3.3** (API authentication) — add API key or IAP to pipeline endpoints
4. **Address HIGH-7.5** (rate limiting) — prevent cost abuse on public endpoints
5. **Fix remaining LOW findings** from Batch 13
