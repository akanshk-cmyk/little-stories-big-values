"""Side-effect-free configuration for future hosted adapters. No secret loading.

Configured is not authorized, reachable, indexed, or ready for story generation.
"""
import os
from dataclasses import dataclass, field
from math import isfinite

from pydantic import SecretStr


@dataclass(frozen=True)
class HostedSettings:
    enabled: bool = False
    api_key: SecretStr = field(default_factory=lambda: SecretStr(''), repr=False)
    organization_id: str = ''
    project_id: str = ''
    index_id: str = ''
    api_generation: str = ''
    region: str = 'us'
    connect_seconds: float = 10.0
    read_seconds: float = 60.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('Hosted enabled flag must be a boolean.')
        if not isinstance(self.api_key, SecretStr):
            raise ValueError('Hosted API key must use secret storage.')
        if self.region not in ('us', 'eu'):
            raise ValueError('Unsupported hosted region.')
        if self.api_generation not in ('', 'v1', 'v2'):
            raise ValueError('Unsupported hosted API generation.')
        for value, limit in ((self.connect_seconds, 30), (self.read_seconds, 120)):
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= limit:
                raise ValueError('Invalid hosted timeout.')
        for value in (self.organization_id, self.project_id, self.index_id):
            if not isinstance(value, str) or (value and (len(value) > 160 or
                    any(not (c.isascii() and (c.isalnum() or c in '_.:-')) for c in value))):
                raise ValueError('Invalid hosted identifier.')

    def missing(self):
        values = {'api_key': self.api_key.get_secret_value().strip(),
                  'organization_id': self.organization_id, 'project_id': self.project_id,
                  'index_id': self.index_id, 'api_generation': self.api_generation}
        return tuple(name for name, value in values.items() if not value)

    def status(self):
        """Internal diagnostic only; no credentials or account identifiers returned."""
        return {'enabled': self.enabled, 'configured': not self.missing(),
                'missing': self.missing(), 'integration_implemented': False,
                'authentication': 'not_checked', 'index_access': 'not_checked',
                'live_execution_allowed': False}


def from_environment(environment=None):
    env = os.environ if environment is None else environment
    flag = env.get('LLAMACLOUD_ENABLED', 'false').strip().lower()
    if flag not in ('true', 'false'):
        raise ValueError('LLAMACLOUD_ENABLED must be true or false.')
    try:
        connect = float(env.get('LLAMACLOUD_CONNECT_SECONDS', '10'))
        read = float(env.get('LLAMACLOUD_READ_SECONDS', '60'))
    except (ValueError, TypeError):
        raise ValueError('Invalid hosted timeout configuration.') from None
    return HostedSettings(enabled=flag == 'true',
        api_key=SecretStr(env.get('LLAMA_CLOUD_API_KEY', '')),
        organization_id=env.get('LLAMACLOUD_ORGANIZATION_ID', ''),
        project_id=env.get('LLAMACLOUD_PROJECT_ID', ''),
        index_id=env.get('LLAMACLOUD_INDEX_ID', ''),
        api_generation=env.get('LLAMACLOUD_API_GENERATION', ''),
        region=env.get('LLAMACLOUD_REGION', 'us'),
        connect_seconds=connect, read_seconds=read)
