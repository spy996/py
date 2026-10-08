# -*- coding: utf-8 -*-
"""批量重新识别已有成果，回填「申请日期」等缺失字段（独立脚本，不依赖运行中的后端服务）。

用法：venv\\Scripts\\python.exe reidentify_achievements_batch.py
逻辑与 main.py 的 _reidentify_achievement_async 一致：
  - 只回填空字段，不覆盖已登记/已修正的值；
  - 申请日期识别到非空则覆盖（本次核心目标）。
"""
import os
import re
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, logger
from database import SessionLocal
from models import Achievement
from utils.text_extract import extract_text_from_file

MAX_WORKERS = 3


def _extract_fields(content: str, filename: str = "") -> dict:
    """用 DeepSeek 从成果文件文本识别字段（与 main.py 的 _extract_achievement_fields 同款 prompt）。"""
    if not content or not content.strip():
        return {}
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    text_part = content[:6000] + "\n……（中间省略）……\n" + content[-1500:]
    prompt = (
        "你是科技成果信息抽取专家。请阅读下面这份成果材料（专利证书/论文/软件著作权证书/获奖证书/标准等），"
        "抽取关键字段，只返回一个 JSON 对象：\n"
        "{\n"
        '  "name": "成果名称（专利名/论文题目/软著名/奖项名等）",\n'
        '  "category": "类型，从[专利,论文,软件著作权,获奖,标准,成果登记,鉴定报告,其他]选一个",\n'
        '  "status": "状态，从[在研,已授权,已发表,已登记,已获奖,已发布,其他]选一个",\n'
        '  "holder": "权利人/作者/完成人/专利权人",\n'
        '  "achieve_date": "取得日期（授权日/发表日期/获奖日期等），YYYY-MM-DD",\n'
        '  "application_date": "申请日期（专利/软著等证书上标注的申请日），YYYY-MM-DD，没有则填空串",\n'
        '  "remark": "备注（专利号/刊物/等级/编号等补充信息，可简略）"\n'
        "}\n\n"
        "严格规则：\n"
        "1. 只输出 JSON，禁止 markdown 代码块和任何解释\n"
        "2. 日期统一 YYYY-MM-DD，没有就填空串\n"
        "3. category/status 必须从给定候选里选最贴切的一个\n"
        "4. 无法判断的字段填空串\n\n"
        f"文件名：{filename}\n"
        f"材料文本：\n{text_part}"
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
    }
    resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    return {
        "name": str(data.get("name") or "").strip(),
        "category": str(data.get("category") or "").strip(),
        "status": str(data.get("status") or "").strip(),
        "holder": str(data.get("holder") or "").strip(),
        "achieve_date": str(data.get("achieve_date") or "").strip(),
        "application_date": str(data.get("application_date") or "").strip(),
        "remark": str(data.get("remark") or "").strip(),
    }


def _reidentify_one(a: Achievement) -> dict:
    """重新识别单条成果，回填后返回结果摘要。"""
    result = {"id": a.id, "name": a.name or a.file_name or "", "app_date": None, "error": None}
    db = SessionLocal()
    try:
        content = ""
        try:
            content = extract_text_from_file(a.file_path)
        except Exception as e:
            result["error"] = f"提取失败: {e}"

        fields = {}
        if content and content.strip():
            try:
                fields = _extract_fields(content, a.file_name or "")
            except Exception as e:
                result["error"] = f"识别失败: {e}"

        row = db.query(Achievement).filter(Achievement.id == a.id).first()
        if row is None:
            result["error"] = "记录不存在"
            return result

        new_app_date = (fields.get("application_date") or "").strip()
        if new_app_date:
            row.application_date = new_app_date
            result["app_date"] = new_app_date

        for attr, key in [
            ("name", "name"), ("category", "category"), ("status", "status"),
            ("holder", "holder"), ("achieve_date", "achieve_date"), ("remark", "remark"),
        ]:
            val = (fields.get(key) or "").strip()
            if val and not getattr(row, attr, None):
                setattr(row, attr, val)

        row.processing_status = "done"
        db.commit()
    except Exception as e:
        result["error"] = f"处理异常: {e}"
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
    return result


def main():
    db = SessionLocal()
    try:
        items = db.query(Achievement).order_by(Achievement.id).all()
    finally:
        db.close()

    # 只处理「有磁盘文件」的成果
    todo = [a for a in items if a.file_path and os.path.exists(a.file_path)]
    skipped = [a.id for a in items if not (a.file_path and os.path.exists(a.file_path))]
    print(f"成果总数 {len(items)}，待重新识别 {len(todo)} 条，跳过（无文件）{len(skipped)} 条")
    print(f"并发 {MAX_WORKERS} 路，开始……")

    done = 0
    filled = 0
    errors = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_reidentify_one, a): a for a in todo}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            if r["app_date"]:
                filled += 1
                print(f"[{done}/{len(todo)}] #{r['id']} {r['name'][:30]} → 申请日期 {r['app_date']}")
            elif r["error"]:
                errors.append(r)
                print(f"[{done}/{len(todo)}] #{r['id']} {r['name'][:30]} ✗ {r['error']}")
            else:
                print(f"[{done}/{len(todo)}] #{r['id']} {r['name'][:30]} 无申请日期")

    print("\n===== 完成 =====")
    print(f"处理 {done} 条，回填申请日期 {filled} 条，出错 {len(errors)} 条，耗时 {int(time.time() - t0)} 秒")
    if errors:
        print("出错明细：")
        for r in errors:
            print(f"  #{r['id']} {r['name'][:40]} → {r['error']}")


if __name__ == "__main__":
    main()
