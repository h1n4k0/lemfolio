"""Rebuild guarded local edits against a frozen full source, never a snippet."""
from __future__ import annotations
from .store import StoreError, TEXT_EXTENSIONS, validate_path
from pathlib import Path


def resolve_changes(changes, snapshot, *, coverage=None):
    resolved = []
    seen = set()
    for change in changes:
        path = validate_path(change['path'])
        if path.casefold() in seen:
            raise StoreError('Duplicate change paths', 400)
        seen.add(path.casefold())
        content, edits = change.get('content'), change.get('edits')
        if edits is None:
            if 'content' not in change:
                raise StoreError('A change requires an explicit replacement, deletion or local edit',400)
            # A null content is a deliberate manual deletion. Agent policy is
            # checked separately and does not allow this as a silent fallback.
            resolved.append({'path':path,'content':content})
            continue
        if content is not None or not edits or len(edits)>100:
            raise StoreError('Choose a complete replacement or guarded local edits',400)
        if path not in snapshot['text_files'] or Path(path).suffix.lower() not in TEXT_EXTENSIONS:
            raise StoreError('Local edits require an existing frozen UTF-8 text file',409)
        source = snapshot['text_files'][path]
        spans = []
        for edit in edits:
            old, new = edit['old_text'], edit['new_text']
            if not old:
                raise StoreError('An edit anchor cannot be empty',400)
            start = source.find(old)
            if start < 0 or source.find(old,start+1) >= 0:
                raise StoreError('Local edit anchor must match exactly once in the frozen full file',409)
            end = start+len(old)
            if coverage is not None:
                file_coverage = coverage.get(path,{})
                if file_coverage.get('sha256') != snapshot['source_hashes'][path] or not any(r['start_char'] <= start and end <= r['end_char'] for r in file_coverage.get('ranges',[])):
                    raise StoreError('Local edit anchor is outside the model-visible frozen range',409)
            spans.append((start,end,new))
        spans.sort(key=lambda item:item[0])
        if any(right[0] < left[1] for left,right in zip(spans,spans[1:])):
            raise StoreError('Local edit anchors overlap',409)
        result = source
        for start,end,new in reversed(spans):
            result = result[:start]+new+result[end:]
        if snapshot['files'][path].startswith(b'\xef\xbb\xbf'):
            result = '\ufeff'+result
        resolved.append({'path':path,'content':result})
    return resolved
