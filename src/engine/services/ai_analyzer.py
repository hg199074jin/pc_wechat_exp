"""AI 群聊分析核心服务.

读取已解密的 WeChat 数据库, 提取文字+链接消息, 拼装 prompt 调 OpenAI 兼容 API,
返回 Markdown 格式的分析结果. 结果以 Markdown 文件存盘.
"""
from __future__ import annotations

import json as _json
import os
import re
import uuid as _uuid
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

from engine.services.analysis_artifact import (
    normalize_artifact,
    parse_artifact_json,
    render_markdown_report,
    save_artifact,
)
from engine.services.evidence_verifier import verify_artifact_evidence
from engine.services.message import query_messages
from engine.services.name_resolver import resolve_wxid
from engine.services.style_fingerprint import ensure_style, style_to_prompt

# Maximum messages per group per day — 超过则截断到最近 N 条
MAX_MSG_PER_GROUP = 3000
DEFAULT_LLM_TIMEOUT = 120
ARTIFACT_CHUNK_CHAR_THRESHOLD = 12000
ARTIFACT_CHUNK_CHAR_SIZE = 6000
MAX_ARTIFACT_CHUNKS = 8

# 链接消息类型 (微信 local_type=49)
_LINK_TYPE = 49
# 文字消息类型
_TEXT_TYPE = 1

# 排除不需要通过 query_messages 过滤的图片/语音等
_ANALYZE_TYPES = '1,49'

_SYSTEM_PROMPT = """你是一个专业的群聊分析助手，擅长把零散聊天记录整理成可直接阅读的中文 Markdown 报告。

硬性要求：
1. 只输出最终报告正文，不输出分析过程、计划、思考、解释或过渡语。
2. 禁止出现英文元话语，例如 "Let me analyze"、"Let me identify"、"Here is"、"I will"。
3. 使用简体中文，语气客观、简洁、像审计/咨询工作底稿摘要。
4. 不要编造聊天记录中没有的信息；原文引用必须来自输入消息。

报告格式：
# 群聊分析报告：{群聊名称}

## 总体摘要
用 2-3 句话概括当天最重要的讨论、结论和风险/价值点。

## 关键话题
按重要性列出最多 8 个话题。每个话题使用三级标题：
### 1. 话题标题
- 核心内容：3-5 句话，归纳讨论脉络和结论。
- 关键发言人：列出参与讨论或提供关键信息的人。
- 代表原文：
  > 选择 1-2 条最能支撑结论的原文。

## 待跟进事项
如聊天中出现明确问题、任务、工具安装障碍、资源请求或决策点，列出待跟进事项；没有则写“无明确待跟进事项”。"""

_META_LINE_RE = re.compile(
    r'^\s*(let me|i will|i\'ll|here is|here\'s|now i|based on|this group chat|'
    r'i need to|let\'s|first,? i|the main topics|i can|i should)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _format_single_msg(msg: Dict) -> Optional[str]:
    """Format one message into "[HH:MM] name: content" line, or None to skip."""
    ts = msg.get('create_time')
    if not ts:
        return None
    try:
        time_str = datetime.fromtimestamp(int(ts)).strftime('%H:%M')
    except (ValueError, TypeError, OSError):
        return None

    msg_type = msg.get('msg_type', 0)
    name = msg.get('sender_name') or '未知'
    content = (msg.get('content') or '').strip()

    if msg_type == _TEXT_TYPE:
        if not content:
            return None
        return f'[{time_str}] {name}: {content}'

    if msg_type == _LINK_TYPE:
        xml = msg.get('xml_parsed') or {}
        title = (xml.get('title') or '').strip()
        des = (xml.get('des') or '').strip()
        if title and des:
            text = f'[链接] {title} - {des}'
        elif title:
            text = f'[链接] {title}'
        elif content:
            text = f'[链接] {content}'
        else:
            return None
        return f'[{time_str}] {name}: {text}'

    return None


def format_messages_for_llm(messages: List[Dict]) -> str:
    """Format message list for LLM input. Truncates at MAX_MSG_PER_GROUP."""
    sorted_msgs = sorted(messages, key=lambda m: m.get('create_time') or 0)

    lines = []
    for msg in sorted_msgs:
        line = _format_single_msg(msg)
        if line:
            lines.append(line)

    total = len(lines)
    if total > MAX_MSG_PER_GROUP:
        lines = lines[:MAX_MSG_PER_GROUP]
        lines.append(f'\n(共 {total} 条消息, 已截断到最近 {MAX_MSG_PER_GROUP} 条)')

    return '\n'.join(lines)


def format_messages_for_artifact(messages: List[Dict]) -> str:
    """Format messages with msg_id for artifact extraction."""
    sorted_msgs = sorted(messages, key=lambda m: m.get('create_time') or 0)
    lines = []
    for msg in sorted_msgs:
        line = _format_single_msg(msg)
        if not line:
            continue
        msg_id = msg.get('id') if msg.get('id') is not None else msg.get('msg_id')
        if msg_id is None:
            msg_id = ''
        lines.append(f'[msg_id={msg_id}] {line}')

    total = len(lines)
    if total > MAX_MSG_PER_GROUP:
        lines = lines[:MAX_MSG_PER_GROUP]
        lines.append(f'\n(共 {total} 条消息, 已截断到最近 {MAX_MSG_PER_GROUP} 条)')
    return '\n'.join(lines)


def _compute_message_stats(messages: list, formatted: str) -> dict:
    """Compute deterministic stats for artifact prompts."""
    senders = set()
    formatted_count = 0
    for msg in messages or []:
        if _format_single_msg(msg):
            formatted_count += 1
            sender = msg.get('sender_name') or msg.get('sender')
            if sender:
                senders.add(sender)
    return {
        'message_count': len(messages or []),
        'formatted_count': formatted_count,
        'unique_senders': len(senders),
        'total_chars': len(formatted or ''),
        'chunked': False,
        'chunks': 0,
        'truncated': formatted_count > MAX_MSG_PER_GROUP,
    }


def _chunk_text_by_line(text: str, chunk_size: int = ARTIFACT_CHUNK_CHAR_SIZE) -> list:
    """Split formatted text by line without splitting a message line."""
    chunks, cur, cur_len = [], [], 0
    for line in (text or '').splitlines():
        line_len = len(line) + 1
        if cur and cur_len + line_len > chunk_size:
            chunks.append('\n'.join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += line_len
    if cur:
        chunks.append('\n'.join(cur))
    return chunks


def build_artifact_prompt(group_name: str, date: str, stats: dict,
                          formatted_messages: str,
                          style_prompt: str = '',
                          partial: bool = False) -> Tuple[str, str]:
    """Build prompt that asks the LLM to return a strict analysis artifact JSON."""
    phase = '这是分块消息，请只分析本块内容。' if partial else '这是完整消息，请输出完整分析。'
    system = f"""你是一个专业的群聊知识分析助手。你的任务不是直接写 Markdown，而是输出可机器解析的 JSON artifact。

硬性要求：
1. 只输出 JSON，不要 Markdown，不要解释，不要 ``` 包裹。
2. evidence.msg_id 必须来自输入中的 [msg_id=数字]。
3. evidence.quote 必须逐字摘自原始消息，禁止改写、翻译、概括。
4. evidence.sender 必须是该消息的真实发送者。
5. 每个话题最多给 3 条 evidence。
6. knowledge_candidates 只收录真正值得长期沉淀的知识；没有就返回空数组，不要硬编。
7. 每个 knowledge candidate 必须说明 why_valuable，并用 source_msg_ids 绑定原始消息。
8. 使用简体中文，避免英文元话语。

{style_prompt}

输出 JSON Schema：
{{
  "summary": "2-3句话总体摘要",
  "topics": [
    {{
      "id": "topic_1",
      "title": "话题标题",
      "summary": "话题摘要",
      "participants": ["发言人"],
      "evidence": [
        {{"msg_id": 123, "time": "10:32", "sender": "张三", "quote": "原始消息摘录"}}
      ],
      "knowledge_candidates": [
        {{
          "title": "知识标题",
          "type": "audit_case|sop|prompt|faq|article|tool|risk|methodology|note",
          "score": 0,
          "summary": "摘要",
          "why_valuable": "为什么值得沉淀",
          "content_md": "结构化正文",
          "tags": ["标签"],
          "source_msg_ids": [123]
        }}
      ]
    }}
  ],
  "followups": [
    {{"title": "待跟进事项", "owner": "", "evidence_msg_ids": [123]}}
  ]
}}
"""
    user = (
        f'群聊名称: {group_name}\n'
        f'日期: {date}\n'
        f'消息数: {stats.get("message_count", 0)} / 可分析消息: {stats.get("formatted_count", 0)} / '
        f'发言人: {stats.get("unique_senders", 0)} / 总字数: {stats.get("total_chars", 0)}\n'
        f'{phase}\n\n'
        f'消息记录:\n{formatted_messages}'
    )
    return system, user


def build_prompt(group_name: str, date: str, msg_count: int,
                 formatted_messages: str) -> Tuple[str, str]:
    """Build (system_prompt, user_prompt) tuple for chat completions."""
    user_prompt = (
        f'群聊名称: {group_name}\n'
        f'日期: {date}\n'
        f'消息数量: {msg_count} 条\n\n'
        f'请严格按系统消息中的 Markdown 格式输出。直接从 "# 群聊分析报告：{group_name}" 开始，'
        f'不要输出任何英文分析过程、思考步骤或格式说明。\n\n'
        f'---\n'
        f'{formatted_messages}'
    )
    return _SYSTEM_PROMPT, user_prompt


def sanitize_analysis_markdown(markdown: str, group_name: str = None) -> str:
    """Remove model preambles/thinking traces from generated Markdown."""
    text = (markdown or '').strip()
    if not text:
        return ''

    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    report_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('#') or '群聊分析报告' in stripped or stripped in ('总体摘要', '## 总体摘要'):
            report_start = i
            break

    if report_start and report_start > 0:
        lines = lines[report_start:]

    cleaned = []
    for line in lines:
        stripped = line.strip()
        if _META_LINE_RE.match(stripped):
            continue
        if stripped.lower().startswith(('let me ', "let's ", 'i will ', "i'll ")):
            continue
        cleaned.append(line)

    # Collapse excessive blank lines.
    compact = []
    blank = False
    for line in cleaned:
        if not line.strip():
            if not blank:
                compact.append('')
            blank = True
        else:
            compact.append(line)
            blank = False

    result = '\n'.join(compact).strip()
    if result.startswith('群聊分析报告'):
        result = '# ' + result
    elif group_name and not result.startswith('#') and '群聊分析报告' not in result[:80]:
        result = f'# 群聊分析报告：{group_name}\n\n{result}'
    return result


# ---------------------------------------------------------------------------
# LLM API call
# ---------------------------------------------------------------------------

def call_llm(system: str, user: str, base_url: str, api_key: str,
             model: str, temperature: float, max_tokens: int,
             timeout: int = 30, proxy: str = 'auto') -> str:
    """Call an OpenAI-compatible chat completion API. Returns message content.

    Retries on transient network errors (timeout, connection reset, proxy
    errors, 5xx) up to 3 times with a short backoff. Raises on HTTP error or
    after final failure.

    ``proxy`` controls outbound proxy behaviour:
    - ``'auto'`` (default): honour system proxy env vars (requests default).
    - ``'none'``: bypass all proxy env vars (pass empty proxies), suited for
      domestic/Chinese LLM endpoints (MiniMax/智谱) that should not be proxied.
    - any other string: use it as an explicit proxy URL.
    """
    import requests

    url = f'{base_url.rstrip("/")}/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = {
        'model': model,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
    }

    # Resolve proxy preference into a requests-compatible proxies mapping.
    # None means "do not override" (use env); an explicit dict overrides env.
    proxies = None
    if proxy == 'none':
        # Empty values bypass env-var proxies in requests.
        proxies = {'http': None, 'https': None}
    elif proxy and proxy != 'auto':
        proxies = {'http': proxy, 'https': proxy}

    import time as _time

    last_err = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            resp = requests.post(
                url, headers=headers, json=body, timeout=timeout, proxies=proxies,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data['choices'][0]['message']['content']
            if 500 <= resp.status_code < 600 and attempt < max_attempts - 1:
                last_err = RuntimeError(
                    f'LLM API error {resp.status_code}: {resp.text[:300]}')
                _time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f'LLM API error {resp.status_code}: {resp.text[:300]}')
        except requests.Timeout as e:
            last_err = e
            if attempt < max_attempts - 1:
                _time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f'LLM request timed out {max_attempts} times: {e}')
        except (requests.ConnectionError, requests.exceptions.ProxyError) as e:
            # ProxyError / RemoteDisconnected / connection reset are transient.
            last_err = e
            if attempt < max_attempts - 1:
                _time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f'LLM request failed after {max_attempts} retries: {e}')
        except requests.RequestException as e:
            last_err = e
            raise RuntimeError(f'LLM request failed: {e}')

    raise RuntimeError(f'LLM request failed after {max_attempts} retries: {last_err}')


def _llm_timeout(cfg: dict, default: int = DEFAULT_LLM_TIMEOUT) -> int:
    """Return a bounded LLM timeout in seconds."""
    try:
        value = int(cfg.get('timeout') or default)
    except (TypeError, ValueError):
        value = default
    return max(15, min(600, value))


def _safe_debug_part(value: str, max_len: int = 80) -> str:
    """Return a filesystem-safe, readable filename segment."""
    text = str(value or '').strip()
    text = ''.join(c if c.isalnum() or c in '-_@.' else '_' for c in text)
    text = re.sub(r'_+', '_', text).strip('._')
    return (text or 'unknown')[:max_len]


def _json_error_snapshot(error: Exception, text: str, radius: int = 180) -> dict:
    """Build a compact JSON parse error snapshot with nearby output text."""
    pos = getattr(error, 'pos', None)
    snapshot = {
        'type': error.__class__.__name__,
        'message': str(error),
        'lineno': getattr(error, 'lineno', None),
        'colno': getattr(error, 'colno', None),
        'pos': pos,
    }
    if isinstance(pos, int):
        source = text or ''
        start = max(0, pos - radius)
        end = min(len(source), pos + radius)
        excerpt = source[start:end]
        pointer_offset = pos - start
        snapshot['context'] = excerpt
        snapshot['pointer'] = ' ' * max(pointer_offset, 0) + '^'
        snapshot['context_start'] = start
        snapshot['context_end'] = end
    return snapshot


def _write_artifact_json_debug(cfg: dict, meta: dict, raw: str,
                               preprocessed: str, first_error: Exception,
                               repair_rounds: list, final_error: Exception) -> str:
    """Persist failed LLM JSON output for local debugging.

    The file stays local under ai_analysis/debug and is only written when all
    parse/repair attempts fail.
    """
    debug_dir = cfg.get('_debug_dir')
    if not debug_dir:
        return ''
    os.makedirs(debug_dir, exist_ok=True)
    context = meta or {}
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    group_part = _safe_debug_part(context.get('group_name') or 'group')
    date_part = _safe_debug_part(context.get('date') or 'date')
    phase_part = _safe_debug_part(context.get('phase') or 'artifact')
    chunk_part = ''
    if context.get('chunk_index') is not None:
        chunk_part = f"_chunk{context.get('chunk_index')}"
    filename = f'{stamp}_{group_part}_{date_part}_{phase_part}{chunk_part}.json'
    path = os.path.join(debug_dir, filename)
    payload = {
        'created_at': created_at,
        'context': context,
        'first_error': _json_error_snapshot(first_error, preprocessed),
        'final_error': _json_error_snapshot(final_error, repair_rounds[-1].get('preprocessed_output', preprocessed) if repair_rounds else preprocessed),
        'raw_output': raw or '',
        'preprocessed_output': preprocessed or '',
        'repair_rounds': repair_rounds,
    }
    with open(path, 'w', encoding='utf-8') as f:
        _json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _get_config(config_path: str) -> dict:
    """Read full config from disk. Returns empty dict if not present."""
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return _json.load(f)
    except (_json.JSONDecodeError, OSError):
        return {}


def _mask_api_key(api_key: str) -> str:
    """Show first 4 and last 4 chars, mask middle."""
    if not api_key:
        return ''
    if len(api_key) <= 8:
        return api_key
    return f'{api_key[:4]}{"*" * (len(api_key) - 8)}{api_key[-4:]}'


def load_llm_config(config_path: str) -> dict:
    """Read private LLM config; falls back to legacy analysis config."""
    try:
        from engine.config_file import get_llm_config
        cfg = get_llm_config()
        if cfg:
            return cfg
    except Exception:
        pass
    cfg = _get_config(config_path)
    return cfg.get('llm', {})


def load_llm_config_masked(config_path: str) -> dict:
    """Like load_llm_config but with api_key masked."""
    cfg = load_llm_config(config_path)
    if 'api_key' in cfg:
        cfg['api_key'] = _mask_api_key(cfg['api_key'])
    return cfg


def save_llm_config(llm_cfg: dict, config_path: str) -> None:
    """Save LLM config to ignored local config, not the backup config."""
    try:
        from engine.config_file import set_llm_config
        set_llm_config(llm_cfg)
        return
    except Exception:
        pass
    full = _get_config(config_path)
    full['llm'] = llm_cfg
    os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(full, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------

def list_schedules(config_path: str) -> list:
    """List all schedules from config."""
    return _get_config(config_path).get('schedules', [])


def add_schedule(schedule: dict, config_path: str) -> str:
    """Add a schedule, return its new id."""
    full = _get_config(config_path)
    schedules = full.setdefault('schedules', [])
    schedule_id = str(_uuid.uuid4())
    schedules.append({**schedule, 'id': schedule_id})
    os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(full, f, ensure_ascii=False, indent=2)
    return schedule_id


def update_schedule(schedule_id: str, updates: dict, config_path: str) -> bool:
    """Update a schedule by id. Returns True if found."""
    full = _get_config(config_path)
    schedules = full.get('schedules', [])
    for s in schedules:
        if s.get('id') == schedule_id:
            s.update(updates)
            break
    else:
        return False
    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(full, f, ensure_ascii=False, indent=2)
    return True


def delete_schedule(schedule_id: str, config_path: str) -> bool:
    """Delete a schedule by id. Returns True if found."""
    full = _get_config(config_path)
    schedules = full.get('schedules', [])
    new_schedules = [s for s in schedules if s.get('id') != schedule_id]
    if len(new_schedules) == len(schedules):
        return False
    full['schedules'] = new_schedules
    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(full, f, ensure_ascii=False, indent=2)
    return True


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------

def _safe_date(date: str) -> str:
    """Sanitise a date label for use in a filename.

    Only digits, hyphens, underscores and the literal '_to_' range separator
    are kept; anything else (notably path separators ``/`` and ``\\`` that
    enable traversal on Windows) is replaced with ``_``.
    """
    text = str(date or '')
    return re.sub(r'[^0-9_a-zA-Z-]', '_', text) or 'unknown_date'


def result_path(storage_dir: str, chat_id: str, date: str) -> str:
    """Return absolute path to the Markdown file for (chat_id, date)."""
    safe_id = ''.join(c if c.isalnum() or c in '_@' else '_' for c in chat_id)
    return os.path.join(storage_dir, safe_id, f'{_safe_date(date)}.md')


def _atomic_write_text(path: str, text: str) -> None:
    """Write text to ``path`` atomically (tmp file + os.replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def period_label(start_date: str, end_date: str) -> str:
    """Return a filesystem/URL-safe label for an analysis date range."""
    if not end_date or start_date == end_date:
        return start_date
    return f'{start_date}_to_{end_date}'


def storage_dir_for(decrypted_dir: str) -> str:
    """Get the AI analysis storage directory for a given decrypted data root."""
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis')


def config_path_for(decrypted_dir: str) -> str:
    """Get the LLM config path for a given decrypted data root."""
    return os.path.join(storage_dir_for(decrypted_dir), 'config.json')


def _call_artifact_llm(group_name: str, date: str, stats: dict,
                       formatted: str, style_prompt: str,
                       cfg: dict, *, partial: bool = False,
                       chunk_index: int = None) -> dict:
    """Call LLM and parse a normalized artifact."""
    system, user = build_artifact_prompt(
        group_name, date, stats, formatted, style_prompt=style_prompt, partial=partial
    )
    raw = call_llm(
        system=system, user=user,
        base_url=cfg['base_url'],
        api_key=cfg['api_key'],
        model=cfg['model'],
        temperature=cfg.get('temperature', 0.2),
        max_tokens=cfg.get('max_tokens', 4096),
        timeout=_llm_timeout(cfg),
        proxy=cfg.get('proxy', 'auto'),
    )
    debug_context = {
        'group_name': group_name,
        'date': date,
        'phase': 'partial_artifact' if partial else 'artifact',
        'chunk_index': chunk_index,
        'message_count': stats.get('message_count'),
        'total_chars': stats.get('total_chars'),
    }
    try:
        return _parse_artifact_json_with_repair(raw, cfg, debug_context=debug_context)
    except Exception as e:
        raise ValueError(f'LLM 输出不是合法 JSON: {e}')


def _preprocess_llm_json(raw: str) -> str:
    """Best-effort cleanup of common LLM JSON mistakes before strict parsing.

    Removes ``//`` line comments, ``/* */`` block comments, and trailing
    commas that LLMs frequently emit but strict JSON rejects. Does not attempt
    to fix structural problems — those are left to the repair step.
    """
    text = _strip_reasoning_blocks(raw)
    # Strip /* block comments */.
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    # Strip // line comments (only when not inside a string — cheap heuristic:
    # only remove when the rest of the line does not look quoted). We keep it
    # simple and remove //... to end of line, which is the common LLM pattern.
    text = re.sub(r'(^|[^:])//.*$', lambda m: m.group(1), text, flags=re.MULTILINE)
    # Remove trailing commas before } or ].
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _strip_reasoning_blocks(raw: str) -> str:
    """Remove model reasoning wrappers while preserving any final answer."""
    text = raw or ''
    text = re.sub(r'<think>.*?</think>\s*', '', text,
                  flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _is_reasoning_only_response(raw: str) -> bool:
    """Return whether a model responded with thought text but no final JSON."""
    text = (raw or '').strip()
    if not re.search(r'<think>', text, flags=re.IGNORECASE):
        return False
    return not _strip_reasoning_blocks(text)


def _parse_artifact_json_with_repair(raw: str, cfg: dict,
                                     debug_context: dict = None) -> dict:
    """Parse artifact JSON with regex preprocessing and LLM repair fallback.

    Pipeline: regex preprocess -> strict parse -> up to 2 LLM repair rounds.
    Each repair round feeds the previous error back so the model can target it.
    """
    preprocessed = _preprocess_llm_json(raw)
    try:
        return parse_artifact_json(preprocessed)
    except Exception as first_error:
        error = first_error
        current = preprocessed
        repair_rounds = []
        max_repairs = 2
        for attempt in range(max_repairs):
            system = (
                '你是严格的 JSON 修复器。只输出修复后的合法 JSON 对象，'
                '不要输出 Markdown、```代码块、解释或任何前后缀文字。'
                '硬性要求：'
                '1. 保持原有字段名和字段值完全不变，只修复 JSON 语法错误；'
                '2. 所有键名和字符串值必须用双引号；'
                '3. 去除尾随逗号、注释、单引号；'
                '4. 输出必须是单个完整的 JSON 对象，以 { 开头、以 } 结尾。'
            )
            user = (
                f'下面内容应当是一份群聊分析 artifact JSON，但解析失败（第{attempt + 1}次修复）：{error}\n'
                '请只修复 JSON 语法，输出合法 JSON 对象。\n\n'
                f'{current}'
            )
            repaired_raw = call_llm(
                system=system,
                user=user,
                base_url=cfg['base_url'],
                api_key=cfg['api_key'],
                model=cfg['model'],
                temperature=0,
                max_tokens=cfg.get('max_tokens', 4096),
                timeout=_llm_timeout(cfg),
                proxy=cfg.get('proxy', 'auto'),
            )
            repaired = _preprocess_llm_json(repaired_raw)
            if _is_reasoning_only_response(repaired_raw):
                repair_error = ValueError('LLM 修复响应仅包含推理文本，未返回 JSON')
                repair_rounds.append({
                    'attempt': attempt + 1,
                    'error': _json_error_snapshot(repair_error, repaired),
                    'raw_output': repaired_raw,
                    'preprocessed_output': repaired,
                })
                error = repair_error
                break
            try:
                return parse_artifact_json(repaired)
            except Exception as repair_error:
                repair_rounds.append({
                    'attempt': attempt + 1,
                    'error': _json_error_snapshot(repair_error, repaired),
                    'raw_output': repaired_raw,
                    'preprocessed_output': repaired,
                })
                error = repair_error
                current = repaired
                continue
        debug_path = _write_artifact_json_debug(
            cfg, debug_context or {}, raw, preprocessed,
            first_error, repair_rounds, error
        )
        suffix = f'；调试文件: {debug_path}' if debug_path else ''
        raise ValueError(f'{first_error}; 修复 {max_repairs} 次后仍失败: {error}{suffix}')


def _merge_key(value: str) -> str:
    """Build a stable key for deterministic rollup de-duplication."""
    return re.sub(r'\s+', '', str(value or '')).casefold()


def _unique_values(values: list) -> list:
    result = []
    seen = set()
    for value in values or []:
        text = str(value or '').strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _candidate_score(candidate: dict) -> int:
    try:
        return int((candidate or {}).get('score') or 0)
    except (TypeError, ValueError):
        return 0


def _merge_partial_artifacts(partial_artifacts: list, stats: dict) -> dict:
    """Merge parsed chunk artifacts without another structured LLM response."""
    topic_map = {}
    topic_order = []
    followup_map = {}
    followup_order = []
    summaries = []

    for partial in partial_artifacts or []:
        summary = str((partial or {}).get('summary') or '').strip()
        if summary and summary not in summaries:
            summaries.append(summary)

        for source_topic in (partial or {}).get('topics') or []:
            title = str(source_topic.get('title') or '未命名话题').strip()
            key = _merge_key(title) or f'topic_{len(topic_order) + 1}'
            if key not in topic_map:
                topic_map[key] = {
                    'id': source_topic.get('id') or f'topic_{len(topic_order) + 1}',
                    'title': title,
                    'summary_parts': [],
                    'participants': [],
                    'evidence': [],
                    'knowledge_candidates': [],
                }
                topic_order.append(key)
            target = topic_map[key]
            topic_summary = str(source_topic.get('summary') or '').strip()
            if topic_summary and topic_summary not in target['summary_parts']:
                target['summary_parts'].append(topic_summary)
            target['participants'] = _unique_values(
                target['participants'] + list(source_topic.get('participants') or [])
            )

            evidence_keys = {
                (item.get('msg_id'), item.get('sender'), item.get('time'), item.get('quote'))
                for item in target['evidence']
            }
            for evidence in source_topic.get('evidence') or []:
                item = dict(evidence or {})
                item_key = (item.get('msg_id'), item.get('sender'), item.get('time'), item.get('quote'))
                if item_key not in evidence_keys:
                    target['evidence'].append(item)
                    evidence_keys.add(item_key)

            candidate_index = {
                _merge_key(item.get('title') or item.get('summary')): idx
                for idx, item in enumerate(target['knowledge_candidates'])
            }
            for candidate in source_topic.get('knowledge_candidates') or []:
                incoming = dict(candidate or {})
                candidate_key = _merge_key(incoming.get('title') or incoming.get('summary'))
                if not candidate_key:
                    candidate_key = f'candidate_{len(target["knowledge_candidates"])}'
                existing_idx = candidate_index.get(candidate_key)
                if existing_idx is None:
                    incoming['tags'] = _unique_values(incoming.get('tags') or [])
                    incoming['source_msg_ids'] = list(dict.fromkeys(incoming.get('source_msg_ids') or []))
                    target['knowledge_candidates'].append(incoming)
                    candidate_index[candidate_key] = len(target['knowledge_candidates']) - 1
                    continue
                existing = target['knowledge_candidates'][existing_idx]
                winner = incoming if _candidate_score(incoming) > _candidate_score(existing) else existing
                winner = dict(winner)
                winner['tags'] = _unique_values(
                    list(existing.get('tags') or []) + list(incoming.get('tags') or [])
                )
                winner['source_msg_ids'] = list(dict.fromkeys(
                    list(existing.get('source_msg_ids') or []) + list(incoming.get('source_msg_ids') or [])
                ))
                target['knowledge_candidates'][existing_idx] = winner

        for source_followup in (partial or {}).get('followups') or []:
            title = str(source_followup.get('title') or '').strip()
            if not title:
                continue
            key = _merge_key(title)
            if key not in followup_map:
                followup_map[key] = {
                    'title': title,
                    'owner': source_followup.get('owner') or '',
                    'evidence_msg_ids': list(source_followup.get('evidence_msg_ids') or []),
                }
                followup_order.append(key)
            else:
                target = followup_map[key]
                if not target['owner'] and source_followup.get('owner'):
                    target['owner'] = source_followup.get('owner')
                target['evidence_msg_ids'] = list(dict.fromkeys(
                    target['evidence_msg_ids'] + list(source_followup.get('evidence_msg_ids') or [])
                ))

    topics = []
    for key in topic_order:
        topic = topic_map[key]
        topic['summary'] = '；'.join(topic.pop('summary_parts'))
        topics.append(topic)
    return {
        'summary': '；'.join(summaries[:6]),
        'topics': topics,
        'followups': [followup_map[key] for key in followup_order],
        'stats': {**(stats or {}), 'rollup_mode': 'deterministic'},
    }


def _compact_artifact_for_polish(artifact: dict) -> dict:
    """Limit fallback polish input without discarding source-backed outcomes."""
    topics = []
    for topic in (artifact.get('topics') or [])[:12]:
        topics.append({
            'title': topic.get('title') or '',
            'summary': topic.get('summary') or '',
            'participants': list(topic.get('participants') or [])[:8],
            'evidence': list(topic.get('evidence') or [])[:2],
            'knowledge_candidates': list(topic.get('knowledge_candidates') or [])[:3],
        })
    return {
        'summary': artifact.get('summary') or '',
        'topics': topics,
        'followups': list(artifact.get('followups') or [])[:12],
    }


def _render_artifact_markdown(artifact: dict, group_name: str, date: str,
                              cfg: dict) -> str:
    """Render a report, with a Markdown-only polish pass for fallback rollups."""
    render_artifact = dict(artifact or {})
    render_artifact.setdefault('group_name', group_name)
    render_artifact.setdefault('date', date)
    fallback = sanitize_analysis_markdown(render_markdown_report(render_artifact), group_name)
    if (artifact.get('stats') or {}).get('rollup_mode') != 'deterministic':
        return fallback

    compact = _compact_artifact_for_polish(artifact)
    system = """你是专业的群聊分析报告编辑。请把已完成结构合并的群聊分析，整理成最终中文 Markdown 报告。

硬性要求：
1. 只输出最终 Markdown 报告，不输出思考、解释、JSON、代码块或英文元话语。
2. 必须从“# 群聊分析报告：”开始，包含“## 总体摘要”“## 关键话题”“## 待跟进事项”。
3. 只能使用输入中已有的事实、话题、证据和待跟进事项，不得编造。
4. 合并重复表达，按重要性排序，保持简洁、可读。
"""
    user = (
        f'群聊名称: {group_name}\n日期: {date}\n\n'
        f'已完成结构合并的分析数据:\n{_json.dumps(compact, ensure_ascii=False)}'
    )
    try:
        raw = call_llm(
            system=system, user=user,
            base_url=cfg['base_url'], api_key=cfg['api_key'], model=cfg['model'],
            temperature=cfg.get('temperature', 0.2),
            max_tokens=min(int(cfg.get('max_tokens') or 4096), 6000),
            timeout=_llm_timeout(cfg), proxy=cfg.get('proxy', 'auto'),
        )
        polished = sanitize_analysis_markdown(_strip_reasoning_blocks(raw), group_name)
        if polished.startswith('#') and ('总体摘要' in polished or '关键话题' in polished):
            return polished
    except Exception:
        pass
    return fallback


def _rollup_artifacts(group_name: str, date: str, stats: dict,
                      partial_artifacts: list, style_prompt: str,
                      cfg: dict) -> dict:
    """Ask LLM to merge chunk artifacts into one artifact."""
    system = """你是结构化群聊分析合并助手。请把多个分块 artifact 合并成一份完整 artifact。

硬性要求：
1. 只输出 JSON，不要 Markdown，不要解释，不要 ``` 包裹。
2. 合并重复话题，保留最有证据支撑的内容。
3. evidence 的 msg_id、sender、quote 必须原样保留。
4. knowledge_candidates 去重，保留分数更高、证据更完整的候选。
"""
    if style_prompt:
        system += '\n' + style_prompt
    user = (
        f'群聊名称: {group_name}\n日期: {date}\n统计: {_json.dumps(stats, ensure_ascii=False)}\n\n'
        f'分块 artifacts:\n{_json.dumps(partial_artifacts, ensure_ascii=False)}'
    )
    raw = call_llm(
        system=system, user=user,
        base_url=cfg['base_url'],
        api_key=cfg['api_key'],
        model=cfg['model'],
        temperature=cfg.get('temperature', 0.2),
        max_tokens=cfg.get('max_tokens', 4096),
        timeout=_llm_timeout(cfg),
        proxy=cfg.get('proxy', 'auto'),
    )
    debug_context = {
        'group_name': group_name,
        'date': date,
        'phase': 'rollup_artifact',
        'message_count': stats.get('message_count'),
        'total_chars': stats.get('total_chars'),
        'chunks': len(partial_artifacts or []),
    }
    try:
        return _parse_artifact_json_with_repair(raw, cfg, debug_context=debug_context)
    except Exception as e:
        return _merge_partial_artifacts(partial_artifacts, stats)


def analyze_group(decrypted_dir: str, chat_id: str, group_name: str,
                  date: str, wxid: str = None,
                  config_path: str = None,
                  progress_cb: Optional[Callable] = None) -> Tuple[str, str, int]:
    """Analyze one group for one date. Returns (message, status, msg_count).

    status in {'ok', 'skip', 'error'}. For ok, message is markdown. For skip or
    error, message is the reason shown to the user.
    """
    cfg_path = config_path or config_path_for(decrypted_dir)
    cfg = dict(load_llm_config(cfg_path) or {})
    cfg['_debug_dir'] = os.path.join(storage_dir_for(decrypted_dir), 'debug')

    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        return 'LLM 未配置', 'error', 0

    try:
        result = query_messages(
            decrypted_dir, chat_id, wxid=wxid,
            page=1, per_page=MAX_MSG_PER_GROUP * 2,
            start_date=date, end_date=date,
            msg_types=_ANALYZE_TYPES,
        )
        messages = result.get('messages', [])
    except FileNotFoundError:
        return '未找到该群当天的聊天数据库', 'skip', 0
    except Exception as e:
        return f'查询消息失败: {e}', 'error', 0

    if not messages:
        return '当天没有可分析的文字/链接消息', 'skip', 0

    formatted = format_messages_for_artifact(messages)
    if not formatted.strip():
        return '当天消息主要是图片、语音、文件或系统消息，暂无可总结文本', 'skip', len(messages)

    stats = _compute_message_stats(messages, formatted)
    style_prompt = ''
    try:
        style_prompt = style_to_prompt(ensure_style(decrypted_dir, chat_id, group_name))
    except Exception:
        style_prompt = ''

    if progress_cb:
        progress_cb(chat_id, group_name, 'extracting_artifact', len(messages))

    try:
        if len(formatted) > ARTIFACT_CHUNK_CHAR_THRESHOLD:
            chunks = _chunk_text_by_line(formatted)
            if len(chunks) > MAX_ARTIFACT_CHUNKS:
                chunks = chunks[-MAX_ARTIFACT_CHUNKS:]
                stats['truncated'] = True
            stats['chunked'] = True
            stats['chunks'] = len(chunks)
            partials = []
            for idx, chunk in enumerate(chunks, start=1):
                partials.append(_call_artifact_llm(
                    group_name, date, stats, chunk, style_prompt, cfg,
                    partial=True, chunk_index=idx
                ))
            artifact_data = _rollup_artifacts(group_name, date, stats, partials, style_prompt, cfg)
        else:
            artifact_data = _call_artifact_llm(
                group_name, date, stats, formatted, style_prompt, cfg, partial=False
            )
    except ValueError as e:
        return str(e), 'error', len(messages)
    except Exception as e:
        return str(e), 'error', len(messages)

    if progress_cb:
        progress_cb(chat_id, group_name, 'verifying_evidence', len(messages))
    artifact = normalize_artifact(
        artifact_data,
        chat_id=chat_id,
        group_name=group_name,
        date=date,
        stats=stats,
    )
    artifact['verify'] = verify_artifact_evidence(artifact, messages)
    save_artifact(storage_dir_for(decrypted_dir), artifact)

    if progress_cb:
        candidates_count = sum(
            len(topic.get('knowledge_candidates') or [])
            for topic in artifact.get('topics') or []
        )
        progress_cb(chat_id, group_name, 'rendering_report', candidates_count)
    markdown = _render_artifact_markdown(artifact, group_name, date, cfg)

    out = result_path(storage_dir_for(decrypted_dir), chat_id, date)
    try:
        _atomic_write_text(out, markdown)
    except OSError as e:
        return f'保存失败: {e}', 'error', len(messages)

    return markdown, 'ok', len(messages)


def analyze_group_range(decrypted_dir: str, chat_id: str, group_name: str,
                        start_date: str, end_date: str, wxid: str = None,
                        config_path: str = None,
                        progress_cb: Optional[Callable] = None) -> Tuple[str, str, int]:
    """Analyze one group for a date range and save it under a range label."""
    label = period_label(start_date, end_date)
    cfg_path = config_path or config_path_for(decrypted_dir)
    cfg = dict(load_llm_config(cfg_path) or {})
    cfg['_debug_dir'] = os.path.join(storage_dir_for(decrypted_dir), 'debug')

    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        return 'LLM 未配置', 'error', 0

    try:
        result = query_messages(
            decrypted_dir, chat_id, wxid=wxid,
            page=1, per_page=MAX_MSG_PER_GROUP * 2,
            start_date=start_date, end_date=end_date,
            msg_types=_ANALYZE_TYPES,
        )
        messages = result.get('messages', [])
    except FileNotFoundError:
        return '未找到该时间段的聊天数据库', 'skip', 0
    except Exception as e:
        return f'查询消息失败: {e}', 'error', 0

    if not messages:
        return '该时间段没有可分析的文字/链接消息', 'skip', 0

    formatted = format_messages_for_artifact(messages)
    if not formatted.strip():
        return '该时间段消息主要是图片、语音、文件或系统消息，暂无可总结文本', 'skip', len(messages)

    stats = _compute_message_stats(messages, formatted)
    stats['start_date'] = start_date
    stats['end_date'] = end_date
    style_prompt = ''
    try:
        style_prompt = style_to_prompt(ensure_style(decrypted_dir, chat_id, group_name))
    except Exception:
        style_prompt = ''

    if progress_cb:
        progress_cb(chat_id, group_name, 'extracting_artifact', len(messages))

    try:
        if len(formatted) > ARTIFACT_CHUNK_CHAR_THRESHOLD:
            chunks = _chunk_text_by_line(formatted)
            if len(chunks) > MAX_ARTIFACT_CHUNKS:
                chunks = chunks[-MAX_ARTIFACT_CHUNKS:]
                stats['truncated'] = True
            stats['chunked'] = True
            stats['chunks'] = len(chunks)
            partials = []
            for idx, chunk in enumerate(chunks, start=1):
                partials.append(_call_artifact_llm(
                    group_name, label, stats, chunk, style_prompt, cfg,
                    partial=True, chunk_index=idx
                ))
            artifact_data = _rollup_artifacts(group_name, label, stats, partials, style_prompt, cfg)
        else:
            artifact_data = _call_artifact_llm(
                group_name, label, stats, formatted, style_prompt, cfg, partial=False
            )
    except ValueError as e:
        return str(e), 'error', len(messages)
    except Exception as e:
        return str(e), 'error', len(messages)

    if progress_cb:
        progress_cb(chat_id, group_name, 'verifying_evidence', len(messages))
    artifact = normalize_artifact(
        artifact_data,
        chat_id=chat_id,
        group_name=group_name,
        date=label,
        stats=stats,
    )
    artifact['verify'] = verify_artifact_evidence(artifact, messages)
    save_artifact(storage_dir_for(decrypted_dir), artifact)

    if progress_cb:
        candidates_count = sum(
            len(topic.get('knowledge_candidates') or [])
            for topic in artifact.get('topics') or []
        )
        progress_cb(chat_id, group_name, 'rendering_report', candidates_count)
    markdown = _render_artifact_markdown(artifact, group_name, label, cfg)

    out = result_path(storage_dir_for(decrypted_dir), chat_id, label)
    try:
        _atomic_write_text(out, markdown)
    except OSError as e:
        return f'保存失败: {e}', 'error', len(messages)

    return markdown, 'ok', len(messages)


def analyze_multiple(decrypted_dir: str, chat_ids: list, group_names: dict,
                     date: str, wxid: str = None,
                     progress_cb: Optional[Callable] = None) -> List[Dict]:
    """Analyze multiple groups for one date.

    progress_cb(chat_id, group_name, status, current, total) is called after each.
    Returns list of {chat_id, status, error}.
    """
    results = []
    total = len(chat_ids)
    for i, chat_id in enumerate(chat_ids, 1):
        group_name = group_names.get(chat_id, chat_id)
        if progress_cb:
            progress_cb(chat_id, group_name, 'analyzing', i, total)
        md, status, msg_count = analyze_group(
            decrypted_dir, chat_id, group_name, date,
            wxid=wxid,
            progress_cb=(
                (lambda cid, gn, st, count, i=i, total=total:
                 progress_cb(cid, gn, st, i, total, count))
                if progress_cb else None
            ),
        )
        results.append({
            'chat_id': chat_id,
            'group_name': group_name,
            'status': status,
            'msg_count': msg_count,
            'error': '' if status == 'ok' else md,
        })
        if progress_cb:
            progress_cb(chat_id, group_name, 'done' if status == 'ok' else status, i, total)
    return results


def analyze_multiple_range(decrypted_dir: str, chat_ids: list, group_names: dict,
                           start_date: str, end_date: str, wxid: str = None,
                           progress_cb: Optional[Callable] = None) -> List[Dict]:
    """Analyze multiple groups for one date range."""
    results = []
    total = len(chat_ids)
    label = period_label(start_date, end_date)
    for i, chat_id in enumerate(chat_ids, 1):
        group_name = group_names.get(chat_id, chat_id)
        if progress_cb:
            progress_cb(chat_id, group_name, 'analyzing', i, total)
        md, status, msg_count = analyze_group_range(
            decrypted_dir, chat_id, group_name, start_date, end_date,
            wxid=wxid,
            progress_cb=(
                (lambda cid, gn, st, count, i=i, total=total:
                 progress_cb(cid, gn, st, i, total, count))
                if progress_cb else None
            ),
        )
        results.append({
            'chat_id': chat_id,
            'group_name': group_name,
            'status': status,
            'msg_count': msg_count,
            'date': label,
            'error': '' if status == 'ok' else md,
        })
        if progress_cb:
            progress_cb(chat_id, group_name, 'done' if status == 'ok' else status, i, total)
    return results


# ---------------------------------------------------------------------------
# Tag tree CRUD
# ---------------------------------------------------------------------------

def load_tags(config_path: str) -> list:
    """Load tag tree from config. Returns [] if missing."""
    return _get_config(config_path).get('tags', [])


def save_tags(tags: list, config_path: str) -> None:
    """Save tag tree to config. Preserves other config fields (e.g. llm, schedules)."""
    full = _get_config(config_path)
    full['tags'] = tags
    os.makedirs(os.path.dirname(config_path) or '.', exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(full, f, ensure_ascii=False, indent=2)


def collect_tagged_chat_ids(tags: list) -> set:
    """Recursively collect all chat_ids from a tag tree."""
    result = set()
    for tag in tags:
        result.update(tag.get('chat_ids', []))
        for child in tag.get('children', []):
            result.update(collect_tagged_chat_ids([child]))
    return result


def compute_untagged_chat_ids(tags: list, all_chat_ids: list) -> list:
    """Return chat_ids not present in any tag."""
    tagged = collect_tagged_chat_ids(tags)
    return [cid for cid in all_chat_ids if cid not in tagged]


def flatten_tag_tree(tags: list) -> list:
    """Convert tag tree to flat list suitable for AI classification input."""
    result = []
    for tag in tags:
        if tag.get('chat_ids'):
            result.append({'name': tag['name'], 'chat_ids': list(tag['chat_ids'])})
        for child in tag.get('children', []):
            if child.get('chat_ids'):
                result.append({'name': f"{tag['name']}/{child['name']}",
                               'chat_ids': list(child['chat_ids'])})
    return result


def auto_classify_groups(group_names: list, batch_size: int = 80,
                          progress_cb=None, config_path: str = None) -> list:
    """Call LLM to classify group names into categories. Returns flat tag list.

    Each batch sent to LLM separately. On parse failure, retry once.
    """
    cfg = load_llm_config(config_path) if config_path else {}
    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        raise RuntimeError('LLM 未配置')

    system = (
        '你是一个群聊分类助手。请根据群聊名称，将它们分类。\n\n'
        '规则：\n'
        '1. 根据群名含义创建分类（不超过15个大类）\n'
        '2. 每个群必须归入一个分类\n'
        '3. 如果不确定，归入"其他"\n'
        '4. 输出纯 JSON，不要其他文字'
    )

    all_results = []
    total = len(group_names)
    for i in range(0, total, batch_size):
        batch = group_names[i:i + batch_size]
        user_lines = '\n'.join(f'{j+1}. {name}' for j, name in enumerate(batch))
        user = f'请对以下群聊进行分类：\n\n{user_lines}\n\n输出格式：\n{{"分类名": ["群名1", "群名2"], "另一个分类": ["群名3"]}}'

        for attempt in range(2):
            try:
                content = call_llm(
                    system=system, user=user,
                    base_url=cfg['base_url'], api_key=cfg['api_key'],
                    model=cfg['model'],
                    temperature=cfg.get('temperature', 0.3),
                    max_tokens=cfg.get('max_tokens', 4096),
                    timeout=_llm_timeout(cfg),
                    proxy=cfg.get('proxy', 'auto'),
                )
                parsed = _json.loads(content)
                for cat, names in parsed.items():
                    all_results.append({'name': cat, 'chat_ids': names})
                break
            except Exception:
                if attempt == 1:
                    all_results.append({'name': '未分类', 'chat_ids': batch})

        if progress_cb:
            done = min(i + batch_size, total)
            progress_cb(done, total)

    return all_results


def map_ai_results_to_tags(ai_results: list, name_to_wxid: dict) -> list:
    """Convert AI classification results (name-based) to tag tree (wxid-based).

    Args:
        ai_results: list of {name: category, chat_ids: [group_names]}
        name_to_wxid: {display_name: wxid} mapping
    """
    tags = []
    for entry in ai_results:
        cat = entry['name']
        wxids = [name_to_wxid[n] for n in entry.get('chat_ids', []) if n in name_to_wxid]
        if wxids:
            tags.append({'name': cat, 'chat_ids': wxids})
    return tags
