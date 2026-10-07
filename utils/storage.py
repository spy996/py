"""磁盘文本存储辅助：提取文本的写入与读取。"""
import os

from config import EXTRACT_DIR
from utils.text_extract import extract_text_from_file


def _content_path(stored_name: str) -> str:
    """根据文件名生成提取文本的磁盘路径"""
    return os.path.join(EXTRACT_DIR, f"{stored_name}.txt")


def _save_content(stored_name: str, content: str) -> None:
    """把提取的全文文本写到磁盘"""
    try:
        with open(_content_path(stored_name), "w", encoding="utf-8") as f:
            f.write(content or "")
    except Exception as e:
        print(f"[存储] 写磁盘失败：{e}")


def _load_content(db_file) -> str:
    """读取文件文本：优先磁盘，其次旧数据库 content 并自动迁移，最后现场提取"""
    path = _content_path(db_file.stored_name)
    # 1) 磁盘已有，直接读
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[读取] 读磁盘失败：{e}")
    # 2) 磁盘没有，但旧数据库 content 有数据 → 搬到磁盘
    if db_file.content and db_file.content.strip():
        _save_content(db_file.stored_name, db_file.content)
        return db_file.content
    # 3) 都没有 → 现场提取并写磁盘
    if os.path.exists(db_file.file_path):
        content = extract_text_from_file(db_file.file_path)
        _save_content(db_file.stored_name, content)
        return content
    return ""
