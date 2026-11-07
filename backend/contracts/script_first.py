"""Versioned v5 script-first review; v4 records keep their original meaning."""
from typing import Literal
from pydantic import model_validator
from .complete_book import CompleteBookPreview,CompleteBookApproval
from .common import content_hash


class ScriptFirstPreview(CompleteBookPreview):
    policy_version:Literal['script-first-review-v1']='script-first-review-v1'

    @model_validator(mode='after')
    def audio_matches(self):
        if self.audio and (not self.narration_requested or self.audio.script_hash!=content_hash(self.narration_script)):
            raise ValueError('Audio does not match the requested script.')
        return self


class ScriptFirstApproval(CompleteBookApproval):
    policy_version:Literal['script-first-review-v1']='script-first-review-v1'
