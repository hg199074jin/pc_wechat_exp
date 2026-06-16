"""Tests for ai_analyzer message formatting."""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from engine.services.ai_analyzer import (
    format_messages_for_llm, build_prompt, sanitize_analysis_markdown, MAX_MSG_PER_GROUP,
)


def test_format_text_messages():
    """Text messages should be formatted as [HH:MM] name: content."""
    messages = [
        {
            'create_time': int(datetime(2026, 6, 14, 9, 15).timestamp()),
            'msg_type': 1,
            'is_sender': False,
            'sender_name': '张三',
            'content': '大家好',
        },
        {
            'create_time': int(datetime(2026, 6, 14, 9, 16).timestamp()),
            'msg_type': 1,
            'is_sender': True,
            'sender_name': '我',
            'content': '早安',
        },
    ]
    result = format_messages_for_llm(messages)
    assert '[09:15] 张三: 大家好' in result
    assert '[09:16] 我: 早安' in result


def test_format_link_messages():
    """Link messages (type 49) should include title from xml_parsed."""
    messages = [
        {
            'create_time': int(datetime(2026, 6, 14, 10, 0).timestamp()),
            'msg_type': 49,
            'is_sender': False,
            'sender_name': '李四',
            'content': '',
            'xml_parsed': {'title': 'Q3预算规划', 'des': '讨论今年第三季度的预算分配', 'url': 'https://docs.qq.com/xxx'},
        },
    ]
    result = format_messages_for_llm(messages)
    assert '[10:00] 李四' in result
    assert '[链接] Q3预算规划 - 讨论今年第三季度的预算分配' in result


def test_format_skips_image_voice_file():
    """Image (type 3), voice (34), file (6) should be skipped."""
    messages = [
        {'create_time': 1000, 'msg_type': 3, 'is_sender': False, 'sender_name': 'A', 'content': ''},
        {'create_time': 1000, 'msg_type': 34, 'is_sender': False, 'sender_name': 'A', 'content': ''},
        {'create_time': 1000, 'msg_type': 6, 'is_sender': False, 'sender_name': 'A', 'content': ''},
        {'create_time': 1000, 'msg_type': 1, 'is_sender': False, 'sender_name': 'A', 'content': '有效消息'},
    ]
    result = format_messages_for_llm(messages)
    # Only 1 text message, so 0 newlines (single line)
    assert 'A: 有效消息' in result
    assert '图片' not in result
    assert '语音' not in result
    assert '文件' not in result


def test_format_truncates_at_limit():
    """Messages over MAX_MSG_PER_GROUP should be truncated with notice."""
    messages = [
        {
            'create_time': int(datetime(2026, 6, 14, 9, i % 60).timestamp()) + (i // 60) * 86400,
            'msg_type': 1,
            'is_sender': False,
            'sender_name': f'用户{i}',
            'content': f'消息{i}',
        }
        for i in range(MAX_MSG_PER_GROUP + 100)
    ]
    result = format_messages_for_llm(messages)
    assert '已截断' in result
    lines = [l for l in result.split('\n') if l.startswith('[')]
    assert len(lines) == MAX_MSG_PER_GROUP


def test_build_prompt_includes_group_and_date():
    """Prompt should include group name and date in user section."""
    system, user = build_prompt(
        group_name='技术讨论群',
        date='2026-06-14',
        msg_count=42,
        formatted_messages='[09:15] A: hi',
    )
    assert '技术讨论群' in user
    assert '2026-06-14' in user
    assert '42' in user
    assert 'Markdown' in system
    assert '总体摘要' in system
    assert '[09:15] A: hi' in user


def test_sanitize_analysis_markdown_removes_model_preamble():
    raw = '''Let me analyze this group chat from "测试群" dated 2026-06-15.
Let me identify the main topics:

AI工具分享 - 讨论了工具安装
Let me structure this into a report.

群聊分析报告 测试群 (2026-06-15)

## 总体摘要
大家主要讨论了 AI 工具安装和使用问题。
'''
    cleaned = sanitize_analysis_markdown(raw, '测试群')
    assert cleaned.startswith('# 群聊分析报告')
    assert 'Let me' not in cleaned
    assert 'AI工具分享 - 讨论了工具安装' not in cleaned
    assert '## 总体摘要' in cleaned
