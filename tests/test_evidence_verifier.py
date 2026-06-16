"""Tests for artifact evidence verification."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.evidence_verifier import verify_artifact_evidence


def _messages():
    return [
        {'id': 1, 'msg_id': 1, 'sender_name': '张三', 'content': '这个可以做成SOP', 'create_time': 1},
        {'id': 2, 'msg_id': 2, 'sender_name': '李四', 'content': 'MiniMax 超时需要调大 timeout', 'create_time': 2},
    ]


def test_verify_passes_matching_msg_sender_quote():
    artifact = {'topics': [{'evidence': [
        {'msg_id': 1, 'sender': '张三', 'quote': '可以做成SOP'}
    ]}]}
    verify = verify_artifact_evidence(artifact, _messages())
    assert verify['status'] == 'verified'
    assert verify['passed'] == 1
    assert verify['failed'] == 0


def test_verify_warns_missing_msg_id():
    artifact = {'topics': [{'evidence': [
        {'msg_id': 999, 'sender': '张三', 'quote': '可以做成SOP'}
    ]}]}
    verify = verify_artifact_evidence(artifact, _messages())
    assert verify['status'] == 'unverified'
    assert verify['warnings'][0]['reason'] == 'msg_id_not_found'


def test_verify_warns_sender_mismatch():
    artifact = {'topics': [{'evidence': [
        {'msg_id': 1, 'sender': '李四', 'quote': '可以做成SOP'}
    ]}]}
    verify = verify_artifact_evidence(artifact, _messages())
    assert verify['status'] == 'unverified'
    assert verify['warnings'][0]['reason'] == 'sender_mismatch'


def test_verify_warns_quote_not_found():
    artifact = {'topics': [{'evidence': [
        {'msg_id': 1, 'sender': '张三', 'quote': '不存在的引用'}
    ]}]}
    verify = verify_artifact_evidence(artifact, _messages())
    assert verify['status'] == 'unverified'
    assert verify['warnings'][0]['reason'] == 'quote_not_found'


def test_empty_evidence_is_unverified_not_error():
    verify = verify_artifact_evidence({'topics': []}, _messages())
    assert verify['status'] == 'unverified'
    assert verify['total_evidence'] == 0
