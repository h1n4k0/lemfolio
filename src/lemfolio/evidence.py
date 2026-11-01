"""Durable verification records and byte-bound immutable source snapshots."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile
from .store import StoreError, TEXT_EXTENSIONS, validate_path


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root) / 'evidence'
        self.snapshots = Path(root) / 'source_snapshots'
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        # A prepared verification is not a completed scientific check. Source
        # approval is already durable in Store and is not rolled back here.
        for path in self.root.glob('*.json'):
            try:
                record=self.read(path.stem)
                if record.get('kind')=='research_verification' and record['result'].get('verification_status')=='pending':
                    record['result'].update(verification_status='failed',verification_error='Application restarted before verification was committed; approved files remain applied',build_error='Verification interrupted; explicit retry required')
                    self.write(record)
            except (ValueError,OSError):
                continue

    @staticmethod
    def _identity(value):
        if not re.fullmatch(r'[a-f0-9]{32,64}', value):
            raise ValueError('Invalid evidence identity')
        return value

    def append(self, project_id, kind, result, snapshot, *, identity=None):
        record = {'id': identity or uuid.uuid4().hex, 'project_id': project_id, 'kind': kind,
                  'base_revision': snapshot['revision'], 'state_revision': snapshot['state_revision'],
                  'snapshot_id': snapshot['snapshot_id'], 'main_file': snapshot['main_file'],
                  'created_at': time.time(), 'result': result}
        if kind=='research_verification':
            record['engine']=result['engine']
        self.write(record)
        return record

    def write(self, record):
        with self.lock:
            path = self.root / (self._identity(record['id']) + '.json')
            temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            with temporary.open('w', encoding='utf-8', newline='\n') as output:
                json.dump(record, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)

    def read(self, identity):
        with self.lock:
            value = json.loads((self.root / (self._identity(identity)+'.json')).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('id') != identity or not isinstance(value.get('result'), dict):
            raise ValueError('Damaged evidence record cannot support verification')
        return value

    def list(self, project_id):
        values = []
        for path in self.root.glob('*.json'):
            try:
                value = self.read(path.stem)
                if value.get('project_id') == project_id:
                    values.append(value)
            except (ValueError, OSError):
                continue
        return sorted(values, key=lambda item:item.get('created_at',0), reverse=True)

    def save_snapshot(self, snapshot):
        identity = self._identity(snapshot['snapshot_id'])
        with self.lock:
            if sum(p.stat().st_size for p in self.snapshots.glob('*') if p.is_file()) + sum(len(value) for value in snapshot['files'].values()) > 2 * 1024**3:
                raise StoreError('Source snapshot storage limit reached', 507)
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
                for path, data in sorted(snapshot['files'].items()):
                    archive.writestr(validate_path(path), data)
            raw = stream.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            metadata = {key:snapshot[key] for key in ('project','source_hashes','revision','state_revision','snapshot_id','main_file')}
            metadata['archive_sha256'] = digest
            for extension, content in (('.zip', raw), ('.json', json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode())):
                target = self.snapshots / (identity+extension)
                temporary = target.with_suffix(extension+'.tmp')
                with temporary.open('wb') as output:
                    output.write(content); output.flush(); os.fsync(output.fileno())
                os.replace(temporary, target)
        return digest

    def load_snapshot(self, identity, expected_digest):
        identity = self._identity(identity)
        with self.lock:
            metadata = json.loads((self.snapshots/(identity+'.json')).read_text(encoding='utf-8'))
            raw = (self.snapshots/(identity+'.zip')).read_bytes()
        if metadata.get('snapshot_id') != identity or hashlib.sha256(raw).hexdigest() != expected_digest or metadata.get('archive_sha256') != expected_digest:
            raise StoreError('Source snapshot integrity check failed', 409)
        files = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                path = validate_path(info.filename)
                if path in files or info.file_size > 20*1024**2 or info.file_size > len(raw):
                    raise StoreError('Invalid frozen source archive',409)
                files[path] = archive.read(info)
        hashes = {path:hashlib.sha256(data).hexdigest() for path,data in files.items()}
        revision = hashlib.sha256(json.dumps(hashes,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if hashes != metadata['source_hashes'] or revision != metadata['revision']:
            raise StoreError('Frozen source manifest mismatch',409)
        texts={}
        for path,data in files.items():
            if Path(path).suffix.lower() in TEXT_EXTENSIONS:
                try:
                    texts[path]=data.decode('utf-8-sig')
                except UnicodeDecodeError:
                    continue  # Retain raw bytes/hash; do not invent text visibility.
        return {**metadata,'files':files,'text_files':texts}
