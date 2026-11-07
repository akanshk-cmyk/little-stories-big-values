> Current-runtime authority: [current release register](current-release-status.md), updated 6 November 2026. This document retains its stage-specific design/history; integrations and test counts below apply to that recorded scope, not automatically to the running app.

# Real Ramayan local-PDF/Groq text-only test

November 5, 2026. User explicitly authorized local PDF retrieval and a clearly labeled text-only PDF for the first real test. This is a limited live test, not a full hosted/illustrated release.

## Open the actual test app

http://localhost:8003/index.html#library

Sign in using the existing parent account (same account store as port 8002). If needed use http://localhost:8003/account.html?from=main . Add a real child profile through My account if none exists. Then open My Story Studio and select Rama, Adapted retelling, Kindness or Sharing, three minutes, English, and the matching child age. The source is already loaded; do not upload the 1,960-page PDF again.

Actual source scope: uploaded Ramayan PDF pages 549-551, the cited welcome/horse-care passage of Canto L. This limited corpus is not whole-book semantic search. Source is read from the previously extracted local PDF, the original file hash is checked, and exact PDF/printed page references are retained. Edition/translator metadata is unverified and labeled as such. No LlamaCloud search, images, public-source license or qualified human review is claimed.

## Real execution findings

- Groq source QA ran on the actual passage and returned supported PASS checks; the report is saved privately under .local/ramayan-live-test/source-qa-report.json. It is reused only after exact quote/hash/version validation. Its origin is automated_qa.
- A UTF-8 cache-reading defect initially blocked evidence because accented source names were decoded with the Windows default. Corrected the explicit UTF-8 read; no source report was rewritten.
- Actual Groq writing initially failed citation validation because claim_text was not copied exactly from its page. Added an explicit citation instruction and bumped writer prompt v2; an actual six-page draft then passed structural/source-citation validation. Original provider responses/operation records are preserved; generated story text was not manually edited.
- Independent reviewer calls encountered rate limits, a truncated response and unsupported PASS records without evidence arrays. These were blocked, not converted into PASS. Configured the independently available openai/gpt-oss-20b reviewer model on Groq and bounded token allowances, then strengthened reviewer prompt v2 to require evidence for every PASS. No end-to-end live QA PASS or parent approval/PDF release is claimed at this point.
- A continuation reused the exact saved actual writer output after verifying its provider receipt, with explicit provenance. Its failed review is preserved under docs/verification/real-ramayan-review-continuation.json. No failed draft is exposed as parent-ready.

## What was implemented

- backend/accounts/text_only.py: explicit user-authorized text-only v5 adapters, empty asset manifest with no fake image approvals, escaped text/source-note rendering, and limited-request validation.
- backend/workflows/story_graph.py: trusted text-only policy bypasses image requirements only for that explicit mode; illustrated mode remains unchanged.
- backend/local_ramayan_test.py: private-use source scope, original-file validation, cached automated source QA, exact evidence binding, actual Groq router, real accounts/credit guards and PDF-only assembly.
- backend/ramayan_live_server.py: separate loopback app on port 8003, same parent account store, no replacement of old servers.
- prototype-v2/character-selection.js: Rama text selection without inventing a supplied portrait.
- prototype-v2/managed-story-ui.js and account capabilities: clear limited-test label/defaults, Groq request routing, no parent source-approval form, no narration controls.
- Provider/model/token-limit configuration and private validation diagnostics. Diagnostics failures cannot weaken validation or turn a known rejection into an unknown paid outcome.

Source QA result, actual writer output and all live failed/review attempts are preserved under .local/ramayan-live-test and docs/verification/real-ramayan-*.json. Do not manually edit those model artifacts. No parent approval is fabricated. Automated smoke attempts are bounded; the interactive app makes model calls only on explicit story submissions under account operation/credit limits.

## Verification

86 focused contract/provider/content/v5/text-only tests passed, no failures/errors. Actual live app health: HTTP 200, accountsEnabled=true, managedWorkflowEnabled=true, singleCompleteReviewEnabled=true. Main page serves the sign-in link. Tests use fixtures; the separate actual Groq findings above describe live outcomes accurately.

## User flow and expected gates

Create Story invokes actual Groq writing and independent draft QA. All five dimensions and critical safety must PASS before Approve Book / Request Changes / Reject appear. Approve Book authorizes the exact text-only version and PDF download. Model rate/validation errors keep approval and PDF blocked and retain the request. Narration is disabled. Use only the supported limited criteria for this first test; other characters/episodes require extending the real eligible corpus rather than silently substituting.

Existing port 8002 remains the earlier account installation. The new actual limited test is on port 8003. Localhost is available only on this computer. The stored source's usage scope is the user's explicitly requested private testing; it is not a verified redistribution license.
