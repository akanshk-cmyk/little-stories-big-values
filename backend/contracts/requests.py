from typing import Literal
from pydantic import Field, model_validator
from .common import Contract, Identifier, Text, unique

StoryType = Literal['original_inspired', 'adapted_retelling', 'traditional_retelling']
ProviderMode = Literal['auto', 'groq', 'nebius']


class StoryRequest(Contract):
    child_profile_id: Identifier
    age: int = Field(ge=3, le=8)
    required_character_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    objective: Text
    language: Literal['English'] = 'English'
    target_minutes: int
    mood: Text
    story_type: StoryType
    idea: str = Field(default='', max_length=2000)
    exclusions: tuple[Text, ...] = ()
    required_elements: tuple[Literal['moral', 'parent_question', 'cultural_fact', 'family_activity'], ...] = ('moral', 'parent_question')
    illustration_style: Text | None = None
    narration_requested: bool = False

    @model_validator(mode='after')
    def valid_request(self):
        if self.target_minutes not in (3, 5, 7, 10):
            raise ValueError('Duration must be 3, 5, 7 or 10 minutes.')
        unique(self.required_character_ids, 'required characters')
        unique(self.required_elements, 'required elements')
        return self


class PlaybookCapabilities(Contract):
    """Capabilities are injected from trusted configuration, never from model output."""
    character_ids: tuple[Identifier, ...]
    objectives: tuple[Text, ...]
    story_types: tuple[StoryType, ...]
    minimum_age: int = Field(default=3, ge=3, le=8)
    maximum_age: int = Field(default=8, ge=3, le=8)

    @model_validator(mode='after')
    def ordered_ages(self):
        if self.minimum_age > self.maximum_age:
            raise ValueError('Invalid age range.')
        return self


def validate_capabilities(request: StoryRequest, capabilities: PlaybookCapabilities):
    if not set(request.required_character_ids) <= set(capabilities.character_ids):
        raise ValueError('A requested character is not supported by this Playbook.')
    if request.objective not in capabilities.objectives or request.story_type not in capabilities.story_types:
        raise ValueError('The requested objective or story type is unsupported.')
    if not capabilities.minimum_age <= request.age <= capabilities.maximum_age:
        raise ValueError('The requested age is unsupported.')
    return request
