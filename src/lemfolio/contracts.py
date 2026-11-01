"""Protocol DTOs generated from contracts/v1/schema.json, shared with TypeScript."""
from __future__ import annotations
import json
from pathlib import Path
from ._generated_contracts import *  # noqa: F403 -- generated protocol surface

SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'contracts' / 'v1' / 'schema.json'

def protocol_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
