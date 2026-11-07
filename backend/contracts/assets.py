from typing import Literal
from pydantic import Field, model_validator
from .common import Contract, Digest, Identifier, Text, unique


class CharacterDesign(Contract):
    character_id: Identifier
    design_version: Identifier
    appearance: Text
    clothing: Text
    identifying_details: tuple[Text, ...]
    approved_reference_asset_ids: tuple[Identifier, ...] = ()


class CharacterManifest(Contract):
    seal_field = 'manifest_hash'
    manifest_id: Identifier
    characters: tuple[CharacterDesign, ...] = Field(min_length=1, max_length=8)
    manifest_hash: Digest | None = None

    @model_validator(mode='after')
    def unique_characters(self):
        unique(tuple(c.character_id for c in self.characters), 'character designs')
        return self


class StaticAssetReference(Contract):
    asset_id: Identifier
    version: Identifier
    file_hash: Digest
    media_type: Literal['image/png', 'image/jpeg', 'image/webp']
    origin: Literal['approved_static_library']
    review_status: Literal['approved']
    rights_status: Literal['permitted']
    character_ids: tuple[Identifier, ...]
    style: Text
    accessible_description: Text
    # No URLs, runtime generation settings or model-granted authorization.


class PageAsset(Contract):
    page_number: int = Field(ge=1, le=6)
    asset: StaticAssetReference
    scene_inspection: Literal['PASS', 'FAIL', 'PENDING'] = 'PENDING'


class AssetManifest(Contract):
    seal_field = 'manifest_hash'
    manifest_id: Identifier
    pages: tuple[PageAsset, ...] = ()
    manifest_hash: Digest | None = None

    @model_validator(mode='after')
    def unique_pages(self):
        unique(tuple(p.page_number for p in self.pages), 'asset pages')
        return self
