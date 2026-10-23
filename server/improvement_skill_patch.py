"""Pure byte-preserving body proposals; structural checks never certify semantics.

No file, permission, engine or source I/O occurs here. The owner staging service
must resolve provenance/authority and reject sensitive content independently.
"""
from __future__ import annotations

import hashlib
import re

import yaml
import fastyaml

import improvement_cases as IC
import improvement_store as IM
from improvement_blobs import MAX_ASSET_BYTES

_FIELDS = {"version", "target_id", "base_revision_id", "before_hash", "operation", "start", "end", "replacement",
           "hypothesis", "failure_signature", "expected_behavior", "possible_regressions", "evidence_refs", "angle"}
_OPEN = re.compile(rb"---[ \t]*\r?\n")
_CLOSE = re.compile(rb"---[ \t]*(?:\r?\n|$)")
_REFERENCES = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/][^\s`<>\"'()\[\]{}]+"
    r"|(?:\.{1,2}[\\/])*(?:\.claude[\\/]|\.agents[\\/])?(?:skills|agents|scripts|helpers|references|assets)[\\/][^\s`<>\"'()\[\]{}]+"
    r"|(?:[\w.-]+/)*[\w.-]+\.(?:py|sh|ps1|exe|bat|cmd|js|mjs|cjs)\b)", re.I)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(raw):
    if type(raw) is not bytes or len(raw) > MAX_ASSET_BYTES or b"\x00" in raw:
        raise ValueError("invalid_skill_bytes")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("invalid_skill_utf8") from error


def _header(raw):
    if len(raw) > 16384:
        raise ValueError("skill_frontmatter_too_large")
    try:
        text = raw.decode("utf-8", errors="strict")
        # Alias/merge/duplicate-key interpretation must not vary between parsers.
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken))
               for token in yaml.scan(text, Loader=yaml.SafeLoader)):
            raise ValueError("ambiguous_skill_layout")
        node = yaml.compose(text, Loader=yaml.SafeLoader)
        if node is None:
            return {}
        if not isinstance(node, yaml.nodes.MappingNode):
            raise ValueError("ambiguous_skill_layout")
        keys = []
        for key, _ in node.value:
            if not isinstance(key, yaml.nodes.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                raise ValueError("ambiguous_skill_layout")
            keys.append(key.value)
        if len(keys) != len(set(keys)):
            raise ValueError("ambiguous_skill_layout")
        return fastyaml.safe_load(text) or {}
    except (yaml.YAMLError, UnicodeError, RecursionError) as error:
        raise ValueError("ambiguous_skill_layout") from error


def _implicit_description(body):
    # Match ordinary discovery's stripped first line, accounting for text-mode
    # newline conversion, but do not normalize any bytes in the resulting file.
    text = body.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text.split("\n", 1)[0] if text else ""


def body_layout(base: bytes) -> dict:
    """Identify immutable prefix and an editable body without reconstructing YAML.

    BOM plain text is preserved. BOM followed by YAML is ambiguous in current
    discovery (which does not recognize that header), so it is rejected.
    """
    _raw(base)
    start = 3 if base.startswith(b"\xef\xbb\xbf") else 0
    if start and base[start:].startswith(b"---"):
        raise ValueError("ambiguous_skill_layout")
    metadata, header = {}, b""
    opening = _OPEN.match(base)
    if opening:
        position, closed = opening.end(), False
        for line in base[position:].splitlines(keepends=True):
            if line.startswith(b"---"):
                if not _CLOSE.fullmatch(line):
                    raise ValueError("ambiguous_skill_layout")
                header = base[opening.end():position]
                metadata, start, closed = _header(header), position + len(line), True
                break
            position += len(line)
            if position > 16384 + opening.end():
                raise ValueError("skill_frontmatter_too_large")
        if not closed:
            raise ValueError("ambiguous_skill_layout")
    elif base in (b"---", b"---\r"):
        raise ValueError("ambiguous_skill_layout")
    description = metadata.get("description")
    explicit_description = isinstance(description, str) and bool(description.strip())
    implicit = None if explicit_description else _implicit_description(base[start:])
    editable = start
    if implicit is not None:
        # Freeze leading whitespace and the entire first nonempty routing line.
        text = base[start:].decode("utf-8")
        offset = len(text) - len(text.lstrip())
        prefix = start + len(text[:offset].encode("utf-8"))
        newline = base.find(b"\n", prefix)
        editable = len(base) if newline < 0 else newline + 1
    return {"body_start": start, "editable_start": editable, "prefix_hash": _digest(base[:start]),
            "frontmatter_hash": _digest(header), "implicit_description": implicit}


def _boundary(base, offset):
    if type(offset) is not int or not 0 <= offset <= len(base):
        raise ValueError("invalid_candidate_region")
    try:
        base[:offset].decode("utf-8", errors="strict")
        base[offset:].decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("invalid_candidate_region") from error


def reference_inventory(raw):
    """Conservative lexical references only; never a complete dependency proof."""
    text = _raw(raw)
    return sorted({match.group(0).rstrip(".,;:") for match in _REFERENCES.finditer(text)})


def validate_body_patch(base: bytes, patch: dict, region: dict | None) -> dict:
    layout = body_layout(base)
    IC._fields(patch, _FIELDS)
    if (type(patch["version"]) is not int or patch["version"] != 1
            or patch["operation"] != "replace_body_region" or patch["angle"] not in ("execution", "domain_process")):
        raise ValueError("unsupported_candidate_patch")
    for field in ("target_id", "base_revision_id"):
        if type(patch[field]) is not str or not IC._ID.fullmatch(patch[field]):
            raise ValueError("invalid_candidate_identifier")
    IC._digest(patch["before_hash"])
    if patch["before_hash"] != _digest(base):
        raise ValueError("candidate_base_changed")
    start, end = patch["start"], patch["end"]
    _boundary(base, start)
    _boundary(base, end)
    if end < start or start < layout["editable_start"]:
        raise ValueError("candidate_protected_region")
    replacement = patch["replacement"]
    if type(replacement) is not str or "\x00" in replacement:
        raise ValueError("invalid_candidate_replacement")
    try:
        replacement = replacement.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("invalid_candidate_utf8") from error
    if len(replacement) > MAX_ASSET_BYTES or len(base) - (end - start) + len(replacement) > MAX_ASSET_BYTES:
        raise ValueError("candidate_too_large")
    for field in ("hypothesis", "failure_signature", "expected_behavior"):
        IC._text(patch[field], 4000)
    regressions = patch["possible_regressions"]
    if type(regressions) is not list or not 1 <= len(regressions) <= 16:
        raise ValueError("invalid_candidate_regressions")
    for value in regressions:
        IC._text(value, 2000)
    refs = patch["evidence_refs"]
    if type(refs) is not list or len(refs) > 32:
        raise ValueError("invalid_candidate_evidence")
    for ref in refs:
        IC._fields(ref, {"kind", "id"})
        if ref["kind"] not in ("owner_request", "tuning_trial") or type(ref["id"]) is not str or not IC._ID.fullmatch(ref["id"]):
            raise ValueError("invalid_candidate_evidence")
    if region is not None:
        IC._fields(region, {"start", "end", "before_hash"})
        _boundary(base, region["start"])
        _boundary(base, region["end"])
        if (region["before_hash"] != patch["before_hash"] or region["start"] < layout["editable_start"]
                or not region["start"] <= start <= end <= region["end"]):
            raise ValueError("candidate_outside_owner_region")
    content = base[:start] + replacement + base[end:]
    after = body_layout(content)
    if any(layout[field] != after[field] for field in ("body_start", "prefix_hash", "frontmatter_hash", "implicit_description")):
        raise ValueError("candidate_routing_changed")
    inventory = reference_inventory(base)
    if reference_inventory(content) != inventory:
        raise ValueError("candidate_dependency_changed")
    return {"content": content, "content_hash": _digest(content), "byte_size": len(content),
            "patch_hash": IM._hash(patch), "keep_base": content == base,
            "edit_status": "within_owner_region" if region is not None else "awaiting_exact_diff",
            "semantic_status": "unreviewed", "reference_inventory": inventory, "dependency_status": "unresolved",
            "body_start": layout["body_start"], "prefix_hash": layout["prefix_hash"], "frontmatter_hash": layout["frontmatter_hash"]}
