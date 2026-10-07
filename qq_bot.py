# -*- coding: utf-8 -*-
"""
QQ 机器人网关（官方 QQ 开放平台 botpy SDK）

功能：让用户在手机 QQ 上「单聊」本系统机器人，实现：
  1. 发文字提问  → 智能问答（检索知识库 + DeepSeek 回答）
  2. 发「找/下载 xx 文件」 → 全文检索命中文件 → 把文件实体发回 QQ
  3. 发「汇总/统计/报告 xx」 → 跨项目统计与报告
  4. 发文件给机器人 → 下载保存到本地

架构：
  手机 QQ  ──WebSocket(出站)──▶ 腾讯 QQ 机器人网关  ──▶  本进程(botpy)
                                                            │ HTTP(127.0.0.1:8000)
                                                            ▼
                                                      后端 main.py（检索/问答/下载）

接入前提：
  - 在 q.qq.com 用手机 QQ 扫码注册开发者并创建机器人，拿到 AppID + AppSecret
  - 在机器人「开发设置」里订阅 C2C（单聊）消息事件
  - 将 AppID / AppSecret 写入 .env 的 QQ_APP_ID / QQ_APP_SECRET

运行：python qq_bot.py（需与后端 main.py 同时运行）
"""
import os
import io
import re
import json
import asyncio
import hashlib
import logging

import aiohttp
import botpy
from botpy.message import C2CMessage
from botpy.http import Route

# ===================== 配置 =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env(path: str) -> dict:
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = _load_env(os.path.join(BASE_DIR, ".env"))
QQ_APP_ID = os.getenv("QQ_APP_ID", ENV.get("QQ_APP_ID", "")).strip()
QQ_APP_SECRET = os.getenv("QQ_APP_SECRET", ENV.get("QQ_APP_SECRET", "")).strip()
# 可选：只响应这些 user_openid（逗号分隔），留空=不限制
QQ_ALLOWED_OPENIDS = os.getenv("QQ_ALLOWED_OPENIDS", ENV.get("QQ_ALLOWED_OPENIDS", "")).strip()
# 运行时白名单集合（留空时，首个发消息的用户会被自动授权）
_ALLOWED = {x.strip() for x in QQ_ALLOWED_OPENIDS.split(",") if x.strip()}
# 后端地址
BACKEND_API = os.getenv("BACKEND_API", ENV.get("BACKEND_API", "http://127.0.0.1:8000")).strip()
# 后端内部服务 token（访问受鉴权保护的后端接口用，见 .env 的 BOT_TOKEN）
BOT_TOKEN = os.getenv("BOT_TOKEN", ENV.get("BOT_TOKEN", "")).strip()

# 临时目录：接收用户文件 / 下载待发送文件
TMP_DIR = os.path.join(BASE_DIR, "qq_tmp")
os.makedirs(TMP_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QQ-BOT] %(levelname)s %(message)s",
)
logger = logging.getLogger("qq_bot")

# 文件大小上限（QQ 单文件硬限制 200MB，这里留余量）
MAX_SEND_SIZE = 150 * 1024 * 1024
# 单条文本回复最大长度（QQ 单聊有长度限制）
MAX_REPLY_LEN = 4000


# ===================== 工具函数 =====================
def _clean_keyword(text: str) -> str:
    """把用户消息里的指令词去掉，尽量提取检索关键词。"""
    if not text:
        return ""
    # 去掉常见指令前缀/词
    stop = ["帮我", "请", "麻烦", "下载", "发我", "发给我", "给我", "找一下", "找", "要",
            "文件", "合同", "那个", "这个", "一份", "一下", "把", "的", "关于", "有关",
            "查询", "搜索", "看看", "能否", "可以", "谢谢", "请问", "查"]
    kw = text
    for w in stop:
        kw = kw.replace(w, " ")
    # 去掉标点，压缩空白
    kw = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_\- ]", " ", kw)
    kw = re.sub(r"\s+", " ", kw).strip()
    return kw


def _is_file_request(text: str) -> bool:
    return any(w in text for w in ["下载", "发我", "发给我", "给我", "找", "要文件", "文件"])


def _is_report_request(text: str) -> bool:
    return any(w in text for w in ["汇总", "统计", "报告", "总结", "所有项目", "跨项目", "盘点"])


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _persist_allowed_openid(openid: str) -> None:
    """把 openid 追加写入 .env 的 QQ_ALLOWED_OPENIDS（首次自动授权，持久化）。"""
    env_path = os.path.join(BASE_DIR, ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith("QQ_ALLOWED_OPENIDS"):
            existing = line.split("=", 1)[1].strip() if "=" in line else ""
            vals = [v for v in existing.split(",") if v.strip()]
            if openid not in vals:
                vals.append(openid)
            lines[i] = f"QQ_ALLOWED_OPENIDS={','.join(vals)}\n"
            found = True
            break
    if not found:
        lines.append(f"\nQQ_ALLOWED_OPENIDS={openid}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    logger.info(f"[白名单] 已把 {openid} 写入 .env 的 QQ_ALLOWED_OPENIDS")


# ===================== 机器人客户端 =====================
class KnowledgeBot(botpy.Client):
    def __init__(self, intents, **kwargs):
        # 调大 HTTP 超时，避免文件上传接口（合并等）5 秒默认超时
        kwargs.setdefault("timeout", 60)
        super().__init__(intents=intents, **kwargs)

    # ---------- 单聊消息事件 ----------
    async def on_c2c_message_create(self, message: C2CMessage):
        openid = getattr(message.author, "user_openid", None)
        logger.info(f"[C2C] 收到消息 from={openid} content={message.content!r} attachments={len(message.attachments or [])}")
        try:
            await self._dispatch(message)
        except Exception as e:
            logger.exception("[C2C] 处理消息异常")
            await self._safe_reply(message, f"处理出错了：{e}")

    async def _dispatch(self, message: C2CMessage):
        global _ALLOWED
        openid = message.author.user_openid
        # 白名单校验：留空=首次自动授权该用户；已配置=严格校验
        if not _ALLOWED:
            _ALLOWED.add(openid)
            _persist_allowed_openid(openid)
            await self._safe_reply(message, "✅ 已将你设为唯一授权用户，从现在起只有你能使用本助手。")
        elif openid not in _ALLOWED:
            await self._safe_reply(message, "你没有权限使用本助手。")
            return

        # 1) 收到文件 → 保存
        if message.attachments:
            await self._handle_incoming_file(message)
            return

        # 2) 文本消息 → 意图分发
        text = (message.content or "").strip()
        if not text:
            await self._safe_reply(message, "请发文字或文件给我。")
            return

        if _is_report_request(text):
            await self._do_cross_analysis(message, text)
        elif _is_file_request(text):
            await self._do_file_search(message, text)
        else:
            await self._do_qa(message, text)

    # ---------- 通用：安全回复文本 ----------
    async def _safe_reply(self, message: C2CMessage, text: str):
        try:
            await message.reply(content=text[:MAX_REPLY_LEN])
        except Exception as e:
            logger.error(f"[C2C] 回复失败：{e}")

    # ---------- 后端 HTTP 调用 ----------
    async def _backend(self, method: str, path: str, **kwargs):
        url = f"{BACKEND_API}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        if BOT_TOKEN:
            headers["X-Bot-Token"] = BOT_TOKEN
        async with aiohttp.ClientSession() as s:
            async with s.request(method, url, timeout=aiohttp.ClientTimeout(total=300), headers=headers, **kwargs) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"后端返回 {resp.status}: {await resp.text()}")
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return await resp.json()
                return await resp.read()

    # ---------- 1. 智能问答 ----------
    async def _do_qa(self, message: C2CMessage, text: str):
        await self._safe_reply(message, "正在检索知识库并生成回答，请稍候……")
        try:
            data = await self._backend("POST", "/ask", json={"question": text, "top_k": 8, "project_id": None})
        except Exception as e:
            await self._safe_reply(message, f"问答失败：{e}")
            return
        answer = (data.get("answer") or "").strip()
        refs = data.get("references") or []
        if refs:
            names = [f"{r.get('original_name', '')}" for r in refs if r.get("original_name")]
            seen = []
            for n in names:
                if n and n not in seen:
                    seen.append(n)
            if seen:
                answer += "\n\n📎 相关文件：\n" + "\n".join(f"· {n}" for n in seen[:8])
        await self._safe_reply(message, answer or "没有找到相关内容。")

    # ---------- 2. 文件检索 + 发送 ----------
    async def _do_file_search(self, message: C2CMessage, text: str):
        kw = _clean_keyword(text)
        if not kw:
            await self._safe_reply(message, "请说明要找的文件，例如「下载 木里河 通讯合同」。")
            return
        await self._safe_reply(message, f"正在检索包含「{kw}」的文件……")
        try:
            results = await self._backend("GET", "/search", params={"q": kw})
        except Exception as e:
            await self._safe_reply(message, f"检索失败：{e}")
            return
        if not results:
            await self._safe_reply(message, f"没有找到包含「{kw}」的文件，换个关键词试试？")
            return

        # 文本列出匹配结果
        lines = [f"共找到 {len(results)} 个匹配文件，正在为你发送最匹配的："]
        for r in results[:8]:
            lines.append(f"· {r['original_name']}（{r.get('project_name', '')}）")
        await self._safe_reply(message, "\n".join(lines))

        # 发送第一个匹配文件
        first = results[0]
        fid = first.get("file_id")
        fname = first.get("original_name", "file")
        try:
            content = await self._backend("GET", f"/files/{fid}/download")
        except Exception as e:
            await self._safe_reply(message, f"下载文件失败：{e}")
            return
        local = os.path.join(TMP_DIR, fname)
        with open(local, "wb") as f:
            f.write(content)
        await self._send_file(message, local, fname)

    # ---------- 3. 跨项目统计/报告 ----------
    async def _do_cross_analysis(self, message: C2CMessage, text: str):
        await self._safe_reply(message, "正在跨项目统计并生成报告，请稍候……")
        try:
            data = await self._backend("POST", "/cross-project/analyze", json={"question": text})
        except Exception as e:
            await self._safe_reply(message, f"生成报告失败：{e}")
            return
        # 后端返回可能是 {report: ...} 或 {summary: ...} 等，尽量兼容
        if isinstance(data, dict):
            answer = data.get("report") or data.get("answer") or data.get("summary") or json.dumps(data, ensure_ascii=False)
        else:
            answer = str(data)
        await self._safe_reply(message, answer or "没有生成内容。")

    # ---------- 4. 接收用户发来的文件 ----------
    async def _handle_incoming_file(self, message: C2CMessage):
        saved = []
        async with aiohttp.ClientSession() as s:
            for att in message.attachments:
                url = getattr(att, "url", None)
                fname = getattr(att, "filename", None) or f"qq_file_{message.id}"
                if not url:
                    continue
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.read()
                    local = os.path.join(TMP_DIR, fname)
                    with open(local, "wb") as f:
                        f.write(data)
                    saved.append(local)
                except Exception as e:
                    logger.error(f"[C2C] 下载附件失败 {fname}：{e}")
        if saved:
            await self._safe_reply(message, f"已收到 {len(saved)} 个文件：\n" + "\n".join(os.path.basename(x) for x in saved))
        else:
            await self._safe_reply(message, "未能下载你发来的文件。")

    # ---------- 5. 发送文件到 QQ（分片上传） ----------
    async def _send_file(self, message: C2CMessage, file_path: str, file_name: str):
        if not os.path.exists(file_path):
            await self._safe_reply(message, "文件不存在，无法发送。")
            return
        size = os.path.getsize(file_path)
        if size > MAX_SEND_SIZE:
            await self._safe_reply(message, f"文件 {file_name} 超过 150MB 上限，暂不支持发送。")
            return
        try:
            file_info = await self._upload_media(message.author.user_openid, file_path, file_name, size)
        except Exception as e:
            logger.exception("[C2C] 文件上传失败")
            await self._safe_reply(message, f"文件发送失败：{e}")
            return
        try:
            await message.reply(msg_type=7, media={"file_info": file_info})
        except Exception as e:
            logger.exception("[C2C] 文件消息发送失败")
            await self._safe_reply(message, f"文件已上传但发送失败：{e}")

    async def _upload_media(self, openid: str, file_path: str, file_name: str, size: int) -> str:
        """分片上传本地文件到 QQ，返回 file_info。"""
        with open(file_path, "rb") as f:
            data = f.read()

        md5_10m = _md5(data[:10_002_432])

        # 1) 预上传
        route = Route("POST", "/v2/users/{openid}/upload_prepare", openid=openid)
        prep = await self.api._http.request(route, json={
            "file_type": 4,
            "file_size": str(size),
            "file_name": file_name,
            "md5": _md5(data),
            "sha1": _sha1(data),
            "md5_10m": md5_10m,
        })
        upload_id = prep.get("upload_id")
        parts = prep.get("parts") or []
        if not upload_id or not parts:
            raise RuntimeError(f"预上传失败：{prep}")

        # 2) 逐片 PUT 到 COS 预签名 URL + part_finish
        async with aiohttp.ClientSession() as s:
            offset = 0
            for p in parts:
                block_size = int(p.get("block_size", 0))
                chunk = data[offset: offset + block_size]
                if not chunk:
                    break
                # PUT 分片到预签名 URL
                async with s.put(p["presigned_url"], data=chunk,
                                 timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status not in (200, 204):
                        raise RuntimeError(f"分片 PUT 失败：HTTP {resp.status}")
                # 通知分片完成
                fin_route = Route("POST", "/v2/users/{openid}/upload_part_finish", openid=openid)
                await self.api._http.request(fin_route, json={
                    "upload_id": upload_id,
                    "part_index": p.get("index", 0),
                    "block_size": str(len(chunk)),
                    "md5": _md5(chunk),
                })
                offset += block_size

        # 3) 合并
        merge_route = Route("POST", "/v2/users/{openid}/files", openid=openid)
        merged = await self.api._http.request(merge_route, json={
            "file_type": 4,
            "upload_id": upload_id,
            "file_name": file_name,
        })
        file_info = merged.get("file_info") if isinstance(merged, dict) else None
        if not file_info:
            raise RuntimeError(f"合并失败：{merged}")
        return file_info


# ===================== 入口 =====================
def main():
    if not QQ_APP_ID or not QQ_APP_SECRET:
        print("=" * 60)
        print("未配置 QQ_APP_ID / QQ_APP_SECRET")
        print("请先到 q.qq.com 创建机器人，并把 AppID / AppSecret")
        print("写入 .env 文件的 QQ_APP_ID / QQ_APP_SECRET 后重新运行。")
        print("=" * 60)
        return

    # 订阅 C2C 单聊消息事件：GROUP_AND_C2C_EVENT = 1 << 25
    intents = botpy.Intents.none()
    intents.value = 1 << 25

    client = KnowledgeBot(intents=intents)
    logger.info("QQ 机器人网关启动中……")
    client.run(appid=QQ_APP_ID, secret=QQ_APP_SECRET)


if __name__ == "__main__":
    main()
