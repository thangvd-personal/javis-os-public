"""Host-private declared-reference evidence from the real bound context manifest.

This is not a grant parser or a complete executable/runtime dependency proof.
Only the semantic service supplies its verified host binding; HTTP/model JSON
must never be accepted as this capture. No discovered path expands that grant.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import improvement_store as IM
from improvement_cases import output_path
from improvement_skill_patch import reference_inventory
from improvement_trial_context import ContextSource

_LINK = re.compile(r'\]\(([^\s)#]+)(?:#[^\s)]*)?\)')
_MARKDOWN = re.compile(r'!?\[[^\]\r\n]*\]\(([^)\r\n]*)\)')
_URI = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|mailto:|data:)[^\s`<>\"'()\[\]{}]+")
_EXECUTABLE = {'.py', '.sh', '.ps1', '.exe', '.bat', '.cmd', '.js', '.mjs', '.cjs'}


def _references(raw):
    text = raw.decode('utf-8')
    targets = _MARKDOWN.findall(text)
    # Keep previously recognized links with complex labels as well. Exclude
    # href spans from plain-reference extraction; equal spellings can denote
    # different root-relative and document-relative identities.
    plain = _MARKDOWN.sub('', text)
    targets.extend(_LINK.findall(plain))
    plain = _LINK.sub('', plain)
    refs = {(uri, 'unsupported') for uri in _URI.findall(plain)}
    plain = _URI.sub('', plain)
    refs.update((ref, 'plain') for ref in reference_inventory(plain.encode('utf-8')))
    for target in targets:
        ref = target.strip().split('#', 1)[0]
        if not ref:
            continue  # An in-document anchor introduces no file dependency.
        supported = (re.fullmatch(r'[^\s()<>]+', ref) is not None
            and '://' not in ref and not ref.startswith(('mailto:', 'data:')))
        refs.add((ref, 'markdown' if supported else 'unsupported'))
    return sorted(refs)


def _key(path):
    return os.path.normcase(os.path.normpath(str(path))).replace('\\', '/')


def _resolved_reference(ref, root, relative, kind):
    if kind == 'unsupported':
        return None
    if kind == 'markdown':
        try:
            ref = unquote(ref, errors='strict')
        except UnicodeError:
            return None
    normalized = ref.replace('\\', '/')
    path = Path(normalized)
    if path.is_absolute():
        return _key(path)  # Still requires one exact pre-existing grant below.
    parent = Path(root) / PurePosixPath(relative).parent
    # Plain repository-qualified resource names use their declaring root.
    # Markdown links always use the declaring document's directory.
    if kind == 'plain' and normalized.startswith(('skills/', 'agents/', '.claude/', '.agents/')):
        parent = Path(root)
    resolved = _key(parent / normalized)
    if not resolved.startswith(_key(root).rstrip('/') + '/'):
        return None
    return resolved


def dependency_evidence(capture, canonical_ref, before, after, *, candidate_root):
    canonical_ref = output_path(canonical_ref)
    roots = _references(before)
    if _references(after) != roots:
        raise ValueError('semantic_dependency_inventory_changed')
    candidate_root = Path(candidate_root)
    if not candidate_root.is_absolute():
        raise ValueError('semantic_dependency_root_invalid')
    granted = []
    for record in capture['sources']:
        if capture['version'] == 1:
            locator = {key: record[key] for key in ('root', 'relative')}
        elif record.get('kind') == 'file' and record.get('role') == 'protected' and isinstance(record.get('projection'), str):
            locator = record['locator']
        else:
            continue  # Metadata/discovery grants do not grant unprojected file bodies.
        if record['present']:
            granted.append((record, locator))
    nodes, edges, unresolved, active = {}, [], [], set()
    executable = False

    def visit(ref, root, relative, origin, kind):
        nonlocal executable
        if len(nodes) + len(unresolved) >= 1000:
            raise ValueError('semantic_dependency_graph_too_large')
        identity = _resolved_reference(ref, root, relative, kind)
        matches = [(record, locator) for record, locator in granted
            if identity is not None and _key(Path(locator['root']) / locator['relative']) == identity]
        if len(matches) != 1:
            unresolved.append({'reference': ref, 'from': origin, 'reason': 'dependency_ungranted_or_ambiguous'})
            return
        record, locator = matches[0]
        identifier = record['ref']
        edges.append({'from': origin, 'to': identifier})
        if identifier in active:
            unresolved.append({'reference': ref, 'from': origin, 'reason': 'dependency_cycle'})
            return
        if identifier in nodes:
            return
        # Import via module so the same real stable-reader boundary remains testable.
        import improvement_trial_context as context
        from improvement_trial_dashboard import _protected_authority
        root, relative = Path(locator['root']), output_path(locator['relative'])
        try:
            authority = _protected_authority(root, relative)
            observed, text = context._read(ContextSource(identifier, root, relative, record['required']))
            if (observed['hash'] != record['hash'] or observed['identity'] != record['identity']
                    or _protected_authority(root, relative) != authority or text is None):
                raise IM.ConflictError('semantic_dependency_source_changed')
        except (ValueError, OSError, PermissionError) as error:
            raise IM.ConflictError('semantic_dependency_source_changed') from error
        nodes[identifier] = {'ref': identifier, 'relative': relative, 'hash': observed['hash'],
            'identity': observed['identity'], 'authority_hash': IM._hash(authority), 'content': text}
        executable |= PurePosixPath(relative).suffix.lower() in _EXECUTABLE
        active.add(identifier)
        raw = text.encode('utf-8')
        for nested, nested_kind in _references(raw):
            visit(nested, root, relative, identifier, nested_kind)
        active.remove(identifier)

    for ref, kind in roots:
        visit(ref, candidate_root, canonical_ref, 'candidate', kind)
    return {'version': 1, 'verdict': 'unknown' if unresolved or executable else 'pass',
        'coverage': 'declared_reference_graph', 'runtime': 'unsupported_executable_dependency' if executable else 'not_qualified_by_this_check',
        'nodes': [nodes[key] for key in sorted(nodes)], 'edges': edges, 'unresolved': unresolved,
        'references': sorted({ref for ref, _ in roots}), 'manifest_hash': capture['plan_hash']}
