"""Deterministic evidence verification for analysis artifacts."""
from __future__ import annotations

import re


def _msg_id(msg):
    return msg.get('id') if msg.get('id') is not None else msg.get('msg_id')


def _normalize_text(text: str) -> str:
    return re.sub(r'\s+', '', text or '')


def build_message_index(messages: list) -> dict:
    """Build a lookup table keyed by both int and string msg_id."""
    index = {}
    for msg in messages or []:
        mid = _msg_id(msg)
        if mid is None:
            continue
        index[mid] = msg
        index[str(mid)] = msg
    return index


def _warn(path: str, reason: str, evidence: dict) -> dict:
    return {
        'path': path,
        'reason': reason,
        'msg_id': evidence.get('msg_id'),
        'sender': evidence.get('sender') or '',
        'quote': evidence.get('quote') or '',
    }


def verify_artifact_evidence(artifact: dict, messages: list) -> dict:
    """Verify artifact evidence against source messages.

    This is a soft gate: it reports warnings and writes verified flags, but it
    does not remove evidence or reject the artifact.
    """
    index = build_message_index(messages)
    total = 0
    passed = 0
    warnings = []

    for topic_idx, topic in enumerate(artifact.get('topics') or []):
        for ev_idx, evidence in enumerate(topic.get('evidence') or []):
            if not isinstance(evidence, dict):
                continue
            total += 1
            path = f'topics[{topic_idx}].evidence[{ev_idx}]'
            msg = index.get(evidence.get('msg_id')) or index.get(str(evidence.get('msg_id')))
            if not msg:
                evidence['verified'] = False
                warnings.append(_warn(path, 'msg_id_not_found', evidence))
                continue

            expected_sender = (evidence.get('sender') or '').strip()
            actual_sender = (msg.get('sender_name') or msg.get('sender') or '').strip()
            if expected_sender and actual_sender and expected_sender != actual_sender:
                evidence['verified'] = False
                warnings.append(_warn(path, 'sender_mismatch', evidence))
                continue

            quote = _normalize_text(evidence.get('quote') or '')
            content = _normalize_text(msg.get('content') or '')
            if quote and quote not in content:
                evidence['verified'] = False
                warnings.append(_warn(path, 'quote_not_found', evidence))
                continue

            evidence['verified'] = True
            passed += 1

    failed = total - passed
    if total == 0:
        status = 'unverified'
    elif failed == 0:
        status = 'verified'
    elif passed > 0:
        status = 'partial'
    else:
        status = 'unverified'

    return {
        'status': status,
        'total_evidence': total,
        'passed': passed,
        'failed': failed,
        'warnings': warnings,
    }
