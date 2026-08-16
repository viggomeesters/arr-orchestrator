#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
errors=[]
forbidden_names={'.env','id_rsa','id_ed25519','credentials.json','secrets.json'}
for p in ROOT.rglob('*'):
    if '.git' in p.parts: continue
    rel=p.relative_to(ROOT)
    if p.is_file() and (p.name in forbidden_names or p.suffix.lower() in {'.pem','.p12','.pfx','.sqlite','.sqlite3','.db','.torrent','.nzb'}):
        errors.append(f'forbidden tracked-style artifact: {rel}')
tracked=subprocess.run(['git','ls-files'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.splitlines()
for name in tracked:
    base=Path(name).name
    if base in forbidden_names or Path(name).suffix.lower() in {'.pem','.p12','.pfx','.sqlite','.sqlite3','.db','.torrent','.nzb'}:
        errors.append(f'forbidden tracked artifact: {name}')
patterns={
 'private_key': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
 'github_token': re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'),
 'generic_secret_assignment': re.compile(r'(?i)(api[_-]?key|password|token|secret)\s*[:=]\s*["\']?[A-Za-z0-9+/=_-]{16,}')
}
scan_ext={'.md','.json','.jsonl','.yaml','.yml','.py','.sh','.toml','.txt','.example'}
for p in ROOT.rglob('*'):
    if not p.is_file() or '.git' in p.parts or (p.suffix.lower() not in scan_ext and p.name != '.env.example'): continue
    text=p.read_text(encoding='utf-8',errors='ignore')
    for label,pattern in patterns.items():
        if pattern.search(text): errors.append(f'{label} pattern in {p.relative_to(ROOT)}')
if errors:
    print('\n'.join(sorted(set(errors))),file=sys.stderr); raise SystemExit(1)
print(f'public safety check: ok ({len(tracked)} tracked paths inspected)')
