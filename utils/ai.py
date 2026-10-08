"""DeepSeek 智能问答纯函数：关键词扩展 + 文本分块 + LLM 调用。"""
import re
import json
from typing import List

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL

# 文本分块参数
CHUNK_SIZE = 1200        # 每个文本块的字符数（更细的块，便于精确命中 + 覆盖更多文件）
CHUNK_OVERLAP = 100      # 相邻块的重叠字符数，避免关键词落在切缝处漏检

MAX_CONTINUATIONS = 3    # 回答被 max_tokens 截断时，最多自动续写次数（防无限循环）


def _fallback_terms(question: str) -> List[str]:
    """兜底：把问题拆成中文二元组 + 英文/数字词"""
    terms = set()
    cn_chars = re.findall(r"[\u4e00-\u9fff]", question)
    for i in range(len(cn_chars) - 1):
        terms.add(cn_chars[i] + cn_chars[i + 1])
    terms.update(re.findall(r"[A-Za-z0-9_]{2,}", question))
    if not terms:
        terms = {question}
    return list(terms)


def _expand_query(question: str) -> List[str]:
    """用 DeepSeek 把问题扩展成一组检索关键词（含同义词/别名），失败返回空列表"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    prompt = (
        "你是检索专家。请把用户的问题改写成一组用于全文检索的关键词/短语，"
        "包含同义词、近义词和常见别名，每个词尽量简短。\n"
        "只输出一个 JSON 字符串数组，不要任何其他文字。\n"
        "例如问题\"项目的疲劳寿命是多少\" → [\"疲劳寿命\",\"疲劳强度\",\"疲劳\",\"寿命\",\"试验\"]\n\n"
        f"问题：{question}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 300,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        arr = json.loads(raw[start:end + 1])
    except Exception:
        return []
    return [str(x).strip() for x in arr if str(x).strip()]


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """把长文本切成带重叠的块，尽量在换行/句号处断开，减少语义截断。"""
    text = text or ""
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(
                text.rfind("\n", start + size // 2, end),
                text.rfind("。", start + size // 2, end),
                text.rfind("；", start + size // 2, end),
            )
            if cut > start + size // 2:
                end = cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _call_deepseek(question: str, context: str) -> str:
    """调用 DeepSeek API，仅依据资料回答问题；若回答被 max_tokens 截断，自动续写补齐。"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "你是研发知识库的专业研究助手，擅长从文件资料与结构化台账（合同、经费、成果、待办、项目信息、资料摘要）中提炼、归纳和综合信息。"
        "请基于提供的资料，全面、准确地回答用户问题。\n\n"
        "要求：\n"
        "1. 优先从资料中提取答案，覆盖资料里的关键信息、数据和结论，不要遗漏重要内容；"
        "当资料覆盖多个文件/项目时，尽量都照顾到。\n"
        "2. 可以适当归纳、总结、对比和概括，用清晰的结构（分点、小标题、表格）呈现，"
        "让回答既全面又好读。\n"
        "3. 如果资料不足以完整回答，请如实说明，并给出已检索到的相关线索，不要编造、不要硬凑。\n"
        "4. 涉及具体数据或结论时，自然地注明出处（例如“根据《xxx》记载”或“《xxx》中提到”），"
        "不要机械地在每个数字后加括号堆砌来源。\n"
        "5. 语气专业、自然、易读，避免机械罗列和重复。\n"
        "6. 当上下文中出现【精确统计结果】标记时，这些数字已由系统精确计算，请直接引用，"
        "不要自行重新计算、数数或估算；若是分组计数（如「已授权:86，已登记:18」），各数字是并列的分类计数，"
        "不要把它们相加当成总数；涉及统计请注明统计口径。\n"
        "7. 对于「有多少/有哪些/清单/列表」这类统计或列举问题，请简洁地报出总数与分类，"
        "再举 2~3 个示例即可，不要逐条罗列全部明细；完整明细清单由系统在「参考资料」中提供，"
        "你可在回答末尾用一句话提示「完整清单见参考资料」，不要抱怨资料不完整或缺失。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"},
    ]

    parts = []
    for _ in range(MAX_CONTINUATIONS + 1):  # 首次回答 + 最多 MAX_CONTINUATIONS 次续写
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 8100,
            "stream": False,
        }
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"] or ""
        finish_reason = choice.get("finish_reason", "")
        if content:
            parts.append(content)
        # 仅在「因长度截断」且确实生成了内容时才续写，避免死循环
        if finish_reason != "length" or not content:
            break
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "请继续补充，从上一次中断处接着写，直接输出剩余内容，不要重复已经写过的部分。",
        })

    return "".join(parts)
