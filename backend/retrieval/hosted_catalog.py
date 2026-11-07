"""Account-owned active-source catalog. Local metadata reads only, never query APIs."""
from types import MappingProxyType
from backend.contracts.common import content_hash
from .evidence_resolver import EvidenceSourcePlan
from .hosted_search import HybridSearchOptions


class HostedSourceCatalog:
    def __init__(self, accounts, workspace, publisher, *, usage_reference=None,
                 defaults=None, search_options=None):
        self.accounts, self.workspace, self.publisher = accounts, workspace, publisher
        self.usage_reference = usage_reference or (lambda owner, request, manifest: None)
        self.defaults = MappingProxyType(dict(defaults or {}))
        self.options = search_options or HybridSearchOptions()
        self.version = 'hosted-catalog-' + content_hash([sorted((str(k), v) for k, v in self.defaults.items()),
                                                       self.options.model_dump(mode='json')])

    @staticmethod
    def query_for(request):
        text = '\n'.join(('Topic: ' + request.idea, 'Characters: ' + ', '.join(request.required_character_ids),
                          'Value: ' + request.objective))
        if len(text) > 2000:
            raise ValueError('The topic is too long for the configured source query.')
        return text

    def list(self, owner):
        self.accounts.require(owner, role=None)
        with self.publisher.store.db() as db:
            rows = db.execute('SELECT p.id FROM active_publications a JOIN publications p ON p.id=a.id '
                              'WHERE p.owner=? ORDER BY p.id', (owner,)).fetchall()
        result = []
        for row in rows:
            try:
                active = self.publisher.active(owner, row[0])  # No remote request.
                _, plan = self.publisher._load(owner, row[0])
                summary = self.workspace.summary(plan.manifest.source_id, owner)
                result.append(dict(source_id=plan.manifest.source_id, title=summary['title'],
                    edition=summary['edition'], purpose=plan.manifest.purpose, publication_id=row[0],
                    index_version=active['index_version'], live_verification='not_checked_by_catalog'))
            except (PermissionError, ValueError, RuntimeError):
                continue  # Revoked/stale entries are never offered as eligible choices.
        return result

    def select(self, owner, request, options):
        purpose = ('traditional_claims' if request.story_type != 'original_inspired' or
                   'cultural_fact' in request.required_elements else 'parent_inspiration')
        choices = [item for item in self.list(owner) if item['purpose'] == purpose]
        selected = options.selected_source_ids
        if selected:
            choices = [item for item in choices if selected == (item['source_id'],)]
        elif (owner, purpose) in self.defaults:
            choices = [item for item in choices if item['source_id'] == self.defaults[(owner, purpose)]]
        if len(choices) != 1:
            return None  # No arbitrary first-source selection or silent substitution.
        choice = choices[0]
        _, publication = self.publisher._load(owner, choice['publication_id'])
        return EvidenceSourcePlan(publication_id=choice['publication_id'], query=self.query_for(request),
            usage_authorization_id=self.usage_reference(owner, request, publication.manifest), options=self.options)
