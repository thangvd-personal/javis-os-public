"""Prepare and rehearse a paired Team/business comparison without calling a model.

CLI: python pilot/team-multiday/paired_evaluation.py --dry-run --out <directory>
Real execution requires a caller-supplied adapter, frozen real inputs and owner grading.
This module deliberately has no provider or application-engine imports.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Callable

ARMS = ('single', 'team')
RUBRIC = ('facts', 'completeness', 'usefulness', 'clarity')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def synthetic_manifest() -> dict:
    """Twenty explicitly synthetic rehearsal inputs; none are a business result."""
    pairs = []
    for workflow in ('content', 'sales_analysis'):
        for index in range(1, 11):
            data = {'sku': f'DEMO-{index:02}', 'price': 100000 + index * 1000} if workflow == 'content' else {
                'period': 'synthetic-period', 'rows': [{'order_id': str(index), 'gross': index * 1000,
                                                       'refund': 100, 'cancelled': False}],
                'independent_expected_net': index * 1000 - 100}
            item = {'pair_id': f'{workflow}-{index:02}', 'workflow': workflow,
                    'input': {'prompt': 'Synthetic rehearsal: prepare a draft from this frozen source.',
                              'data': data},
                    'criteria': {'owner_confirm': True, 'rubric': list(RUBRIC)}}
            item['input_hash'] = digest(item['input'])
            pairs.append(item)
    return {'schema': 1, 'source_kind': 'synthetic', 'settings': {'provider': 'fake', 'model': 'fake',
            'reasoning': 'fixed', 'tools': 'none', 'quality_policy': 'observe_only'},
            'observation_days_required': [7, 10], 'pairs': pairs}


def validate_manifest(manifest: dict, dry_run: bool) -> None:
    pairs = manifest.get('pairs') or []
    if len(pairs) != 20 or len({p.get('pair_id') for p in pairs}) != 20:
        raise ValueError('Expected twenty uniquely identified pairs')
    if any(sum(p.get('workflow') == kind for p in pairs) != 10 for kind in ('content', 'sales_analysis')):
        raise ValueError('Expected ten content and ten sales-analysis pairs')
    for pair in pairs:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,80}', str(pair.get('pair_id', ''))):
            raise ValueError('Pair identifiers must be safe directory names')
        if pair.get('input_hash') != digest(pair.get('input')):
            raise ValueError('An input changed after the manifest was frozen')
        if not pair.get('criteria'):
            raise ValueError('Every pair needs the same acceptance rubric for both arms')
    if not manifest.get('settings'):
        raise ValueError('Frozen model, reasoning and tool settings are required')
    if not dry_run and manifest.get('source_kind') != 'real_frozen':
        raise ValueError('Synthetic inputs cannot establish real business outcomes')


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def run_pairs(manifest: dict, executor: Callable[[dict], dict], out: Path, *, dry_run: bool) -> dict:
    """Execute isolated arms once; a recorded failure remains part of the experiment.

    A retry is a new measured experiment/attempt, not an overwrite of a failed record.
    The executor cannot mark its own product accepted; owner grades are separate.
    """
    validate_manifest(manifest, dry_run)
    if dry_run and not getattr(executor, 'is_fake', False):
        raise ValueError('Dry runs require an explicitly marked fake executor')
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_hash = digest(manifest)
    manifest_path = out / 'manifest.json'
    if manifest_path.exists() and digest(json.loads(manifest_path.read_text(encoding='utf-8'))) != manifest_hash:
        raise ValueError('This output directory belongs to another frozen experiment')
    _write(manifest_path, manifest)
    order = [(p, arm) for p in manifest['pairs'] for arm in ARMS]
    random.Random(283).shuffle(order)
    records = []
    for pair, arm in order:
        path = out / 'records' / pair['pair_id'] / f'{arm}.json'
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous.get('manifest_hash') != manifest_hash or previous.get('input_hash') != pair['input_hash']:
                raise ValueError('An existing execution belongs to a different input')
            records.append(previous)
            continue
        workspace = out / 'workspaces' / pair['pair_id'] / arm
        workspace.mkdir(parents=True, exist_ok=True)
        if not workspace.resolve().is_relative_to(out):
            raise ValueError('Workspace escapes the experiment directory')
        request = {'pair_id': pair['pair_id'], 'arm': arm, 'input': copy.deepcopy(pair['input']),
                   'input_hash': pair['input_hash'], 'settings': copy.deepcopy(manifest['settings']),
                   'criteria': copy.deepcopy(pair['criteria']), 'workspace': str(workspace),
                   'history': [], 'dry_run': dry_run}
        record = {'pair_id': pair['pair_id'], 'workflow': pair['workflow'], 'arm': arm,
                  'manifest_hash': manifest_hash, 'input_hash': pair['input_hash'], 'dry_run': dry_run,
                  'status': 'running', 'usage': None, 'owner_review': None}
        # Persist before entering the adapter: a crash must not repeat a possibly completed action.
        _write(path, record)
        start = time.monotonic()
        try:
            result = executor(request)
            if not isinstance(result, dict):
                raise ValueError('An executor must return a structured result')
            product = workspace / 'result.txt'
            product.write_text(str(result.get('text') or ''), encoding='utf-8')
            record.update(status='dry_run_only' if dry_run else 'pending_owner_review',
                          output_sha256=hashlib.sha256(product.read_bytes()).hexdigest(),
                          product=str(product.relative_to(out)), usage=result.get('usage'))
        except Exception as ex:
            # No exception text: adapters might include credentials or raw customer data in errors.
            record.update(status='failed', error_type=type(ex).__name__)
        record['elapsed_seconds'] = time.monotonic() - start
        _write(path, record)
        records.append(record)
    report = summarize(records, dry_run=dry_run)
    _write(out / 'report.json', report)
    reviews = out / 'owner-review-template.json'
    if not reviews.exists():
        _write(reviews, {'instruction': 'Owner grades actual outputs; leave unobserved values null.',
                        'reviews': [{'pair_id': p['pair_id'], 'arm': arm, 'accepted': None,
                                     'scores': {key: None for key in RUBRIC}, 'critical_errors': None,
                                     'owner_minutes': None} for p in manifest['pairs'] for arm in ARMS]})
    return report


def summarize(records: list, *, dry_run: bool) -> dict:
    # No fabricated savings or scores: dry execution is only a preparation check.
    return {'business_status': 'not_run' if dry_run else 'pending_owner_review',
            'execution_records': len(records), 'failed_executions': sum(r['status'] == 'failed' for r in records),
            'interrupted_executions': sum(r['status'] == 'running' for r in records),
            'unknown_usage_executions': sum(r.get('usage') is None for r in records),
            'accepted_results': 0, 'quality_improvement': None, 'cash_cost_per_accepted_result': None,
            'real_model_calls': 0 if dry_run else None,
            'pending': ['frozen real business inputs', 'configured provider/model access',
                        '7-10 day observation', 'independent owner grading']}


def _fake_executor(request: dict) -> dict:
    return {'text': f"Synthetic rehearsal: {request['pair_id']} / {request['arm']}", 'usage': None}


_fake_executor.is_fake = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_pairs(synthetic_manifest(), _fake_executor, args.out, dry_run=True), indent=2))


if __name__ == '__main__':
    main()
