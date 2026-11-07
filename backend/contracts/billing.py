"""Simulation DTOs, not a payment gateway, credit ledger or entitlement implementation."""
from typing import Literal
from pydantic import AwareDatetime, field_validator, model_validator
from .common import Contract, Identifier, NonNegative


class SimulatedWeeklyPass(Contract):
    pass_id: Identifier
    account_id: Identifier
    is_simulated: Literal[True]
    price_cents: Literal[500]
    currency: Literal['USD']
    story_limit: Literal[10]
    stories_used: NonNegative
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    auto_renew: Literal[False] = False

    @field_validator('is_simulated', 'auto_renew', mode='before')
    @classmethod
    def booleans_only(cls, value):
        if type(value) is not bool:
            raise ValueError('Use a real boolean, not a numeric or string flag.')
        return value

    @field_validator('price_cents', 'story_limit', mode='before')
    @classmethod
    def integers_only(cls, value):
        if type(value) is not int:
            raise ValueError('Use integer amounts/limits, not numeric strings or floats.')
        return value

    @model_validator(mode='after')
    def valid_pass(self):
        from datetime import timedelta, timezone
        if self.expires_at.astimezone(timezone.utc)-self.starts_at.astimezone(timezone.utc) != timedelta(days=7) or self.stories_used > self.story_limit:
            raise ValueError('A simulated weekly pass lasts seven days and covers at most ten stories.')
        return self
