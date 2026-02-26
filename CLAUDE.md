# Sacramento County WCAG 2.1 AA PDF Remediation Pipeline — Project Specification

## Override Log

```
OVERRIDE: Skipped Phases 1, 2, 3.
Reason: Requirements are locked from client engagement; user elects to proceed directly to implementation.
Risk: No formal Requirements Matrix, Gap Review, or Assumptions verification was performed.
      Gaps are captured in the Assumptions Register below and MUST be verified before Tier C delivery.
Tier: C — Enterprise Client Delivery (Full verification pipeline mandatory).
Date: 2026-02-23
```

## Overview

An asynchronous document processing pipeline for Sacramento County that remediates legacy PDF documents to meet WCAG 2.1 AA and PDF/UA compliance. The system extracts structural data from PDFs using Adobe Acrobat Services, generates semantic HTML with AI-drafted alt text via Vertex AI (Gemini), routes complex elements through a Human-In-The-Loop (HITL) React dashboard for review, and recompiles approved content into fully compliant PDF/UA documents.

**Client**: Sacramento County
**Audience**: County staff (HITL reviewers), county IT (deployment/ops), end users (accessible document consumers)
**Problem**: Legacy PDFs lack proper tagging, alt text, reading order, and table structure — failing WCAG 2.1 AA requirements.

## Objectives

1. Automate extraction of structural content from untagged/poorly-tagged PDFs
2. Generate contextual alt text for images and figures using Vertex AI
3. Flag complex elements (nested tables, forms, mathematical notation) for human review
4. Produce PDF/UA compliant output documents with full tag structure
5. Provide a HITL dashboard for county staff to review, approve, and correct AI-generated remediation

## Scope

### In Scope
- PDF ingestion and queueing via Cloud Run + Pub/Sub
- Structural extraction via Adobe Acrobat Services (Extract + Auto-Tag)
- AI-generated alt text via Vertex AI (Gemini 1.5 Pro)
- React-based HITL review dashboard
- PDF/UA recompilation from approved semantic HTML
- axe-core automated accessibility validation

### Out of Scope
- OCR for scanned/image-only PDFs (Phase 2 — assumed all PDFs have extractable text)
- Batch migration of existing county document archives
- Integration with county DMS (Document Management System) — API-only for POC
- Mobile-responsive HITL dashboard (desktop-first for POC)

## Tech Stack

| Category | Choice | Version |
|----------|--------|---------|
| Cloud Platform | Google Cloud Platform | — |
| Compute | Cloud Run | v2 (2nd gen) |
| Message Queue | Cloud Pub/Sub | — |
| AI / LLM | Vertex AI — Gemini 1.5 Pro | gemini-1.5-pro-002 |
| PDF Extraction | Adobe Acrobat Services — PDF Extract API | v4.x |
| PDF Auto-Tagging | Adobe Acrobat Services — Auto-Tag API | v4.x |
| PDF Generation | Adobe Acrobat Services — PDF Accessibility Checker | v4.x |
| Backend Language | Python | 3.11+ |
| Backend Framework | FastAPI | 0.109+ |
| Data Validation | Pydantic v2 | 2.5+ |
| HITL Frontend | React + TypeScript | React 18, TS 5.x |
| UI Components | Shadcn/ui + Tailwind CSS | Latest |
| Accessibility Testing | axe-core | 4.8+ |
| Container Runtime | Docker | 24+ |
| IaC | Terraform (GCP provider) | 1.7+ |

## Data Models / Type Definitions

```python
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional

class DocumentStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    AI_DRAFTING = "ai_drafting"
    HITL_REVIEW = "hitl_review"
    APPROVED = "approved"
    RECOMPILING = "recompiling"
    COMPLETE = "complete"
    FAILED = "failed"

class WCAGCriterion(str, Enum):
    ALT_TEXT = "1.1.1"           # Non-text Content
    INFO_RELATIONSHIPS = "1.3.1" # Info and Relationships
    READING_ORDER = "1.3.2"      # Meaningful Sequence
    SENSORY = "1.3.3"            # Sensory Characteristics
    COLOR_CONTRAST = "1.4.3"     # Contrast (Minimum)
    IMAGES_OF_TEXT = "1.4.5"     # Images of Text
    HEADINGS_LABELS = "2.4.6"    # Headings and Labels
    LINK_PURPOSE = "2.4.4"       # Link Purpose (In Context)
    LANGUAGE = "3.1.1"           # Language of Page
    NAME_ROLE_VALUE = "4.1.2"    # Name, Role, Value

class ComplexityFlag(str, Enum):
    SIMPLE = "simple"            # Auto-remediation sufficient
    REVIEW = "review"            # AI draft + human review
    MANUAL = "manual"            # Requires manual remediation

class PDFDocument(BaseModel):
    id: str = Field(description="UUID for the document")
    filename: str
    gcs_input_path: str          # gs://bucket/input/filename.pdf
    gcs_output_path: Optional[str] = None
    status: DocumentStatus = DocumentStatus.QUEUED
    page_count: int = 0
    created_at: datetime
    updated_at: datetime

class ExtractionResult(BaseModel):
    document_id: str
    adobe_job_id: str
    extracted_json_path: str     # GCS path to Adobe Extract JSON
    auto_tag_json_path: str      # GCS path to Adobe Auto-Tag JSON
    elements_count: int
    images_count: int
    tables_count: int

class WCAGFinding(BaseModel):
    id: str
    document_id: str
    element_id: str              # Reference to extracted element
    criterion: WCAGCriterion
    severity: str                # "critical", "serious", "moderate", "minor"
    description: str
    suggested_fix: Optional[str] = None
    ai_draft: Optional[str] = None  # AI-generated remediation
    complexity: ComplexityFlag = ComplexityFlag.SIMPLE

class HITLReviewItem(BaseModel):
    id: str
    document_id: str
    finding_id: str
    element_type: str            # "image", "table", "heading", "link", etc.
    original_content: dict       # Raw extracted element data
    ai_suggestion: str           # AI-drafted alt text or tag structure
    reviewer_decision: Optional[str] = None  # "approve", "edit", "reject"
    reviewer_edit: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None

class RemediatedDocument(BaseModel):
    document_id: str
    semantic_html_path: str      # GCS path to approved HTML
    pdfua_output_path: str       # GCS path to final PDF/UA
    axe_score: Optional[float] = None
    wcag_violations_remaining: int = 0
    manual_review_items: int = 0
```

## File Structure

```
sacramento-wcag/
  CLAUDE.md                        # This spec
  wcag_execution_ledger.jsonl      # Phase 4 execution ledger
  .claude/
    artifacts/
      state.json                   # Phase state
  infra/
    terraform/
      main.tf                     # GCP resources (Cloud Run, Pub/Sub, GCS)
      variables.tf
      outputs.tf
    Dockerfile                     # Cloud Run container
    cloudbuild.yaml               # CI/CD
  services/
    common/
      models.py                   # Pydantic models (above)
      config.py                   # Environment config
      gcs_client.py               # GCS utilities
      pubsub_client.py            # Pub/Sub utilities
    ingestion/
      main.py                     # FastAPI — receives PDFs, queues to Pub/Sub
      router.py
    extraction/
      main.py                     # Pub/Sub consumer — calls Adobe Extract + Auto-Tag
      adobe_client.py             # Adobe Acrobat Services wrapper
      parser.py                   # Parse Adobe JSON into WCAGFinding objects
    ai_drafting/
      main.py                     # Pub/Sub consumer — calls Vertex AI
      vertex_client.py            # Vertex AI / Gemini wrapper
      prompt_templates.py         # Alt text generation prompts
    recompilation/
      main.py                     # Rebuilds PDF/UA from approved HTML
      pdfua_builder.py
  hitl-dashboard/
    package.json
    src/
      app/
        layout.tsx
        page.tsx                  # Dashboard home — queue of documents
      components/
        document-queue.tsx        # List of documents pending review
        review-panel.tsx          # Side-by-side: original element vs AI suggestion
        element-viewer.tsx        # Renders extracted element (image, table, etc.)
        approval-controls.tsx     # Approve / Edit / Reject buttons
      lib/
        api.ts                    # API client for backend
        types.ts                  # TypeScript types matching Pydantic models
  tests/
    test_extraction.py
    test_ai_drafting.py
    test_recompilation.py
    test_wcag_validation.py
```

## Module Specifications

### Module A: Infrastructure (infra/)
- **Purpose**: Scaffold GCP Cloud Run, Pub/Sub topics/subscriptions, GCS buckets, and IAM
- **Components**: Terraform configs, Dockerfile, cloudbuild.yaml
- **Behavior**: Terraform provisions resources; Docker builds Python 3.11 image with FastAPI
- **Acceptance Criteria**:
  - [ ] `terraform plan` succeeds with no errors
  - [ ] Cloud Run service deploys and responds to health check
  - [ ] Pub/Sub topic + subscription created for document processing pipeline
  - [ ] GCS buckets created for input PDFs, extraction results, and output PDFs

### Module B: PDF Extraction (services/extraction/)
- **Purpose**: Extract structural content from PDFs using Adobe Acrobat Services
- **Components**: Adobe API client, JSON parser, Pub/Sub consumer
- **Behavior**: Receives document ID from Pub/Sub → downloads PDF from GCS → calls Adobe Extract API → calls Auto-Tag API → parses results into WCAGFinding objects → publishes to AI drafting topic
- **Acceptance Criteria**:
  - [ ] Adobe Extract API returns valid structural JSON for test PDF
  - [ ] Auto-Tag API returns valid tag structure
  - [ ] Parser correctly identifies images, tables, headings, and links
  - [ ] Each element gets a ComplexityFlag based on element type and nesting depth
  - [ ] Results stored in GCS and document status updated

### Module C: AI Drafting (services/ai_drafting/)
- **Purpose**: Generate contextual alt text and tag suggestions using Vertex AI (Gemini 1.5 Pro)
- **Components**: Vertex AI client, prompt templates, Pub/Sub consumer
- **Behavior**: Receives extraction results → for each image/figure element, sends bounding box context + surrounding text to Gemini → generates descriptive alt text → for tables, generates semantic HTML structure → flags REVIEW/MANUAL items for HITL
- **Acceptance Criteria**:
  - [ ] Alt text generated for all images (WCAG 1.1.1)
  - [ ] Table structure mapped to semantic HTML with headers (WCAG 1.3.1)
  - [ ] Complex nested tables (>2 levels) flagged as MANUAL
  - [ ] AI drafts stored as HITLReviewItem records
  - [ ] Gemini API errors handled with retry + fallback to MANUAL flag

### Module D: HITL Dashboard (hitl-dashboard/)
- **Purpose**: React dashboard for county staff to review, approve, edit, or reject AI-generated remediation
- **Components**: Document queue, review panel, element viewer, approval controls
- **Behavior**: Displays queue of documents with pending review items → reviewer sees original element alongside AI suggestion → can approve (use as-is), edit (modify suggestion), or reject (flag for manual remediation) → approved items feed into recompilation
- **Acceptance Criteria**:
  - [ ] Dashboard displays document queue with status counts
  - [ ] Review panel shows original extracted element and AI suggestion side-by-side
  - [ ] Approve/Edit/Reject workflow updates HITLReviewItem records
  - [ ] Dashboard itself is keyboard-navigable and meets WCAG 2.1 AA
  - [ ] Batch approve available for SIMPLE-flagged items

### Module E: Recompilation (services/recompilation/)
- **Purpose**: Rebuild PDF/UA compliant documents from approved semantic HTML
- **Components**: PDF/UA builder, axe-core validator
- **Behavior**: Collects all approved HITLReviewItems for a document → assembles semantic HTML → validates with axe-core → generates PDF/UA output → stores in GCS
- **Acceptance Criteria**:
  - [ ] Output PDF passes Adobe Accessibility Checker
  - [ ] axe-core reports zero critical/serious violations
  - [ ] PDF/UA tag structure includes: headings, lists, tables with headers, alt text, reading order, language tag
  - [ ] Documents with remaining MANUAL items produce MANUAL_REVIEW_REQUIRED output

## Non-Functional Requirements

| Category | Target | How to Verify |
|----------|--------|---------------|
| Accessibility | WCAG 2.1 AA for output PDFs AND the HITL dashboard | axe-core audit, Adobe Accessibility Checker, manual keyboard navigation test |
| Performance | < 60s per PDF page for extraction + AI drafting | Timing logs in execution ledger |
| Performance | HITL dashboard < 3s page load | Lighthouse audit |
| Reliability | Retry on Adobe/Vertex API 5xx errors (3 retries, exponential backoff) | Error handling code review |
| Reliability | Dead-letter queue for failed documents after max retries | Pub/Sub DLQ configuration |
| Maintainability | Max 400 lines per file, consistent naming, typed interfaces | Lint rules |
| Security | API keys in environment variables only, no secrets in code | grep audit for hardcoded keys |
| Security | HITL dashboard behind authentication (OAuth2 or IAP) | Auth middleware review |
| Scalability | Pipeline handles 100 concurrent documents | Cloud Run autoscaling config |

## Evidence Pack

| Source | URL / Reference | Version | Notes |
|--------|----------------|---------|-------|
| Adobe Acrobat Services — Extract API | https://developer.adobe.com/document-services/docs/overview/pdf-extract-api/ | v4 | Returns JSON with element positions, text, tables |
| Adobe Acrobat Services — Auto-Tag API | https://developer.adobe.com/document-services/docs/overview/pdf-accessibility-auto-tag-api/ | v4 | Generates PDF tag structure |
| Adobe PDF Accessibility Checker | https://developer.adobe.com/document-services/docs/overview/pdf-accessibility-checker-api/ | v4 | Validates PDF/UA compliance |
| GCP Cloud Run Docs | https://cloud.google.com/run/docs | v2 (2nd gen) | Container-based serverless |
| GCP Pub/Sub Docs | https://cloud.google.com/pubsub/docs | — | Message queueing |
| Vertex AI — Gemini API | https://cloud.google.com/vertex-ai/docs/generative-ai/model-reference/gemini | 1.5 Pro | gemini-1.5-pro-002 |
| WCAG 2.1 Guidelines | https://www.w3.org/TR/WCAG21/ | 2.1 | AA conformance level |
| PDF/UA Specification | https://www.pdfa.org/resource/pdfua-in-a-nutshell/ | ISO 14289-1 | Tagged PDF standard |
| axe-core | https://github.com/dequelabs/axe-core | 4.8+ | Accessibility testing engine |

## Assumptions Register

| ID | Assumption | Risk if Wrong | Verification Method | Verified? |
|----|-----------|---------------|---------------------|-----------|
| A1 | Input PDFs have extractable text (not scanned images) | OCR pipeline needed — major scope increase | Test with sample county PDFs | Pending |
| A2 | Adobe Extract API handles complex multi-column layouts | Manual parsing needed for complex layouts | Test with sample county PDFs | Pending |
| A3 | Adobe API rate limits sufficient for POC volume (~100 docs/day) | Throttling causes pipeline delays | Check Adobe API documentation | Pending |
| A4 | County IT can provision GCP project with required APIs enabled | Blocked on infra if not | Confirm with county IT contact | Pending |
| A5 | Gemini 1.5 Pro produces usable alt text from bounding box context | Alt text quality may require fine-tuning or few-shot examples | Manual review of 20+ samples | Pending |
| A6 | County staff can use a web-based HITL dashboard (no desktop app requirement) | Dashboard approach invalid | Confirm with county stakeholders | Pending |
| A7 | Authentication via GCP IAP is acceptable (no county SSO integration for POC) | Auth integration needed | Confirm with county IT | Pending |
| A8 | PDF/UA recompilation can be achieved via Adobe Acrobat Services (not just validation) | Need alternative PDF generation library | Verify Adobe API capabilities | Pending |
| A9 | Expected document volume for POC is < 1000 documents total | Scaling architecture needed earlier | Confirm with county | Pending |
| A10 | Nested tables (>2 levels) are rare in county documents | High MANUAL flag rate reduces automation value | Sample analysis | Pending |

## Known Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Adobe API changes or deprecation during POC | Medium | Pin API version, monitor changelog |
| Gemini alt text quality insufficient for county standards | High | Include human review for all images in first batch; calibrate prompts |
| Complex PDF layouts cause extraction failures | High | Dead-letter queue + manual remediation path |
| Context window limits during long Phase 4 implementation | Medium | JSONL ledger + Emergency Compaction Protocol |
| County IT delays on GCP provisioning | Medium | Develop locally with emulators first |

## Acceptance Criteria Checklist

- [ ] End-to-end: Upload PDF → Extract → AI Draft → HITL Review → PDF/UA output
- [ ] Output PDFs pass Adobe Accessibility Checker with zero critical issues
- [ ] Output PDFs pass axe-core with zero critical/serious violations
- [ ] HITL dashboard is keyboard-navigable and meets WCAG 2.1 AA itself
- [ ] All images have alt text (WCAG 1.1.1)
- [ ] All tables have proper header associations (WCAG 1.3.1)
- [ ] Reading order is correct in output PDFs (WCAG 1.3.2)
- [ ] Language tag set on output PDFs (WCAG 3.1.1)
- [ ] Failed documents route to dead-letter queue with error details
- [ ] No API keys or secrets in codebase (env vars only)
- [ ] Pipeline handles 10 concurrent documents without failure

---

## Project-Specific Adaptations

### Unified Execution Ledger (replaces standard Work Log for Phase 4)

For this project, `wcag_execution_ledger.jsonl` replaces the standard Work Log AND serves as the compaction recovery source. After every successful sub-task, append a structured JSON entry:

```json
{
  "timestamp": "2026-02-23T14:30:00Z",
  "task_id": "A.1",
  "task_name": "Terraform GCP resources",
  "action_taken": "Created main.tf with Cloud Run, Pub/Sub, GCS resources",
  "verification": "terraform plan: SUCCESS (3 resources to create)",
  "pending_blockers": [],
  "lossless_pointers": {
    "main_tf": "infra/terraform/main.tf",
    "variables_tf": "infra/terraform/variables.tf"
  },
  "status": "DONE"
}
```

### Post-Compaction Recovery

After any compaction event, the FIRST action is:
1. Read the last 20 lines of `wcag_execution_ledger.jsonl`
2. Reconstruct active state from ledger entries
3. Resume from the last logged task

### Phase 5 Triggers

When Module E (Recompilation) is complete, automatically trigger Phase 5:
- **Layer 1 (Compliance)**: Cross-reference every acceptance criterion against implementation
- **Layer 2a (Quality)**: Lint, typecheck, no TODOs/placeholders
- **Layer 2b (NFR)**: Run axe-core on output PDFs + HITL dashboard; verify performance targets
- **Layer 3 (Accuracy)**: Verify Adobe API usage against pinned Evidence Pack docs
- **MANUAL_REVIEW_REQUIRED**: Generate queue for complex nested tables that axe-core cannot fully validate

### API Setup (Execute Before Any Code)

Before writing pipeline code, the user must provide:
1. **GCP Service Account JSON** — Generate at: GCP Console → IAM → Service Accounts → Create Key
2. **Adobe Acrobat Services API credentials** — Generate at: https://acrobatservices.adobe.com/dc-integration-creation-app-cdn/main.html

Set as environment variables:
```
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
ADOBE_CLIENT_ID=<your-client-id>
ADOBE_CLIENT_SECRET=<your-client-secret>
```

---

## Deployment Checklist

**CRITICAL: Follow this checklist for EVERY deployment. Skipping steps has caused production outages.**

### Backend (Cloud Run)

**Required env vars** — these MUST be present on every Cloud Run revision:
```
WCAG_ADOBE_CLIENT_ID=<adobe-client-id>
WCAG_ADOBE_CLIENT_SECRET=<adobe-client-secret>
WCAG_GCP_PROJECT_ID=report-conciliation-487916
WCAG_GCP_REGION=us-central1
WCAG_VERTEX_AI_MODEL=gemini-2.5-pro
WCAG_VERTEX_AI_LOCATION=us-central1
PYTHONPATH=/app
WCAG_DB_PATH=/data/wcag_pipeline.db
WCAG_EXTRACTION_CACHE_DIR=/data/.extract_cache
PYTHONUNBUFFERED=1
```

**Deploy command** — ALWAYS use `--update-env-vars` (merges), NEVER `--set-env-vars` (replaces all):
```bash
# CORRECT — preserves existing env vars:
gcloud run deploy sacto-wcag-api \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --timeout 300 \
  --update-env-vars="PYTHONUNBUFFERED=1"

# WRONG — wipes all other env vars:
# gcloud run deploy ... --set-env-vars="PYTHONUNBUFFERED=1"
```

**Post-deploy verification — MANDATORY, NO EXCEPTIONS:**
```bash
# Default run (cheap — 1-page canary PDF, ~2 server-side API calls):
python scripts/verify_deploy.py --revision <REVISION>

# ALL checks must pass. Do NOT claim deployment is successful until this outputs:
# VERDICT: ALL CHECKS PASSED (51/51 passed)

# Full run (adds image PDF for Vertex AI e2e):
python scripts/verify_deploy.py --revision <REVISION> --with-image-pdf

# Cheap run (health + env + CORS only — zero external API cost):
python scripts/verify_deploy.py --skip-paid-apis
```

The script tests 8 categories (51+ checks, cost-capped at 5 external API calls):
1. **Health** — all 5 dependency probes return expected status
2. **Env vars** — all 10 required env vars present on the revision
3. **Analyze contract** — 200 response, task_id, 50 rules, pipeline_metadata with 3 stages, work metrics
4. **Remediate contract** — 200 response, X-Task-Id, X-Pipeline-Metadata, X-Remediation-Delta, lang="en", title, skip-link
5. **Vertex AI e2e** — (optional) real image PDF → verify AI actually generates alt text
6. **CORS headers** — all 4 custom headers exposed (X-Task-Id, X-Pipeline-Version, X-Pipeline-Metadata, X-Remediation-Delta)
7. **Silent fallback detection** — Vertex AI probe reports auth method, no silent passes
8. **Gate semantic truth** — stages with status "success" have evidence of actual work; degraded/skipped stages have explanations

**Legacy manual checks** (only if the script is unavailable):
```bash
gcloud run revisions describe <REVISION> --region us-central1 \
  --format='yaml(spec.containers[0].env[].name)'
curl https://sacto-wcag-api-738802459862.us-central1.run.app/api/health
```

### Frontend (Vercel)

**Required env var** (set in Vercel project settings):
```
NEXT_PUBLIC_API_URL=https://sacto-wcag-api-738802459862.us-central1.run.app
```

**Deploy command**:
```bash
cd hitl-dashboard
npx vercel --prod --force  # --force bypasses build cache
```

**Post-deploy verification**:
```bash
# Check production alias is updated
npx vercel ls --prod
# Visit https://hitl-dashboard.vercel.app/upload and verify UI loads
```

### Common Pitfalls (learned from incidents)

| Pitfall | Impact | Prevention |
|---------|--------|------------|
| `--set-env-vars` instead of `--update-env-vars` | Wipes all credentials → 500 on every API call | Always use `--update-env-vars` for incremental changes |
| Vercel build cache serves old code | UI shows stale components | Use `--force` flag on deploy |
| Missing `PYTHONPATH=/app` | Module imports fail in container | Include in env vars; verify after deploy |
| `PYTHONPATH` set to local Windows path | Imports break in Linux container | Must be `/app`, not `C:/Program Files/...` |
| Credential check mismatch (availability vs usage) | Tool reports "available" but silently falls back per-call | Both the availability check AND the per-call function must use the same credential sources (ADC, K_SERVICE, GOOGLE_APPLICATION_CREDENTIALS) |
| Silent fallback returns | Function returns fallback value instead of raising → pipeline reports "success" for zero work | Use `StageNoOpError` in pipeline stages; check work metrics (ai_succeeded > 0) not just status |
| Unit tests pass but production fails | Mocking dependencies hides integration bugs | Run `scripts/verify_deploy.py` after every deploy — tests real HTTP endpoints |
| Test PDFs without extractable text | Scanned-PDF guard rejects blank test PDFs → 422 | Use reportlab to generate PDFs with actual text content |
| Redundant imports inside try blocks | Python scoping: `from X import Y` inside a block shadows module-level import | Never re-import at module level; use the existing import |
| Gate fail-open: validation tool unavailable = PASS | axe-core/Adobe/VeraPDF unavailable → gate reports pass → false compliance claim | All gate unavailability returns soft_fail/P1/flag_hitl, never P2/proceed |
| `except Exception: pass` on validation | VeraPDF error during fix acceptance → fix silently accepted without validation | Reject the fix when validation fails; unvalidated changes are worse than no changes |
| Regression gate silently skipped | VeraPDF unavailable → regression gate skipped → no log → output compliance unverified | Log WARNING + set span attribute when regression gate is skipped |
| CORS only on actual responses | CORS `Access-Control-Expose-Headers` appears on real responses, NOT OPTIONS preflight | Test with GET+Origin header, not OPTIONS |

### Mandatory Post-Change Process

**EVERY code change must follow this sequence. No exceptions.**

1. `python -m pytest tests/ -x -q` — all tests pass (currently 625+)
2. Deploy: `gcloud run deploy ...` with `--update-env-vars`
3. `python scripts/verify_deploy.py --revision <REVISION>` — 51/51 pass
4. Update `.claude/session-handoff.md` with: revision, changes, verification evidence
5. Only THEN state "deployment successful"

**A change is NOT verified until `verify_deploy.py` passes.** Unit tests alone are insufficient.

### Session Handoff (Compaction Recovery)

After every deploy, update `.claude/session-handoff.md` with:
- Cloud Run service + revision + region
- Git commit SHA
- Env var state (verified via verify_deploy.py)
- Changes made and why
- Known invariants (rules that must NEVER be violated)
- Backlog progress
- Verification evidence (paste verify_deploy.py result)
- Next steps in priority order

On session start (or after compaction), read `.claude/session-handoff.md` + `scripts/verify_deploy_report.json` to resume.
