# Retrieval baseline v1

This is a separate local retrieval experiment. It never invokes the held story-evaluation runner, a model or Pinecone.

Implemented: the existing keyword scoring/tokenization applied to a fixed, owner-reviewed Stage 4 chunk corpus; source/version/permission checks; explicit relevance labels; saved inputs, traces, scores and failure cases.

Follow-up Stage 6 implements local BM25, BGE dense search, RRF and FlashRank plus an opt-in Pinecone adapter tested offline. See [matched comparison](experiments/hybrid-local-003/comparison.json) and [verification](../../docs/stage6-verification.md). Live Pinecone operation and a human-reviewed Ramayan benchmark remain pending. This page's commands below describe the original keyword baseline.

## Actual saved experiment

[keyword-synthetic-001](experiments/keyword-synthetic-001/summary.json) contains 10 program-authored engineering cases: 7 PASS, 2 FAIL, 1 PENDING. All source text is explicitly fictional. It is not a religious reference corpus or human-labeled cultural evaluation.

Five completed relevance cases produced three top-five hits. Four negative/policy cases passed. The pending-label case is excluded from metric denominators. The older-wording and supplied name-variant cases failed and remain saved as failures. No improvement claim is made.

## Run a new synthetic experiment

```powershell
uv run python scripts/run_retrieval_baseline.py --synthetic --workspace .local/retrieval-baseline-fixture-v1 --output evaluation/retrieval_v1/experiments/keyword-synthetic-002
```

Use a new output directory each time. Existing experiment files cannot be overwritten. The synthetic setup reviews only its own program-authored fixtures in a separate workspace; it does not approve Ramayan content. Reusing the fixture workspace can create new synthetic review records/epochs, which the report records.

## Prepare real labels

Start with a small selected subset, not all 1,853 candidate passages:

1. In the source workspace, check the edition, original page, extracted text and section boundary for each candidate passage.
2. Record content suitability and rights decisions. Parent review is inspiration-only, not approval for traditional claims. External transmission is a separate permission and is not needed for this local baseline.
3. Write a realistic search question and record its acceptable matching chunk IDs, exact source/edition/snapshot and generation. Record who reviewed the answer and a reference to that review. Include irrelevant, withdrawn and mismatched-edition cases.
4. Keep unresolved labels `pending`. Do not label an AI-suggested answer as human-reviewed without a person checking it.
5. Freeze the corpus and use the same questions, expected IDs, chunking generation and permissions in later retrieval comparisons. Keep development and held-out questions separate.

The operator supplies the owner from the trusted local source registry; do not copy or share browser session cookies. The CLI is a local operator tool, not production authentication.

Example case shape, **not an executed real query**:

```json
{
  "test_id": "REAL-001",
  "query": "A question checked by the reviewer",
  "source": {
    "source_id": "actual-source-id",
    "edition_id": "actual-edition-id",
    "snapshot_id": "actual-snapshot-id",
    "generation": 2
  },
  "purpose": "parent_inspiration",
  "expected_chunk_ids": ["actual-reviewed-chunk-id"],
  "expectation": "relevant",
  "label_status": "human_reviewed",
  "label_reference": "A real review record or signed-off annotation reference"
}
```

Store cases as a JSON list. For an explicit negative case use `no_match` or `blocked` and an empty expected-ID list. A human-reviewed label flag is supplied by the experiment author; it is not proof of authenticated expert review. A qualified registry is still required for traditional evidence.

```powershell
uv run python scripts/run_retrieval_baseline.py --workspace .local/source-workspace --owner ACTUAL_REGISTRY_OWNER --cases PATH_TO_REVIEWED_CASES.json --scope human_reviewed --output evaluation/retrieval_v1/experiments/keyword-real-001
```

## Metrics and artifacts

- `inputs.json`: exact requests and expected IDs; hashed in the summary.
- `traces.json`: actual query, eligible/excluded counts, returned IDs/text/pages, scores, corpus hash and latency.
- `results.json`: PASS/FAIL/PENDING and per-case measures.
- `summary.json`: scope, denominators, mean recall@K, mean reciprocal rank, hit rate and negative-case counts.

A relevance case passes when at least one expected acceptable passage is returned. Recall separately measures the proportion of listed target passages retrieved; `all_expected_found` is reported independently. Negative/policy cases are reported separately from relevance metrics. Unavailable target evidence, stale scope or incomplete labels are PENDING. A change in source generation or eligibility epoch during an experiment invalidates every affected scope rather than combining incompatible results.

The method suffix `fixed-workspace-chunks` matters: scoring and tokenization are reused from the existing live keyword implementation, but this adapter searches Stage 4 chunks. It is not a timing/quality benchmark of the legacy endpoint's original splitter, provider calls or complete story workflow. There are no live endpoint changes.
