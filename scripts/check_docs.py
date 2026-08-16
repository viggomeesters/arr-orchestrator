#!/usr/bin/env python3
from pathlib import Path
import re, sys
ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / 'README.md', ROOT / 'CONTRIBUTING.md', ROOT / 'SECURITY.md', *sorted((ROOT / 'docs').rglob('*.md'))]
errors = []
link_re = re.compile(r'\[[^\]]+\]\(([^)]+)\)')
for page in PAGES:
    text = page.read_text(encoding='utf-8')
    if not text.strip(): errors.append(f'empty page: {page.relative_to(ROOT)}')
    for target in link_re.findall(text):
        if target.startswith(('http://','https://','#','mailto:')): continue
        clean = target.split('#',1)[0]
        if not clean: continue
        resolved = (page.parent / clean).resolve()
        if not resolved.exists(): errors.append(f'broken link in {page.relative_to(ROOT)}: {target}')
for token in ('TODO', 'TBD', 'PLACEHOLDER', 'COMING SOON'):
    for page in PAGES:
        if token.lower() in page.read_text(encoding='utf-8').lower():
            errors.append(f'stale marker {token} in {page.relative_to(ROOT)}')
if errors:
    print('\n'.join(errors), file=sys.stderr); raise SystemExit(1)
print(f'documentation check: ok ({len(PAGES)} pages)')
