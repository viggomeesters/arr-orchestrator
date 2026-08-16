#!/usr/bin/env python3
from pathlib import Path
import json, sys
ROOT = Path(__file__).resolve().parents[1]
contract = json.loads((ROOT/'docs/vision.json').read_text(encoding='utf-8'))
schema = json.loads((ROOT/'schemas/repo-vision-contract.schema.json').read_text(encoding='utf-8'))
errors=[]
def check(obj, spec, path='$'):
    t=spec.get('type')
    if t=='object':
        if not isinstance(obj,dict): errors.append(f'{path}: expected object'); return
        for key in spec.get('required',[]):
            if key not in obj: errors.append(f'{path}: missing {key}')
        if spec.get('additionalProperties') is False:
            for key in obj:
                if key not in spec.get('properties',{}): errors.append(f'{path}: unexpected {key}')
        for key, child in spec.get('properties',{}).items():
            if key in obj: check(obj[key],child,f'{path}.{key}')
    elif t=='array':
        if not isinstance(obj,list): errors.append(f'{path}: expected array'); return
        if len(obj)<spec.get('minItems',0): errors.append(f'{path}: too few items')
        if 'items' in spec:
            for i,item in enumerate(obj): check(item,spec['items'],f'{path}[{i}]')
    elif t=='string':
        if not isinstance(obj,str): errors.append(f'{path}: expected string')
        elif len(obj)<spec.get('minLength',0): errors.append(f'{path}: too short')
    if 'const' in spec and obj != spec['const']: errors.append(f'{path}: expected {spec["const"]!r}')
    if 'enum' in spec and obj not in spec['enum']: errors.append(f'{path}: invalid value')
check(contract,schema)
ids=[p.get('id') for p in contract.get('principles',[])]
for required in ('agent-first','plan-before-apply','fail-closed','external-runtime-state','api-over-ui','public-safe'):
    if required not in ids: errors.append(f'missing principle: {required}')
if len(ids)!=len(set(ids)): errors.append('duplicate principle ids')
if len(contract.get('acceptance_scorecard',[]))<6: errors.append('acceptance scorecard needs at least six checks')
if errors:
    print('\n'.join(errors),file=sys.stderr); raise SystemExit(1)
print(f'vision contract: ok ({len(ids)} principles, {len(contract["acceptance_scorecard"])} scorecard checks)')
