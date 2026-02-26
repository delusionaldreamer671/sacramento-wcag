# API Key Rotation Checklist

Run this checklist every time an API key or credential is changed. Skipping steps has caused production outages.

---

## Adobe PDF Services API (WCAG_ADOBE_CLIENT_ID / WCAG_ADOBE_CLIENT_SECRET)

### 1. Obtain New Credentials
- [ ] Go to https://acrobatservices.adobe.com/dc-integration-creation-app-cdn/main.html
- [ ] Select **PDF Services API** (NOT PDF Embed API)
- [ ] Download the OAuth Server-to-Server JSON file
- [ ] Extract `CLIENT_ID` and `CLIENT_SECRETS[0]` from the JSON

### 2. Update Cloud Run (Production)
- [ ] Run the update command (**ALWAYS use `--update-env-vars`, NEVER `--set-env-vars`**):
  ```bash
  gcloud run services update sacto-wcag-api \
    --region us-central1 \
    --update-env-vars="WCAG_ADOBE_CLIENT_ID=<new-client-id>,WCAG_ADOBE_CLIENT_SECRET=<new-secret>"
  ```
- [ ] Verify all 10 env vars survived (none wiped):
  ```bash
  gcloud run revisions describe <REVISION> --region us-central1 \
    --format='yaml(spec.containers[0].env[].name)'
  ```
  Must show: PYTHONUNBUFFERED, WCAG_ADOBE_CLIENT_ID, WCAG_ADOBE_CLIENT_SECRET, WCAG_GCP_PROJECT_ID, WCAG_GCP_REGION, WCAG_VERTEX_AI_MODEL, WCAG_VERTEX_AI_LOCATION, PYTHONPATH, WCAG_DB_PATH, WCAG_EXTRACTION_CACHE_DIR

### 3. Verify Production
- [ ] Health check:
  ```bash
  curl https://sacto-wcag-api-738802459862.us-central1.run.app/api/health
  ```
- [ ] Smoke test (upload a PDF and verify analysis completes):
  ```bash
  curl -X POST ".../api/analyze" -F "file=@test.pdf"
  ```
  Must return HTTP 200 with `rules_checked: 50`
- [ ] Test via frontend: visit https://hitl-dashboard.vercel.app/upload, upload a PDF, confirm analyze + remediate both work

### 4. Update CI/CD Secrets (if using GitHub Actions)
- [ ] Update `ADOBE_CLIENT_ID` in GitHub repo Settings > Secrets
- [ ] Update `ADOBE_CLIENT_SECRET` in GitHub repo Settings > Secrets
- [ ] Trigger a test workflow run to verify

### 5. Update Local Development (if applicable)
- [ ] Update `.env` file in project root:
  ```
  WCAG_ADOBE_CLIENT_ID=<new-client-id>
  WCAG_ADOBE_CLIENT_SECRET=<new-secret>
  ```
- [ ] Run local smoke test: `python -c "from services.common.config import settings; print(settings.adobe_client_id[:8])"`

### 6. Secure the Old Credentials
- [ ] Delete or archive the old credentials JSON file
- [ ] Revoke the old credentials in Adobe Developer Console (if possible)
- [ ] Move the new credentials JSON from Downloads to a secure location (password manager or private vault folder) — it contains ORG_ID and TECHNICAL_ACCOUNT_ID needed for future reference

---

## Vertex AI / GCP Credentials (WCAG_GCP_PROJECT_ID, WCAG_VERTEX_AI_MODEL)

### 1. Update Cloud Run
- [ ] Same `--update-env-vars` pattern as above
- [ ] Verify all 10 env vars survived

### 2. Verify
- [ ] Health check passes
- [ ] Upload a PDF with images — verify AI alt-text generation works (check remediation report for "AltText" events)

### 3. Update CI/CD
- [ ] Update relevant GitHub Secrets
- [ ] For service account key rotation: update `GOOGLE_APPLICATION_CREDENTIALS` or workload identity

---

## General Rules (Apply to ALL Key Rotations)

1. **NEVER use `--set-env-vars`** — it wipes all other env vars. ALWAYS use `--update-env-vars`.
2. **Always verify all 10 env vars survived** after any Cloud Run update.
3. **Always run a full smoke test** (health check + PDF analyze + PDF remediate) after credential changes.
4. **Never commit credentials to git** — they live only in Cloud Run env vars, GitHub Secrets, and local `.env` (which is in `.gitignore`).
5. **Delete credential JSON files from Downloads** after extracting the values.
6. **Document the rotation** — note the date and reason (quota exhaustion, expiry, security rotation) in the project log.

---

## Quick Reference: Where Credentials Are Used

| Location | File | Purpose |
|----------|------|---------|
| Cloud Run env vars | (runtime) | Production backend reads via `WCAG_` prefix |
| `services/common/config.py:36-37` | Code | `Settings.adobe_client_id` / `adobe_client_secret` |
| `services/extraction/adobe_client.py` | Code | Creates Adobe API auth token |
| `services/extraction/adobe_checker.py` | Code | Adobe Accessibility Checker |
| `.github/workflows/ci.yml:81-82` | CI/CD | Maps GitHub Secrets to Cloud Run env vars |
| `infra/cloudbuild.yaml:44-46` | CI/CD | Cloud Build secret references |
| `infra/terraform/main.tf:526-532` | IaC | Terraform placeholder for deploy-time injection |
| Local `.env` file | Dev | Local development (not committed) |
