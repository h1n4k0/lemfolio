"""Export/check the actual Pydantic-backed API against the versioned contract."""
import argparse
import json
from pathlib import Path
import tempfile
from lemfolio.api import create_app

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
base=root/'.test-data';base.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='contract-export-',dir=base) as directory:
    app=create_app(Path(directory))
    try:
        content=json.dumps(app.openapi(),ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    finally:
        app.state.runtime.close();app.state.compiler.close()
target=root/'contracts/v1/openapi.json'
if args.check:
    if not target.exists() or target.read_text(encoding='utf-8')!=content:
        raise SystemExit('OpenAPI contract drift: regenerate and review contracts/v1/openapi.json')
else:
    target.write_text(content,encoding='utf-8',newline='\n')
print('OpenAPI contract check passed' if args.check else 'OpenAPI contract exported')
