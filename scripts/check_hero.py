#!/usr/bin/env python3
from pathlib import Path
import re, sys, xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
errors=[]
for rel,expected in [('assets/hero.svg',(1200,630)),('assets/social-preview.svg',(1280,640))]:
    p=ROOT/rel
    try: root=ET.parse(p).getroot()
    except Exception as exc: errors.append(f'{rel}: invalid XML: {exc}'); continue
    vb=root.attrib.get('viewBox','').split()
    if len(vb)!=4: errors.append(f'{rel}: missing viewBox'); continue
    width,height=float(vb[2]),float(vb[3])
    if (int(width),int(height))!=expected: errors.append(f'{rel}: expected viewBox {expected}, got {(width,height)}')
    text=' '.join((node.text or '') for node in root.iter() if node.tag.endswith('text'))
    for phrase in ('ARR ORCHESTRATOR','TELEGRAM','DOCTOR','PLAN','APPLY','VERIFY'):
        if phrase not in text.upper(): errors.append(f'{rel}: missing {phrase}')
    if re.search(r'placeholder|todo|tbd|lorem',text,re.I): errors.append(f'{rel}: placeholder text')
readme=(ROOT/'README.md').read_text(encoding='utf-8')
if 'assets/hero.svg' not in readme[:800]: errors.append('README does not render hero near the top')
if errors:
    print('\n'.join(errors),file=sys.stderr); raise SystemExit(1)
print('hero structure check: ok')
