"""Read server-injected settings; never read/return secret values to the UI."""
from dataclasses import dataclass,field
import os

ENDPOINTS={'groq':'https://api.groq.com/openai/v1/chat/completions',
           'nebius':'https://api.tokenfactory.nebius.com/v1/chat/completions'}


@dataclass(frozen=True)
class ProviderSettings:
    provider: str
    api_key: str = field(repr=False)
    writer_model: str = ''
    review_model: str = ''
    connect_seconds: float = 10.
    read_seconds: float = 60.
    source_review_model: str = ''

    def __post_init__(self):
        if self.provider not in ENDPOINTS:raise ValueError('Unsupported provider.')
        if not 0<self.connect_seconds<=30 or not 0<self.read_seconds<=120:raise ValueError('Invalid provider timeout.')

    def model(self,role):
        if role=='source_reviewer':return self.source_review_model or self.review_model or self.writer_model
        return (self.review_model or self.writer_model) if role=='reviewer' else self.writer_model

    def configured(self,role):return bool(self.api_key.strip() and self.model(role).strip())


def from_environment(environment=None):
    environment=os.environ if environment is None else environment
    mode=environment.get('AI_PROVIDER_MODE','auto')
    if mode not in ('auto','groq','nebius'):raise ValueError('Invalid configured provider mode.')
    settings={name:ProviderSettings(name,environment.get(prefix+'_API_KEY',''),
        environment.get(prefix+'_MODEL',''),environment.get(prefix+'_REVIEW_MODEL',''))
        for name,prefix in (('groq','GROQ'),('nebius','NEBIUS'))}
    return mode,settings


def readiness(environment=None):
    mode,settings=from_environment(environment)
    return dict(default_mode=mode,new_router_live_enabled=False,providers={name:dict(
        writer_configured=value.configured('writer'),reviewer_configured=value.configured('reviewer'),
        review_model_source='role_specific' if value.review_model else 'same_provider_writer_default',
        authentication='not_checked',model_access='not_checked',generation_and_review='not_checked')
        for name,value in settings.items()})
