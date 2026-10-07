"""文本提取工具：PDF / OCR / Excel / Docx / PPT / 图片等内容提取。

从 main.py 抽离出的纯函数模块，无数据库依赖，可独立复用与测试。
"""
import os
import io
import logging
import threading

import cv2
import numpy as np
import fitz  # PyMuPDF
from rapidocr_onnxruntime import RapidOCR
from openpyxl import load_workbook

logger = logging.getLogger("rd_platform")

# OCR 引擎懒加载：首次真正用到 OCR 时才初始化（模型加载很慢，避免拖慢后端启动）
_ocr_engine = None
_ocr_init_lock = threading.Lock()


def _get_ocr_engine():
    """按需初始化并返回全局 OCR 引擎（双重检查锁，线程安全）。"""
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_init_lock:
            if _ocr_engine is None:
                _ocr_engine = RapidOCR()
    return _ocr_engine


# OCR 引擎非线程安全，用锁串行化并发调用
_ocr_lock = threading.Lock()

# ============ 文本提取工具 ============
def _extract_text_from_pdf(file_bytes: bytes) -> str:
    """PDF 提取：优先文字层，扫描页走 OCR"""
    parts = []
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        print(f"[OCR调试] 打开 PDF 失败：{e}")
        return ""

    logger.debug(f"[OCR] PDF 共 {doc.page_count} 页")
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        logger.debug(f"[OCR] 第 {i + 1} 页 文字层长度 = {len(text)}")

        if len(text) >= 30:
            # 有正常文字层，直接用
            parts.append(text)
            logger.debug(f"[OCR] 第 {i + 1} 页 使用文字层，跳过 OCR")
        else:
            # 扫描件，渲染成图片做 OCR（200 DPI：中文 OCR 够用，速度约为 300 DPI 的 2.2 倍）
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            try:
                with _ocr_lock:
                    result, _ = _get_ocr_engine()(img_bytes)
            except Exception as e:
                logger.warning(f"[OCR] 第 {i + 1} 页 OCR 失败：{e}")
                continue
            if result:
                page_text = "\n".join(line[1] for line in result)
                parts.append(page_text)
                logger.debug(f"[OCR] 第 {i + 1} 页 OCR 识别出 {len(page_text)} 个字符")
            else:
                logger.debug(f"[OCR] 第 {i + 1} 页 OCR 无结果")

    doc.close()
    return "\n\n".join(parts)


def _pdf_needs_ocr(file_path: str, sample_pages: int = 5, text_threshold: int = 30) -> bool:
    """判断 PDF 是否为纯扫描件（抽样前几页均几乎无文字层 → 需要后台 OCR，避免上传阻塞）。"""
    try:
        doc = fitz.open(file_path)
        try:
            pages = min(sample_pages, doc.page_count)
            if pages <= 0:
                return False
            empty = 0
            for i in range(pages):
                if len(doc[i].get_text().strip()) < text_threshold:
                    empty += 1
            return empty == pages
        finally:
            doc.close()
    except Exception:
        return False

def _extract_text_from_excel(file_bytes: bytes) -> str:
    """从 .xlsx / .xlsm 提取所有单元格文字，按行拼接。"""
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception:
        return ""

    parts = []
    try:
        for ws in wb.worksheets:
            parts.append(f"【工作表：{ws.title}】")
            for row in ws.iter_rows(values_only=True):
                # 过滤空单元格，其余转成文字
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
    finally:
        wb.close()

    return "\n".join(parts)


def _extract_text_from_xls(file_bytes: bytes) -> str:
    """从旧版 .xls 提取所有单元格文字（用 xlrd）。"""
    try:
        import xlrd
    except ImportError:
        return "（未安装 xlrd，请运行：pip install xlrd）"
    try:
        wb = xlrd.open_workbook(file_contents=file_bytes)
    except Exception:
        return ""
    parts = []
    for ws in wb.sheets():
        parts.append(f"【工作表：{ws.name}】")
        for row_idx in range(ws.nrows):
            cells = []
            for col_idx in range(ws.ncols):
                cell = ws.cell(row_idx, col_idx)
                v = cell.value
                if v is None:
                    continue
                if cell.ctype == 3:  # 日期类型
                    try:
                        v = xlrd.xldate.xldate_as_datetime(v, wb.datemode).strftime("%Y-%m-%d")
                    except Exception:
                        v = str(v)
                s = str(v).strip()
                if s:
                    cells.append(s)
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_text_from_docx(file_path: str) -> str:
    """从 .docx 提取段落文字。"""
    try:
        from docx import Document
    except ImportError:
        return "（未安装 python-docx，请运行：pip install python-docx）"
    doc = Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs)


def _extract_text_from_pptx(file_bytes: bytes) -> str:
    """从 .pptx 提取所有幻灯片文字（标题 + 正文 + 表格）。"""
    try:
        from pptx import Presentation
    except ImportError:
        return "（未安装 python-pptx，请运行：pip install python-pptx）"
    try:
        prs = Presentation(io.BytesIO(file_bytes))
    except Exception:
        return ""
    parts = []
    for idx, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        texts.append(t)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        texts.append(" | ".join(cells))
        if texts:
            parts.append(f"【第 {idx} 页】" + "\n".join(texts))
    return "\n\n".join(parts)


def _extract_text_from_doc(file_path: str) -> str:
    """解析旧版 .doc：用本机 Word 的 COM 接口（需安装 Word + pywin32），失败返回提示。"""
    try:
        import win32com.client
    except ImportError:
        return "（解析 .doc 需要安装 pywin32：pip install pywin32，且本机需安装 MS Word）"
    word = None
    doc = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        try:
            word.DisplayAlerts = 0  # 0 = wdAlertsNone，避免弹窗
        except Exception:
            pass
        doc = word.Documents.Open(file_path, ReadOnly=True)
        return doc.Content.Text
    except Exception as e:
        return f"（解析 .doc 失败：{e}）"
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass


# 支持内容解析的文件扩展名（不支持的类型如 .gw 只存不解析）
SUPPORTED_EXTS = {
    ".txt", ".md", ".csv", ".log", ".json", ".py", ".html", ".xml",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx", ".ppt",
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
}


def is_supported_file(filename: str) -> bool:
    """判断文件是否支持内容解析；不支持的类型（如 .gw）只存不解析。"""
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in SUPPORTED_EXTS


#直接把图片字节转成图像，支持 jpg/png/bmp 等格式
def _preprocess_for_ocr(img):
    """OCR 前预处理：限制尺寸 + 灰度化 + CLAHE 对比度增强，显著提升发票/单据照片的识别率。
    超长边缩到 2400px（照片噪声多、OCR 慢），短边过小则放大（小字识别不清）。"""
    h, w = img.shape[:2]
    long_side = max(h, w)
    if long_side > 2400:
        scale = 2400.0 / long_side
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    elif long_side < 1200:
        scale = 1200.0 / long_side
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def _extract_text_from_image(file_bytes: bytes) -> str:
    """用 OCR 提取图片（截图/照片/扫描图）中的文字（含预处理，对发票/单据照片更稳）。"""
    # 直接从内存中的图片字节解码成图像（BGR 格式，是 OCR 引擎需要的格式）
    img = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return "（无法解析图片文件，可能是格式损坏）"
    img = _preprocess_for_ocr(img)

    result, _ = None, None
    with _ocr_lock:
        result, _ = _get_ocr_engine()(img)
    if not result:
        return "（未识别到文字）"

    # result 里每一项是 [坐标框, 文字, 置信度]
    return "\n".join(str(item[1]) for item in result)


def extract_text_from_file(file_path):
    """根据文件扩展名提取文本内容"""
    ext = os.path.splitext(file_path)[1].lower()

    # 纯文本文件
    if ext in [".txt", ".md", ".csv", ".log", ".json", ".py", ".html", ".xml"]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk", errors="ignore") as f:
                return f.read()

    # PDF 文件
    elif ext == ".pdf":
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception:
            return "（读取 PDF 文件失败）"
        return _extract_text_from_pdf(file_bytes)

    # Word 文档 (.docx)
    elif ext == ".docx":
        return _extract_text_from_docx(file_path)

    # 旧版 Word 文档 (.doc)
    elif ext == ".doc":
        return _extract_text_from_doc(file_path)

    # Excel 表格 (.xlsx / .xlsm)
    elif ext in (".xlsx", ".xlsm"):
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception:
            return "（读取 Excel 文件失败）"
        return _extract_text_from_excel(file_bytes)

    # 旧版 Excel (.xls)
    elif ext == ".xls":
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception:
            return "（读取 Excel 文件失败）"
        return _extract_text_from_xls(file_bytes)

    # PowerPoint (.pptx)
    elif ext == ".pptx":
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception:
            return "（读取 PPT 文件失败）"
        return _extract_text_from_pptx(file_bytes)

    # 旧版 PowerPoint (.ppt)
    elif ext == ".ppt":
        return "（暂不支持解析旧版 .ppt 文件，请用 PowerPoint 另存为 .pptx 后再上传）"

    # 图片（截图/照片/扫描图）
    
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception:
            return "（读取图片文件失败）"
        return _extract_text_from_image(file_bytes)

    # 其他类型
    else:
                return "（该文件类型暂不支持内容解析）"
