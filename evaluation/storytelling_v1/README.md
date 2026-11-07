# Storytelling content evaluation

User-authorized on November 1, 2026. This is a **new** ten-case dataset. Previously held evaluation files are not read or relabeled. Illustrations, narration, payments and other workflow checks remain separate.

The current deliverables are in [artifacts](artifacts/). Open [Little_Stories_Evaluation.xlsx](artifacts/Little_Stories_Evaluation.xlsx) and [evaluation_summary.md](artifacts/evaluation_summary.md). All unexecuted content scores are PENDING, and human feedback is blank until a person reviews the story. No real model improvement is claimed.

## Connection and execution

The local configuration is a **file**, `.local/.env`, already created with empty secret fields and excluded by `.gitignore`. Put your own key after `NEBIUS_API_KEY=`. Never share that file, key or a screenshot of it. Both the local server (after restarting) and evaluation runner load that file; process environment variables take precedence. This task does not claim that live generation is connected before a valid key is provided.

```powershell
# Only needed if the workbook dependency is missing:
python -m pip install -r evaluation/storytelling_v1/requirements.txt

# Create/validate immutable inputs, prompt snapshots and workbook without model calls:
python -m evaluation.storytelling_v1.runner prepare

# After configuring Nebius: real generation and separate Reviewer calls.
python -m evaluation.storytelling_v1.runner run
```

The original inputs use exactly `Test_ID,User_Input,Model_Output`, contain ten distinct requests, and keep Model_Output blank permanently. Generated outputs go into a separate CSV and per-run artifacts. Resume skips previously executed IDs, preserving failures; a missing-key preflight creates no fake execution IDs. To start a genuinely new experiment, use `--folder` with a new destination; never delete failed outputs to make a pass rate look better.

Each supported case costs one writer call and one judge call. Up to 20 calls are possible for the baseline; unsupported inputs stop at the real application validator before a model call. Failed requests can still incur provider charges if a response was generated. Reruns are additional calls; there is no automatic retry, purchased service, or claim that mock weekly payments cover these costs. No dollar estimate is measured yet.

## Application integration and actual limits

The runner invokes the same `make_original_book(request, 'generate')` or `make_book(request, 'generate')` function used by the application's HTTP endpoints, then calls the same `reviewer_agent.run_review` with its own independent context. It does not substitute prewritten examples, rewrite outputs, bypass character validation, create production approvals or modify the local parent bookshelf.

The evaluation-only review context extends the existing Reviewer/Playbook with the frozen content rubric, complete parent request and a separate `critical_safety` rule. It cannot grant parent approval. Source snippets are never promoted from pending to approved. Writer, judge, rubric, Playbook, catalog and generation source versions are preserved and sealed before runs; changes require a new experiment folder. A provider model alias is recorded along with the returned model identifier, but provider-side weight/version stability cannot be guaranteed.

The architecture in the brief is partly planned:

| Requested role | Actual implementation |
| --- | --- |
| Vyasa / Story Writer | Direct Python generation functions calling Nebius Qwen |
| Dharma / Independent Reviewer | Separate existing Reviewer invocation with evidence pointers |
| Saraswati / Cultural Knowledge | Pending-review festival catalog notes; no live retrieval agent |
| Sutradhar / Supervisor | Explicit Python routing and validation; no live LangGraph supervisor |

Execution traces describe actual local Python/provider calls. Local IDs are labeled `local-eval-*`; provider response IDs are preserved when returned. `langgraph_run_id` is null because no graph executed. There is no fabricated LangSmith or LangGraph telemetry.

The unchanged app currently accepts only three sacred characters and four original-story objectives. Rama, Saraswati and some requested objectives are unsupported. Its original writer hardcodes Veyd, while two requests require Mira. Its festival endpoint does not forward arbitrary free-form story instructions. These are **inspection findings**, not model scores; the tests deliberately expose them rather than changing the requested cast/value. A code capability gap may require a code fix and a new experiment, not a writing-prompt change.

## Scoring and human review

Each dimension is independent. PASS requires all five dimensions plus Critical_Safety to explicitly PASS. Any FAIL produces FAIL, including a safety failure. Otherwise incomplete/blank/invalid evidence produces PENDING. The Excel formula uses an exact count of six PASS cells; blanks never become PASS. Critical safety is separately checked by the judge and existing application word checks; neither is human safety certification.

Word limits count the entire displayed story, including title, six pages, moral, reflection, cultural facts and displayed contextual sections. Technical metadata is excluded. This is stricter than the current writer's page-only duration target and is deliberately disclosed to the judge.

The workbook has the exact three requested sheets: Evaluation Log, Failure Analysis and Failure Traces, with formulas, dropdowns, filters, wrapping, frozen headers and PASS/FAIL/PENDING colors. Overall formulas recalculate in Excel; openpyxl does not compute their cached values. Programmatic metrics are also saved to `metrics.json`. Full outputs remain in CSV/JSON if Excel's cell length limit requires a clearly labeled display truncation.

Inspect the generated story, Reviewer findings and trace, then fill `artifacts/human_reviews.json`. For each reviewed ID, enter `reviewer`, `feedback`, all six `dimension_results`, feedback for every failed dimension, and `critical_safety_feedback` if safety fails; finally set `status` to `COMPLETED`. Revised IDs need their own entries. No reviewer identity is supplied by the software.

```powershell
python -m evaluation.storytelling_v1.runner report
```

This imports human findings into the workbook without changing original model responses or automated reports. Human failures block the combined result; human approval cannot erase a saved automated failure or resolve absent cultural evidence. Edit the JSON for durable feedback: regenerating the workbook replaces direct spreadsheet edits. Automated findings are labeled separately and are never entered as human feedback. Empty human review remains separately pending even if automated scores pass.

## One-instruction improvement cycle

After actual failed outputs and reviews exist, inspect their saved traces and choose a recurring prompt-addressable primary category. Do not infer the cause from a score alone. Every failed story has exactly one primary category; secondary issues remain in detailed notes. Execution failures are also preserved in Failure Traces but PENDING content rows are excluded from story-failure totals.

```powershell
python -m evaluation.storytelling_v1.runner revise --pattern "Low Informational Value" --instruction "<one evidence-based instruction>" --why "<observed pattern, trace evidence and expected improvement>"
python -m evaluation.storytelling_v1.runner rerun
python -m evaluation.storytelling_v1.runner report
```

The pattern above is an example command, not a chosen fix. `revise` requires at least two saved failed stories with the selected primary pattern within one writer path. It appends exactly one bounded instruction to that path, saves the exact original/revised prompts and rationale, and refuses to overwrite an existing revision. The override applies inside the real generation call for this experiment; it does not silently promote an unproven prompt to the product. No other generation settings or evaluation conditions are intentionally changed. Sampling remains stochastic, so one improvement is evidence for that pair, not a causal or statistical guarantee.

Reruns use every saved failed request plus at least one passing control, with unchanged input/facts, `-R1` output IDs and original IDs linked. Every check is reevaluated from scratch. Matched comparisons report FAIL-to-PASS, PASS-to-FAIL, unresolved/retained results and per-dimension changes; they never compare a subset's rate to all ten as equivalent populations. Prompt-fix/regression metrics are null when their denominator is absent. No fix is confirmed without an exact saved FAIL-to-PASS pair.

If all ten pass, do not manufacture a failure: a separately documented, genuinely saved worked failure is needed before this guarded revision command can proceed. If no passing control exists, the cycle also remains pending. These conditions are surfaced rather than silently bypassed.

## Validation

```powershell
python -m unittest discover -s tests -p test_story_evaluation.py
python -m unittest discover -s tests
```

The harness uses explicitly synthetic fixtures in temporary directories and mocks network calls. Those test assertions are engineering checks, never measured storytelling performance. Verification on November 1: 14 evaluation-contract tests passed; the full suite ran 57 tests, with 56 passing and one optional real-PDF test skipped. Artifact inspection confirmed ten inputs, ten PENDING output rows, the exact three workbook sheets, blank execution IDs and zero model calls. Current model execution, human review, prompt diagnosis, one-instruction revision and matched live reruns remain pending until access and actual results exist.
