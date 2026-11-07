"""Reviewed local image bytes and exact-draft scene bindings. No image generation."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from PIL import Image
from backend.contracts import AssetManifest,PageAsset,StaticAssetReference,content_hash,parse_payload
from backend.contracts.common import canonical_json


class AssetRegistry:
    policy_version='static-scenes-v1'

    def __init__(self,accounts):
        self.accounts=accounts;self.store=accounts.store
        self.folder=self.store.folder/'static-assets';self.folder.mkdir(exist_ok=True)
        with self.store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS image_assets(id TEXT PRIMARY KEY,payload TEXT,status TEXT);
                CREATE TABLE IF NOT EXISTS asset_reviews(id TEXT PRIMARY KEY,asset_id TEXT,payload TEXT);
                CREATE TABLE IF NOT EXISTS scene_reviews(id TEXT PRIMARY KEY,payload TEXT);
                CREATE TABLE IF NOT EXISTS scene_selections(draft_hash TEXT,page INTEGER,review_id TEXT,
                    PRIMARY KEY(draft_hash,page));''')

    def add(self,admin,content,*,characters,style,description):
        self.accounts.require(admin,'admin')
        if not isinstance(content,bytes) or not 0<len(content)<=15*1024*1024:raise ValueError('Choose an image up to 15 MB.')
        if not isinstance(characters,(list,tuple)) or not 1<=len(characters)<=8 or any(not isinstance(c,str) for c in characters) or len(set(characters))!=len(characters):raise ValueError('List distinct character identifiers.')
        # Reuse the existing typed static-reference boundary to validate metadata.
        digest=hashlib.sha256(content).hexdigest()
        with Image.open(BytesIO(content)) as image:
            if image.width*image.height>25000000 or getattr(image,'is_animated',False):raise ValueError('Use a still image under 25 megapixels.')
            kind={'PNG':('png','image/png'),'JPEG':('jpg','image/jpeg'),'WEBP':('webp','image/webp')}.get(image.format)
            if not kind:raise ValueError('Use a PNG, JPEG or WebP image.')
            image.verify()
        asset=uuid4().hex
        ref=StaticAssetReference(asset_id=asset,version='pending',file_hash=digest,media_type=kind[1],
            origin='approved_static_library',review_status='approved',rights_status='permitted',
            character_ids=tuple(characters),style=style,accessible_description=description)
        # The typed reference is only a metadata candidate until its authority
        # row is approved; its literals never grant review permission.
        payload=dict(reference=ref.model_dump(mode='json'),file=digest+'.'+kind[0],review_id=None)
        path=self.folder/payload['file']
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Stored image integrity check failed.')
        path.write_bytes(content)
        with self.store.db(write=True) as db:db.execute('INSERT INTO image_assets VALUES(?,?,?)',(asset,canonical_json(payload),'pending'))
        return dict(asset_id=asset,status='pending')

    def review(self,admin,asset_id,*,approve,rights_basis,gentle,notes):
        self.accounts.require(admin,'admin')
        if type(approve) is not bool or type(gentle) is not bool or not isinstance(notes,str) or not notes.strip():raise ValueError('Record the actual review decision and notes.')
        if approve and (not gentle or not isinstance(rights_basis,str) or not rights_basis.strip()):raise ValueError('Approval needs permitted rights and a gentle-content inspection.')
        review=uuid4().hex
        with self.store.db(write=True) as db:
            row=db.execute('SELECT * FROM image_assets WHERE id=?',(asset_id,)).fetchone()
            if not row:raise ValueError('Image not found.')
            payload=json.loads(row['payload']);self._bytes(payload)
            event=dict(review_id=review,asset_id=asset_id,reviewer=admin,approve=approve,
                gentle=gentle,rights_basis=rights_basis,notes=notes,file_hash=payload['reference']['file_hash'])
            db.execute('INSERT INTO asset_reviews VALUES(?,?,?)',(review,asset_id,canonical_json(event)))
            payload['reference']['version']=review;payload['review_id']=review
            db.execute('UPDATE image_assets SET status=?,payload=? WHERE id=?',
                ('approved' if approve else 'withdrawn',canonical_json(payload),asset_id))
        return event

    def _bytes(self,payload):
        path=(self.folder/payload['file']).resolve()
        if not path.is_relative_to(self.folder.resolve()):raise PermissionError('Image path is not authorized.')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=payload['reference']['file_hash']:raise ValueError('Image content changed; review again.')
        return content

    def approved(self,asset_id,db=None):
        if db is None:
            with self.store.db() as connection:return self.approved(asset_id,connection)
        row=db.execute('SELECT * FROM image_assets WHERE id=?',(asset_id,)).fetchone()
        if not row or row['status']!='approved':raise PermissionError('This image is not currently approved.')
        payload=json.loads(row['payload']);self._bytes(payload)
        return payload

    def inspect_scene(self,admin,draft,characters,page_number,asset_id,*,matches,notes):
        self.accounts.require(admin,'admin')
        if type(page_number) is not int or not 1<=page_number<=6 or type(matches) is not bool or not isinstance(notes,str) or not notes.strip():raise ValueError('Record a page-specific image inspection.')
        if draft.character_manifest_id!=characters.manifest_id:raise ValueError('Wrong character designs.')
        page=draft.pages[page_number-1];asset=self.approved(asset_id)
        if not set(page.character_ids)<=set(asset['reference']['character_ids']):raise ValueError('The image does not contain the required scene characters.')
        review=uuid4().hex
        record=dict(review_id=review,reviewer=admin,draft_hash=draft.draft_hash,
            character_hash=characters.manifest_hash,page_number=page_number,page_hash=content_hash(page),
            asset=asset['reference'],matches=matches,notes=notes,policy_version=self.policy_version)
        with self.store.db(write=True) as db:
            # Recheck within the same transaction that publishes the selection.
            if self.approved(asset_id,db)['reference']!=asset['reference']:raise ValueError('Image review changed.')
            db.execute('INSERT INTO scene_reviews VALUES(?,?)',(review,canonical_json(record)))
            db.execute('INSERT INTO scene_selections VALUES(?,?,?) ON CONFLICT(draft_hash,page) DO UPDATE SET review_id=excluded.review_id',
                (draft.draft_hash,page_number,review))
        return record

    def revision(self,draft_hash):
        with self.store.db() as db:rows=[list(r) for r in db.execute('SELECT page,review_id FROM scene_selections WHERE draft_hash=? ORDER BY page',(draft_hash,))]
        return content_hash(rows)

    def manifest(self,draft,characters,*,style=None):
        pages=[]
        with self.store.db() as db:
            rows=db.execute('SELECT s.payload FROM scene_reviews s JOIN scene_selections p ON p.review_id=s.id WHERE p.draft_hash=? ORDER BY p.page',(draft.draft_hash,)).fetchall()
            for row in rows:
                record=json.loads(row[0]);page=draft.pages[record['page_number']-1]
                if (not record['matches'] or record['character_hash']!=characters.manifest_hash or
                    record['page_hash']!=content_hash(page) or record['policy_version']!=self.policy_version):continue
                asset=self.approved(record['asset']['asset_id'],db)
                if asset['reference']!=record['asset']:raise PermissionError('A scene image changed; inspect and approve the replacement.')
                ref=parse_payload(StaticAssetReference,record['asset'])
                if style and ref.style!=style:raise ValueError('The image style does not match the confirmed request.')
                pages.append(PageAsset(page_number=page.page_number,asset=ref,scene_inspection='PASS'))
        identity=content_hash([draft.draft_hash,characters.manifest_hash,[p.model_dump(mode='json') for p in pages]])
        return AssetManifest(manifest_id='assets-'+identity,pages=tuple(pages))

    def validate(self,draft,characters,manifest,*,style=None):
        current=self.manifest(draft,characters,style=style)
        if len(current.pages)!=6 or current!=manifest:raise PermissionError('All six current scene images must pass review for this exact version.')
        return True

    def image_bytes(self,reference):
        payload=self.approved(reference.asset_id)
        if payload['reference']!=reference.model_dump(mode='json'):raise PermissionError('Image version is stale.')
        return self._bytes(payload)
