"""Permission-checked hosted hybrid retrieval; never automatically passes sufficiency.

Results expand exact matching chunks to their full reviewed parent passage. The
original reference is retained; hosted text-file page numbers are not PDF citations.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal

from pydantic import Field, model_validator

from backend.contracts.common import Contract, Digest, Identifier, Text, content_hash, text_hash
from backend.contracts.evidence import RankedPassage
from backend.contracts.hosted_retrieval import HostedSearchHit
from backend.workflows.checkpoints import run_lock


class HybridSearchOptions(Contract):
    top_k: int = Field(default=20, ge=1, le=50)
    max_passages: int = Field(default=5, ge=1, le=5)
    max_context_tokens: int = Field(default=4000, ge=1, le=16000)
    vector_weight: float = Field(default=0.5, ge=0, le=1)
    full_text_weight: float = Field(default=0.5, ge=0, le=1)
    rerank: bool = True

    @model_validator(mode='after')
    def balanced_weights(self):
        if abs(self.vector_weight + self.full_text_weight - 1) > 1e-9:
            raise ValueError('Hybrid weights must sum to one.')
        if self.max_passages > self.top_k:
            raise ValueError('Passage count cannot exceed candidate count.')
        return self


class HostedQuery(Contract):
    operation_id: Identifier
    publication_id: Digest
    story_request_hash: Digest
    query: Text = Field(max_length=2000)
    usage_authorization_id: Identifier
    options: HybridSearchOptions = Field(default_factory=HybridSearchOptions)


class MappedMatch(Contract):
    hit: HostedSearchHit
    local_passage_id: Identifier
    match_start: int = Field(ge=0)
    match_end: int = Field(ge=1)


class HostedSearchSelection(Contract):
    seal_field = 'selection_hash'
    search_id: Digest
    owner_id: Identifier
    publication_id: Digest
    story_request_hash: Digest
    query_hash: Digest
    retrieval_config_hash: Digest
    index_version: Identifier
    manifest_hash: Digest
    matches: tuple[MappedMatch, ...] = Field(default=(), max_length=50)
    passages: tuple[RankedPassage, ...] = Field(default=(), max_length=5)
    # Retrieval is not cultural/evidence sufficiency review. No PASS field is accepted.
    selection_hash: Digest | None = None
    sufficiency: Literal['PENDING'] = 'PENDING'


class SearchBlocked(RuntimeError):
    pass


class HostedSearches:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'hosted-searches.sqlite'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS searches '
                       '(id TEXT PRIMARY KEY,owner TEXT NOT NULL,operation TEXT NOT NULL,payload TEXT NOT NULL,'
                       'UNIQUE(owner,operation))')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, owner, key):
        with self.db() as db:
            row = db.execute('SELECT payload FROM searches WHERE id=? AND owner=?', (key, owner)).fetchone()
        if not row:
            raise PermissionError('Search is unavailable for this account.')
        return json.loads(row[0])

    def save(self, owner, key, record):
        with self.db() as db:
            if db.execute('UPDATE searches SET payload=? WHERE id=? AND owner=?',
                          (json.dumps(record, allow_nan=False), key, owner)).rowcount != 1:
                raise PermissionError('Search is unavailable for this account.')


class LlamaCloudSearch:
    def __init__(self, publisher, store, *, authorize=None, token_counter=None,
                 token_counter_version=None):
        self.publisher, self.store = publisher, store
        # Must check ownership of story request plus permission/budget to transmit query.
        self.authorize = authorize or (lambda owner, query, manifest, operation: False)
        self.token_counter, self.token_counter_version = token_counter, token_counter_version

    def _guard(self, owner, query, plan, operation):
        self.publisher.active(owner, query.publication_id)
        if self.authorize(owner, query, plan.manifest, operation) is not True:
            raise PermissionError('Search request, query transmission or usage is not authorized.')
        if not callable(self.token_counter) or not isinstance(self.token_counter_version, str) or not self.token_counter_version.strip():
            raise SearchBlocked('A versioned target-model token counter is required.')

    def _config_hash(self, query):
        return content_hash([query.options.model_dump(mode='json'), self.token_counter_version,
                             'hosted-hybrid-reviewed-parent-v1'])

    def _normalize(self, owner, query, active, plan, key, response):
        results = response.get('results')
        if not isinstance(results, list) or len(results) > query.options.top_k:
            raise SearchBlocked('Invalid hosted search result collection.')
        by_file = {remote: local for local, remote in active['parsed_files'].items()}
        if len(by_file) != len(active['parsed_files']):
            raise SearchBlocked('Ambiguous hosted file mapping.')
        parents = {p.reference.chunk_id: p for p in plan.manifest.passages}
        matches, selected, seen, chunks = [], [], set(), set()
        tokens = 0
        for rank, result in enumerate(results, 1):
            static, metadata = result.get('static_fields'), result.get('metadata')
            if not isinstance(static, dict) or not isinstance(metadata, dict):
                raise SearchBlocked('Hosted result provenance is missing.')
            remote = static.get('parsed_directory_file_id')
            if remote not in by_file:
                raise SearchBlocked('Search returned a file outside the eligible corpus.')
            local = by_file[remote]
            parent = parents[local]
            expected = self.publisher._metadata(query.publication_id, plan.manifest, parent)
            if any(metadata.get(k) != v for k, v in expected.items()):
                raise SearchBlocked('Search result source/version metadata changed.')
            text = result.get('content')
            if not isinstance(text, str) or not text.strip():
                raise SearchBlocked('Search result text is missing.')
            start = parent.text.find(text)
            if start < 0 or parent.text.find(text, start + 1) >= 0:
                raise SearchBlocked('Search text is changed or cannot be mapped uniquely.')
            end = start + len(text)
            chunk = (remote, start, end)
            if chunk in chunks:
                raise SearchBlocked('Duplicate hosted search chunk.')
            chunks.add(chunk)
            # Original pages come only from the trusted parent. Ignore hosted page coordinates.
            hit = HostedSearchHit(hosted_file_id=remote,
                hosted_chunk_id='chunk-' + content_hash(chunk), text=text,
                pdf_pages=parent.reference.pdf_pages, content_hash=text_hash(text),
                rank=rank, score=result.get('score'), rerank_score=result.get('rerank_score'))
            matches.append(MappedMatch(hit=hit, local_passage_id=local, match_start=start, match_end=end))
            if local in seen or len(selected) >= query.options.max_passages:
                continue
            count = self.token_counter(parent.text)
            if type(count) is not int or count <= 0:
                raise SearchBlocked('The token counter did not return an actual positive count.')
            if tokens + count > query.options.max_context_tokens:
                raise SearchBlocked('Whole reviewed passages exceed the configured context budget.')
            tokens += count
            seen.add(local)
            selected.append(RankedPassage(reference=parent.reference, text=parent.text,
                index_version=active['index_version'], fused_rank=rank,
                rerank_score=hit.rerank_score, token_count=count))
        return HostedSearchSelection(search_id=key, owner_id=owner, publication_id=query.publication_id,
            story_request_hash=query.story_request_hash, query_hash=text_hash(query.query),
            retrieval_config_hash=self._config_hash(query), index_version=active['index_version'],
            manifest_hash=plan.manifest.manifest_hash, matches=tuple(matches), passages=tuple(selected))

    def search(self, owner, query):
        if not isinstance(query, HostedQuery):
            raise TypeError('A validated hosted query is required.')
        _, plan = self.publisher._load(owner, query.publication_id)
        self._guard(owner, query, plan, 'search')
        key = content_hash([owner, query.model_dump(mode='json'), self._config_hash(query)])
        with self.store.db() as db:
            bound = db.execute('SELECT id FROM searches WHERE owner=? AND operation=?',
                               (owner, query.operation_id)).fetchone()
        if bound and bound[0] != key:
            raise SearchBlocked('Search operation ID was reused with different inputs.')
        with run_lock(self.store.folder, key):
            # Fresh validation also applies to cached selections.
            active = self.publisher.verify_active(owner, query.publication_id)
            self._guard(owner, query, plan, 'query')
            with self.store.db() as db:
                row = db.execute('SELECT payload FROM searches WHERE id=? AND owner=?', (key, owner)).fetchone()
            if row:
                record = json.loads(row[0])
                if record['state'] != 'completed':
                    raise SearchBlocked('Prior search outcome needs reconciliation; no automatic repeat query.')
                selection = HostedSearchSelection.model_validate_json(json.dumps(record['selection']))
                self.assert_current(owner, query, selection)
                return selection
            record = dict(state='sending', request_hash=content_hash(query), query_hash=text_hash(query.query),
                          publication_id=query.publication_id, selection=None, error_code=None)
            with self.store.db() as db:
                try:
                    db.execute('INSERT INTO searches VALUES(?,?,?,?)',
                               (key, owner, query.operation_id, json.dumps(record)))
                except sqlite3.IntegrityError:
                    raise SearchBlocked('Search operation is already bound to another request.') from None
            started = time.monotonic()
            try:
                payload = dict(index_id=active['index_id'], query=query.query, top_k=query.options.top_k,
                    num_candidates=query.options.top_k, vector_pipeline_weight=query.options.vector_weight,
                    full_text_pipeline_weight=query.options.full_text_weight,
                    rerank={'enabled': query.options.rerank, 'top_n': query.options.top_k},
                    static_filters={'parsed_directory_file_id': {'operator': 'in',
                        'value': sorted(active['parsed_files'].values())}},
                    custom_filters={k: {'operator': 'eq', 'value': v} for k, v in {
                        'publication_id': query.publication_id, 'snapshot_id': plan.manifest.snapshot_id,
                        'purpose': plan.manifest.purpose}.items()})
                response = self.publisher._request('POST', '/api/v1/retrieval/retrieve', json=payload)
                selection = self._normalize(owner, query, active, plan, key, response)
                # Reject mid-query version/review/pointer changes before exposing passages.
                after = self.publisher.verify_active(owner, query.publication_id)
                if after != active:
                    raise SearchBlocked('Publication changed during retrieval.')
                self._validate_selection(owner, query, selection)
                record.update(state='completed', selection=selection.model_dump(mode='json'))
                return selection
            except Exception:
                record.update(state='blocked', error_code='search_not_validated')
                raise SearchBlocked('Search could not be validated; no evidence was released.') from None
            finally:
                record['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
                self.store.save(owner, key, record)

    def _validate_selection(self, owner, query, selection):
        if not isinstance(selection, HostedSearchSelection):
            raise TypeError('A validated search selection is required.')
        _, plan = self.publisher._load(owner, query.publication_id)
        self._guard(owner, query, plan, 'use_selection')
        active = self.publisher.active(owner, query.publication_id)
        expected_key = content_hash([owner, query.model_dump(mode='json'), self._config_hash(query)])
        if (selection.owner_id != owner or selection.search_id != expected_key or
                selection.publication_id != query.publication_id or
                selection.story_request_hash != query.story_request_hash or
                selection.query_hash != text_hash(query.query) or
                selection.retrieval_config_hash != self._config_hash(query) or
                selection.manifest_hash != active['manifest_hash'] or
                selection.index_version != active['index_version']):
            raise SearchBlocked('Search selection identity or source version changed.')
        parents = {p.reference.chunk_id: p for p in plan.manifest.passages}
        expected_ids, total = [], 0
        for match in selection.matches:
            parent = parents.get(match.local_passage_id)
            if parent is None or (match.hit.hosted_file_id != active['parsed_files'].get(match.local_passage_id) or
                    match.hit.pdf_pages != parent.reference.pdf_pages or
                    match.match_end != match.match_start + len(match.hit.text) or
                    parent.text[match.match_start:match.match_end] != match.hit.text):
                raise SearchBlocked('Search match no longer maps to the reviewed passage.')
            if match.local_passage_id not in expected_ids:
                expected_ids.append(match.local_passage_id)
        if tuple(p.reference.chunk_id for p in selection.passages) != tuple(expected_ids[:query.options.max_passages]):
            raise SearchBlocked('Selected passages do not match search provenance.')
        for passage in selection.passages:
            parent = parents[passage.reference.chunk_id]
            count = self.token_counter(parent.text)
            if (passage.reference != parent.reference or passage.text != parent.text or
                    passage.index_version != active['index_version'] or type(count) is not int or
                    count <= 0 or passage.token_count != count):
                raise SearchBlocked('Selected evidence differs from its reviewed parent.')
            total += count
        if total > query.options.max_context_tokens:
            raise SearchBlocked('Selected evidence exceeds its context budget.')
        return True

    def assert_current(self, owner, query, selection):
        self._validate_selection(owner, query, selection)
        record = self.store.get(owner, selection.search_id)
        if record['state'] != 'completed' or record.get('selection', {}).get('selection_hash') != selection.selection_hash:
            raise SearchBlocked('Selection is not the saved completed search result.')
        try:
            self.publisher.verify_active(owner, query.publication_id)
        except Exception:
            raise SearchBlocked('Saved selection requires current remote publication verification.') from None
        return True
