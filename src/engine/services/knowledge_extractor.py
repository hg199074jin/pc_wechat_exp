"""Knowledge Radar — LLM extraction pipeline for knowledge cards."""
from __future__ import annotations

import json
import re
import html
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from engine.services import ai_analyzer

CARD_TYPES = {
    'audit_case', 'sop', 'prompt', 'faq', 'article', 'tool',
    'risk', 'methodology', 'note',
}

KNOWLEDGE_CHUNK_CHAR_THRESHOLD = 12000
KNOWLEDGE_CHUNK_CHAR_SIZE = 6000
MAX_KNOWLEDGE_CHUNKS = 8


def readable_message_text(content: str) -> str:
    """Return human-readable text from WeChat message content.

    Link/referenced messages are often XML. For knowledge sources, the useful
    text is usually in <title>; expose that instead of raw XML.
    """
    text = (content or '').strip()
    if not text:
        return ''
    if text.startswith('<?xml') or text.startswith('<msg') or '<appmsg>' in text:
        m = re.search(r'<title>(.*?)</title>', text, flags=re.DOTALL | re.IGNORECASE)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1))
            return html.unescape(title).strip()
    return text

DOMAIN_LABELS = {
    'audit_ai': '审计/财税 + AI工具',
    'audit': '审计/财税',
    'ai': 'AI工具与工作流',
    'private_domain': '私域/课程',
    'general': '综合',
}


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _extract_json_object(text: str) -> str:
    """Best-effort extract a JSON object from LLM output."""
    s = ai_analyzer._preprocess_llm_json(text)
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
    start = s.find('{')
    if start >= 0:
        candidate = s[start:]
        try:
            _, end = json.JSONDecoder().raw_decode(candidate)
            return candidate[:end]
        except json.JSONDecodeError:
            end = candidate.rfind('}')
            if end >= 0:
                return candidate[:end + 1]
    return s


def parse_llm_cards(raw: str, min_score: int = 70) -> List[Dict]:
    """Parse LLM JSON output into a list of validated card dicts."""
    data = json.loads(_extract_json_object(raw))
    cards = data.get('cards', [])
    result = []
    for card in cards:
        title = (card.get('title') or '').strip()
        try:
            score = int(card.get('score') or 0)
        except (TypeError, ValueError):
            continue
        if not title or score < min_score:
            continue
        ctype = card.get('type') or 'note'
        if ctype not in CARD_TYPES:
            ctype = 'note'
        result.append({
            'title': title,
            'type': ctype,
            'score': score,
            'summary': card.get('summary') or '',
            'why_valuable': card.get('why_valuable') or '',
            'content_md': card.get('content_md') or '',
            'tags': card.get('tags') if isinstance(card.get('tags'), list) else [],
            'source_msg_ids': card.get('source_msg_ids') if isinstance(card.get('source_msg_ids'), list) else [],
        })
    return result


def _parse_llm_cards_with_repair(raw: str, min_score: int, llm_call) -> List[Dict]:
    """Parse knowledge cards, with one repair attempt for malformed JSON."""
    try:
        return parse_llm_cards(raw, min_score=min_score)
    except (ValueError, json.JSONDecodeError) as first_error:
        system = """你是严格的 JSON 修复器。只输出修复后的合法 JSON 对象。
不要输出思考、解释、Markdown、代码块或 <think> 标签。
保持卡片字段和值不变，只修复 JSON 语法。输出必须以 { 开头、以 } 结尾。"""
        user = (
            f'下面内容应是知识卡 JSON，但解析失败：{first_error}\n'
            '请只修复 JSON 语法，输出单个合法 JSON 对象。\n\n'
            f'{ai_analyzer._preprocess_llm_json(raw)}'
        )
        repaired_raw = llm_call(system, user)
        if ai_analyzer._is_reasoning_only_response(repaired_raw):
            raise ValueError('LLM 修复响应仅包含推理文本，未返回 JSON')
        try:
            return parse_llm_cards(repaired_raw, min_score=min_score)
        except (ValueError, json.JSONDecodeError) as repair_error:
            raise ValueError(
                f'知识卡 JSON 解析失败: {first_error}; 修复后仍失败: {repair_error}'
            )


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _domain_guidance(domain: str) -> str:
    """Return domain-specific extraction guidance."""
    guides = {
        'audit_ai': """优先识别：
- 审计判断口径、底稿处理经验、函证/收入/成本/往来实务案例
- 监管检查、整改事项、风险提示
- AI 新工具、可复用 prompt、自动化工作流、模型选择经验""",
        'audit': """优先识别：
- 审计判断口径、底稿处理经验
- 函证、收入、成本、费用、往来、存货实务案例
- 监管、财政检查、注协检查、整改事项
- 客户资料缺口、风险提示""",
        'ai': """优先识别：
- 新工具和使用方法
- 可复用 prompt 和自动化工作流
- 安装配置踩坑、模型选择经验、token 成本经验""",
        'private_domain': """优先识别：
- 用户真实问题、高频需求
- 课程反馈、可写成文章的观点
- 可产品化的服务机会""",
    }
    return guides.get(domain, '综合识别所有高价值可复用知识。')


def build_knowledge_prompt(domain: str, min_score: int, messages_text: str) -> Tuple[str, str]:
    """Build system + user prompts for knowledge extraction."""
    guidance = _domain_guidance(domain)
    system = f"""你是知识资产提炼专家。你的任务不是总结聊天，而是找出值得长期沉淀和复用的知识。

硬性要求：
1. 只输出 JSON，不要 Markdown，不要解释，不要 ``` 包裹。
2. 每条卡片 score 范围 0-100，只输出 score >= {min_score} 的内容。
3. 每条卡片必须能转化为案例、SOP、提示词、FAQ、文章素材、工具经验、风险线索或方法论之一。
4. source_msg_ids 必须引用消息记录中的 msg_id 数字。
5. 宁缺毋滥：如果没有真正值得沉淀的内容，输出 {{"cards": []}}。

评分维度（满分100）：
- 可复用性 25%：能否在未来项目、客户、写作或培训中重复使用
- 业务价值 25%：能否带来收入、效率、风险控制或客户服务价值
- 稀缺性 15%：是否区别于普通闲聊和泛泛信息
- 结构化程度 15%：是否包含步骤、判断框架、案例结构
- 证据价值 10%：是否有原文、数据、具体情境支撑
- 行动价值 10%：是否能直接形成下一步动作

{guidance}"""

    user = f"""消息记录:
{messages_text}

输出格式（严格 JSON）:
{{"cards":[{{"title":"标题","type":"audit_case|sop|prompt|faq|article|tool|risk|methodology|note","score":80,"summary":"摘要","why_valuable":"为什么值得沉淀","content_md":"结构化正文","tags":["标签"],"source_msg_ids":[123]}}]}}"""

    return system, user


def build_convert_prompt(card: dict, target_type: str) -> Tuple[str, str]:
    """Build prompts to convert a card to a structured format."""
    type_labels = {
        'audit_case': '审计实务案例',
        'sop': 'SOP（标准操作流程）',
        'prompt': '可复用 AI 提示词',
        'faq': 'FAQ（常见问题解答）',
        'article': '文章/课程素材',
        'script': '客户服务话术',
    }
    label = type_labels.get(target_type, target_type)

    system = f"""你是知识转化专家。将给定的知识卡片转化为 {label} 格式。
要求：
1. 只输出 Markdown 正文，不要 JSON，不要解释。
2. 保留原文关键信息，不编造。
3. 结构清晰，可直接使用。"""

    sources_text = ''
    for src in (card.get('sources') or []):
        sources_text += f"\n- [{src.get('chat_name', '')}] {src.get('sender', '')}: {src.get('quote', '')}"

    user = f"""知识卡片:
标题: {card.get('title', '')}
类型: {card.get('type', '')}
摘要: {card.get('summary', '')}
价值: {card.get('why_valuable', '')}
正文:
{card.get('content_md', '')}
标签: {', '.join(card.get('tags', []))}
来源引用:{sources_text}

请转化为 {label} 格式的 Markdown。"""

    return system, user


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_messages_for_knowledge(messages: list) -> str:
    """Format messages with msg_id for knowledge extraction."""
    lines = []
    for msg in sorted(messages, key=lambda m: m.get('create_time') or 0):
        msg_id = msg.get('id')
        if msg_id is None:
            msg_id = msg.get('msg_id')
        ts = msg.get('create_time')
        try:
            dt = datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M')
        except Exception:
            dt = ''
        sender = msg.get('sender_name') or msg.get('sender') or '未知'
        content = (msg.get('content') or '').strip()
        content = readable_message_text(content)
        if not content:
            continue
        lines.append(f'[msg_id={msg_id}] [{dt}] {sender}: {content}')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Message loading
# ---------------------------------------------------------------------------

def load_messages_for_scan(decrypted_dir: str, chat_id: str, date: str,
                           wxid: str = None, end_date: str = None) -> list:
    """Load messages for a single chat on one day or a date range."""
    from engine.services.message import query_messages
    result = query_messages(
        decrypted_dir, chat_id, wxid=wxid,
        page=1, per_page=5000,
        start_date=date, end_date=end_date or date,
        msg_types='1,49',
    )
    return result.get('messages', [])


# ---------------------------------------------------------------------------
# Extraction orchestration
# ---------------------------------------------------------------------------

def _chunk_messages_for_knowledge(messages: list, *, max_chars: int = KNOWLEDGE_CHUNK_CHAR_SIZE) -> list:
    """Split messages into chunks that fit the knowledge extraction prompt."""
    chunks = []
    current = []
    current_len = 0
    for msg in sorted(messages or [], key=lambda m: m.get('create_time') or 0):
        text = format_messages_for_knowledge([msg])
        if not text.strip():
            continue
        if current and current_len + len(text) > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(msg)
        current_len += len(text)
    if current:
        chunks.append(current)
    return chunks


def _card_dedupe_key(card: dict) -> str:
    title = (card.get('title') or '').strip().lower()
    if title:
        return title
    return (card.get('summary') or '').strip().lower()[:120]


def dedupe_knowledge_cards(cards: list) -> list:
    """Merge duplicate cards produced by chunked extraction."""
    merged = {}
    order = []
    for card in cards or []:
        key = _card_dedupe_key(card) or str(len(order))
        if key not in merged:
            merged[key] = card
            order.append(key)
            continue

        old = merged[key]
        if int(card.get('score') or 0) > int(old.get('score') or 0):
            card, old = old, card
            merged[key] = old

        seen_sources = {
            (src.get('chat_id'), src.get('msg_id'), src.get('create_time'))
            for src in old.get('sources', [])
        }
        for src in card.get('sources', []):
            sig = (src.get('chat_id'), src.get('msg_id'), src.get('create_time'))
            if sig not in seen_sources:
                old.setdefault('sources', []).append(src)
                seen_sources.add(sig)
        old['tags'] = sorted(set((old.get('tags') or []) + (card.get('tags') or [])))
    return [merged[key] for key in order]


def extract_cards_from_messages_chunked(
    messages: list,
    chat_name: str,
    date: str,
    llm_call,
    *,
    min_score: int = 70,
    domain: str = 'general',
    error_cb: Optional[Callable[[str], None]] = None,
) -> List[Dict]:
    """Extract knowledge cards, chunking long date ranges before LLM calls."""
    text = format_messages_for_knowledge(messages)
    if not text.strip():
        return []
    if len(text) <= KNOWLEDGE_CHUNK_CHAR_THRESHOLD:
        return extract_cards_from_messages(
            messages, chat_name, date, llm_call,
            min_score=min_score, domain=domain, error_cb=error_cb,
        )

    chunks = _chunk_messages_for_knowledge(messages)
    if len(chunks) > MAX_KNOWLEDGE_CHUNKS:
        chunks = chunks[-MAX_KNOWLEDGE_CHUNKS:]
    cards = []
    for index, chunk in enumerate(chunks, start=1):
        def _chunk_error(reason, index=index, total=len(chunks)):
            if error_cb:
                error_cb(f'分块 {index}/{total}: {reason}')
        cards.extend(extract_cards_from_messages(
            chunk, chat_name, date, llm_call,
            min_score=min_score, domain=domain, error_cb=_chunk_error,
        ))
    return dedupe_knowledge_cards(cards)


def extract_cards_from_messages(
    messages: list,
    chat_name: str,
    date: str,
    llm_call,
    *,
    min_score: int = 70,
    domain: str = 'general',
    error_cb: Optional[Callable[[str], None]] = None,
) -> List[Dict]:
    """Extract knowledge cards from messages using LLM.

    Args:
        messages: list of message dicts with id, create_time, sender_name, content
        chat_name: display name of the chat
        date: date string for card attribution
        llm_call: callable(system, user) -> raw LLM response string
        min_score: minimum score threshold
        domain: extraction domain for prompt guidance

    Returns:
        list of card dicts with sources attached
    """
    text = format_messages_for_knowledge(messages)
    if not text.strip():
        return []

    system, user = build_knowledge_prompt(domain, min_score, text)
    raw = llm_call(system, user)
    try:
        parsed = _parse_llm_cards_with_repair(raw, min_score, llm_call)
    except ValueError as e:
        if error_cb:
            error_cb(str(e))
        return []

    by_id = {}
    for m in messages:
        mid = m.get('id')
        if mid is None:
            mid = m.get('msg_id')
        if mid is not None:
            by_id[mid] = m

    cards = []
    for card in parsed:
        sources = []
        for mid in card.pop('source_msg_ids', []):
            msg = by_id.get(mid)
            if not msg:
                continue
            sources.append({
                'chat_id': '',
                'chat_name': chat_name,
                'msg_id': mid,
                'sender': msg.get('sender_name') or msg.get('sender') or '',
                'create_time': msg.get('create_time'),
                'quote': readable_message_text(msg.get('content') or '')[:500],
                'context': [],
            })
        # Fallback: attach first message as source
        if not sources and messages:
            msg = messages[0]
            sources.append({
                'chat_id': '',
                'chat_name': chat_name,
                'msg_id': msg.get('id') if msg.get('id') is not None else msg.get('msg_id'),
                'sender': msg.get('sender_name') or '',
                'create_time': msg.get('create_time'),
                'quote': readable_message_text(msg.get('content') or '')[:500],
                'context': [],
            })
        card['date'] = date
        card['sources'] = sources
        cards.append(card)
    return cards


def cards_from_analysis_artifact(artifact: dict, *, min_score: int = 80) -> List[Dict]:
    """Convert artifact knowledge candidates into knowledge-store cards.

    Unlike extract_cards_from_messages(), this function does not fallback to an
    arbitrary first message when sources are missing. Missing evidence is
    surfaced in why_valuable so the user can decide whether to keep the card.
    """
    chat_id = artifact.get('chat_id') or ''
    chat_name = artifact.get('group_name') or chat_id
    date = artifact.get('date') or ''
    cards = []

    for topic in artifact.get('topics') or []:
        evidence_by_id = {}
        for ev in topic.get('evidence') or []:
            mid = ev.get('msg_id')
            if mid is None:
                continue
            evidence_by_id[mid] = ev
            evidence_by_id[str(mid)] = ev

        for candidate in topic.get('knowledge_candidates') or []:
            try:
                score = int(candidate.get('score') or 0)
            except (TypeError, ValueError):
                score = 0
            if score < min_score:
                continue

            source_msg_ids = candidate.get('source_msg_ids') or []
            sources = []
            missing = []
            for mid in source_msg_ids:
                ev = evidence_by_id.get(mid) or evidence_by_id.get(str(mid))
                if not ev:
                    missing.append(mid)
                    continue
                sources.append({
                    'chat_id': chat_id,
                    'chat_name': chat_name,
                    'msg_id': ev.get('msg_id'),
                    'sender': ev.get('sender') or '',
                    'create_time': ev.get('create_time'),
                    'quote': ev.get('quote') or '',
                    'context': [{
                        'topic': topic.get('title') or '',
                        'time': ev.get('time') or '',
                        'verified': ev.get('verified', False),
                    }],
                })

            why = candidate.get('why_valuable') or ''
            if missing:
                suffix = f'证据缺失：{", ".join(str(x) for x in missing)}'
                why = f'{why}\n{suffix}'.strip()

            ctype = candidate.get('type') or 'note'
            if ctype not in CARD_TYPES:
                ctype = 'note'
            cards.append({
                'title': candidate.get('title') or '',
                'type': ctype,
                'status': 'inbox',
                'score': score,
                'summary': candidate.get('summary') or '',
                'why_valuable': why,
                'content_md': candidate.get('content_md') or '',
                'tags': candidate.get('tags') if isinstance(candidate.get('tags'), list) else [],
                'source_chat_ids': [chat_id] if chat_id else [],
                'date': date,
                'sources': sources,
            })

    return [c for c in cards if c.get('title')]


def make_llm_call(config_path: str):
    """Create an LLM callable from the saved config."""
    cfg = ai_analyzer.load_llm_config(config_path)
    if not cfg.get('base_url') or not cfg.get('api_key'):
        raise RuntimeError('LLM 未配置：请先在 AI 分析页面配置 API 地址和密钥')

    def _call(system: str, user: str) -> str:
        return ai_analyzer.call_llm(
            system=system,
            user=user,
            base_url=cfg['base_url'],
            api_key=cfg['api_key'],
            model=cfg.get('model', ''),
            temperature=cfg.get('temperature', 0.2),
            max_tokens=cfg.get('max_tokens', 4096),
            timeout=ai_analyzer._llm_timeout(cfg),
            proxy=cfg.get('proxy', 'auto'),
        )
    return _call
