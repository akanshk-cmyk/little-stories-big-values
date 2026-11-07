"""Pure data contracts. No providers, database, graph or environment side effects."""
import hashlib
import json
from typing import Annotated, ClassVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator


def nonblank(value):
    if isinstance(value, str) and not value.strip():
        raise ValueError('Text must not be blank.')
    return value  # Preserve actual text/quotes; never normalize them implicitly.


Text = Annotated[str, BeforeValidator(nonblank), StringConstraints(min_length=1, max_length=12000)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
NonNegative = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(ge=1)]


def canonical_json(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError('Duplicate '+label+'.')


class Contract(BaseModel):
    """Strict scalar types and immutable nested model/tuple fields for new contracts."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, allow_inf_nan=False,
                              validate_default=True)
    seal_field: ClassVar[str | None] = None

    @model_validator(mode='after')
    def validate_seal(self):
        if self.seal_field:
            expected = content_hash(self.model_dump(mode='json', exclude={self.seal_field}))
            supplied = getattr(self, self.seal_field)
            if supplied is not None and supplied != expected:
                raise ValueError('Content hash does not match '+self.seal_field+'.')
            object.__setattr__(self, self.seal_field, expected)
        return self

    def model_copy(self, *, update=None, deep=False):
        # Pydantic's ordinary model_copy(update=...) bypasses validation.
        # Revalidate replacements, including stale content hashes.
        if not update:
            return self
        value = self.model_dump(mode='python')
        value.update(update)
        return type(self).model_validate(value)


def parse_payload(model, payload):
    """JSON arrays may become tuples; strict scalar checks still apply."""
    return model.model_validate_json(canonical_json(payload))
