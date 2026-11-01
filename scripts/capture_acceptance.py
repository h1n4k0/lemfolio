"""Capture a small, secret-free receipt from an existing live verification."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument('--run', required=True)
parser.add_argument('--verification', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = root / 'data'
with sqlite3.connect((data / 'runtime' / 'runs.sqlite3').as_uri() + '?mode=ro', uri=True) as database:
    row = database.execute('SELECT id,task,status,result,error FROM runs WHERE id=?', (args.run,)).fetchone()
if not row:
    raise SystemExit('Unknown run')
task = json.loads(row[1])
verification = json.loads((data / 'evidence' / (args.verification + '.json')).read_text(encoding='utf-8'))
build = json.loads((data / 'builds' / verification['result']['build_id'] / 'receipt.json').read_text(encoding='utf-8'))
assert verification['result']['run_id'] == args.run
assert build['base_revision'] == verification['base_revision'] == task['baseRevision']
assert row[2] == 'completed' and build['status'] == 'success'
result = json.loads(row[3])
receipt = {
    'evidence_class': 'real_browser_api_codex_subscription_and_docker_execution',
    'run_id': row[0], 'provider': task['provider'], 'mode': task['mode'],
    'status': row[2], 'error': row[4], 'base_revision': task['baseRevision'],
    'context_snapshot_id': task['contextSnapshotId'],
    'independent_tools_in_context': [item['tool'] for item in task['context']['domain']['tool_evidence']],
    'answer': result['answer'], 'proposed_change_count': len(result['changes']),
    'verification_id': args.verification, 'build_id': build['id'],
    'build_status': build['status'], 'image_id': build['image_id'],
    'artifacts': build['artifacts'],
    'verification_tool_results': [{'tool': item['tool'], 'status': item['result']['status'], 'provenance': item['provenance']} for item in verification['result']['tool_evidence']],
    'research_accepted': False, 'human_review_required': True,
    'limitations': ['Transport and independent check execution are verified; research correctness is not certified.', 'Claude subscription transport was not live-tested because its official CLI is not logged in.']
}
output = root / 'reports' / 'live-acceptance.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
print(str(output))
print('sha256=' + hashlib.sha256(output.read_bytes()).hexdigest())
