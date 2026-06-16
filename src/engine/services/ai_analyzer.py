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

from engine.services.message import query_messages
from engine.services.name_resolver import resolve_wxid

# Maximum messages per group per day — 超过则截断到最近 N 条
MAX_MSG_PER_GROUP = 3000

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
             timeout: int = 30) -> str:
    """Call an OpenAI-compatible chat completion API. Returns message content.

    Retries once on timeout. Raises on HTTP error or after final failure.
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

    last_err = None
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data['choices'][0]['message']['content']
            raise RuntimeError(f'LLM API error {resp.status_code}: {resp.text[:300]}')
        except requests.Timeout as e:
            last_err = e
            continue
        except requests.RequestException as e:
            last_err = e
            raise RuntimeError(f'LLM request failed: {e}')

    raise RuntimeError(f'LLM request timed out twice: {last_err}')


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

def result_path(storage_dir: str, chat_id: str, date: str) -> str:
    """Return absolute path to the Markdown file for (chat_id, date)."""
    safe_id = ''.join(c if c.isalnum() or c in '_@' else '_' for c in chat_id)
    return os.path.join(storage_dir, safe_id, f'{date}.md')


def storage_dir_for(decrypted_dir: str) -> str:
    """Get the AI analysis storage directory for a given decrypted data root."""
    return os.path.join(os.path.dirname(decrypted_dir), 'ai_analysis')


def config_path_for(decrypted_dir: str) -> str:
    """Get the LLM config path for a given decrypted data root."""
    return os.path.join(storage_dir_for(decrypted_dir), 'config.json')


def analyze_group(decrypted_dir: str, chat_id: str, group_name: str,
                  date: str, wxid: str = None,
                  config_path: str = None) -> Tuple[str, str]:
    """Analyze one group for one date. Returns (markdown, status).

    status in {'ok', 'skip', 'error'}
    """
    cfg_path = config_path or config_path_for(decrypted_dir)
    cfg = load_llm_config(cfg_path)

    if not cfg.get('base_url') or not cfg.get('api_key') or not cfg.get('model'):
        return '', 'error: LLM 未配置'

    try:
        result = query_messages(
            decrypted_dir, chat_id, wxid=wxid,
            page=1, per_page=MAX_MSG_PER_GROUP * 2,
            start_date=date, end_date=date,
            msg_types=_ANALYZE_TYPES,
        )
        messages = result.get('messages', [])
    except FileNotFoundError:
        return '', 'skip'
    except Exception as e:
        return f'查询消息失败: {e}', 'error'

    if not messages:
        return '', 'skip'

    formatted = format_messages_for_llm(messages)
    system, user = build_prompt(group_name, date, len(messages), formatted)

    try:
        markdown = call_llm(
            system=system, user=user,
            base_url=cfg['base_url'],
            api_key=cfg['api_key'],
            model=cfg['model'],
            temperature=cfg.get('temperature', 0.3),
            max_tokens=cfg.get('max_tokens', 4096),
        )
    except Exception as e:
        return str(e), 'error'

    markdown = sanitize_analysis_markdown(markdown, group_name)

    out = result_path(storage_dir_for(decrypted_dir), chat_id, date)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        with open(out, 'w', encoding='utf-8') as f:
            f.write(markdown)
    except OSError as e:
        return f'保存失败: {e}', 'error'

    return markdown, 'ok'


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
        md, status = analyze_group(
            decrypted_dir, chat_id, group_name, date,
            wxid=wxid,
        )
        results.append({'chat_id': chat_id, 'status': status, 'error': '' if status == 'ok' else md})
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
