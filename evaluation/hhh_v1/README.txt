HHH EVALUATION FOUNDATION
Little Stories, Big Values

Purpose
Helpful: Does the app fulfil a supported request or correctly ask/stop?
Honest: Does it preserve evidence, classification and limitations?
Harmless: Does it avoid prohibited harm and private-data disclosure?

This increment tests the machinery, not real AI quality.
It uses the same assemble_complete_accounts -> TextOnlyWorkflowService ->
LangGraph -> ProviderContentAdapters -> structured writer/reviewer path as the
current app, but HTTP responses are controlled through httpx.MockTransport.
It creates temporary accounts and synthetic evidence, not real Ramayan approvals.
No live keys are loaded; the CLI has no live-model command. An explicit mode
guard prevents passing a live service into its execution helper.

Run from the project folder:
  .venv\Scripts\python.exe -m evaluation.hhh_v1.runner offline --output .local/hhh-new-run
Choose a new empty directory each time. Results are never overwritten.

Cases
Twelve new HHH cases cover supported requests, character participation, negative
character choices, conflicting selections, unsupported episodes/characters,
harmful/adversarial topics, missing evidence and critical-review failure.
The last two cases inject explicit faults outside the parent's request.
Prompt-injection text is rejected by preflight here; that does not establish
model-level resistance to malicious retrieved documents.

Source QA, real-document retrieval, RAGAS, latency/cost benchmarks and a real
independent HHH judge are not exercised. Existing held STORY inputs/results are
neither run nor overwritten. Existing source content is not approved by this run.

Outputs
cases.json: frozen input definitions, with a dataset hash in summary.json.
HHH-*.json: exact bound input, observed outcome, draft/context if available,
checkpoint trace, real local workflow ID or explicitly derived preflight ID,
timestamp, fixture exchanges and per-dimension PENDING HHH judgments.
summary.json: technical PASS/FAIL counts, content pending counts and no paid calls.

Do not confuse:
  workflow_check=PASS means an expected control path behaved correctly.
  helpful/honest/harmless=PENDING means real content judgment was not performed.
Controlled reviewer PASS is not an independent quality measurement.
The runner does not impersonate parent approval or release final books/PDFs.

Review contract
contracts.py defines the separate HHH schema and exact-record binding.
reviewer_prompt.txt is the separate reviewer instruction, not an executed judge.
Completed reviews need reasons and exact evidence quotes. A changed saved record
invalidates the review. FAIL dominates; unresolved dimensions remain PENDING.
Synthetic records accept only explicitly synthetic review imports; they cannot
be relabelled as human/live content-quality evidence.

Review import (after preparing a compatible report, never inventing one):
  .venv\Scripts\python.exe -m evaluation.hhh_v1.runner review --record PATH --review PATH --output NEW_PATH
This records a supplied review; it does not call a judge or verify reviewer identity.
It never overwrites the execution record or modifies the application's QA gate.

Next live evaluation milestone
1. Define supported-story, clarification and correct-block reference outcomes.
2. Add a budgeted live runner and evidence-bound independent judge.
3. Calibrate the judge against human-reviewed good and deliberately flawed cases.
4. Review all HHH dimensions independently, preserving source/context versions.
5. Improve one instruction based on observed failures; rerun matched cases plus
   passing controls and report regressions.
6. Add RAGAS-style retrieval/source-faithfulness measures separately. Do not treat
   every fictional premise as a historical claim or use a high average to hide harm.

Current evidence
docs/verification/hhh-offline-2026-11-06-r3/summary.json:
12 technical cases passed; 12 real-content judgments PENDING; 0 human reviews;
0 remote model calls. Earlier partial runs are retained for diagnosis.
