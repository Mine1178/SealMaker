# -*- coding: utf-8 -*-
"""
SealMaker · PDF 签章工作台（v1.5.0 起内置，v1.6.0 起复用 ui_widgets 共享构件）

算法借鉴自「PDF全能工具箱 v10.0.43」的 pdf_add_stamp / _add_stamp_process，
按 SealMaker 的架构做了如下重构：

  * 抽出纯函数层（prepare_stamp / stamp_pdfs / render_page_base / compose_preview），
    UI 与算法解耦；预览与正式签章共用同一套坐标 / 切片计算；
  * 印章来源可直接是内存中的 PIL 图像 —— 设计页生成的印章无需先落盘即可签章；
  * 去掉原版 `mm + 3` 的经验补偿，改为精确 mm→pt，与 508 DPI 导出的物理尺寸一致；
  * 去白底只在「整图完全不透明」时执行，避免误伤透明 PNG 内的白色细节；
  * 支持 /Rotate 旋转页：插入前用 page.derotation_matrix 校正坐标；
  * 印章角度：固定角度或「区间随机、每页独立」；随机值由 (种子, 文件名, 页码)
    哈希生成，可复现且预览与成品一致。骑缝章强制固定角度（整章旋转后切片，
    否则各片角度不同无法拼回完整章）；
  * 后台线程只通过 queue 与主线程通信，所有 Tk 调用都留在主线程。

功能边界（重要）：
  本模块做的是「可见印章图像叠加」（Image XObject），**不是**《电子签名法》意义上
  带数字证书 / 签名值 / 验签信息的电子签名（PAdES），不提供法律效力或完整性保护。
"""

import hashlib
import io
import json
import os
import queue
import re
import threading
import traceback
import tkinter as tk
from types import SimpleNamespace
from tkinter import ttk, filedialog, messagebox

from ui_widgets import SelectableLabel, install_list_copy, BigRadiobutton, BigCheckbutton

from PIL import Image, ImageChops, ImageDraw, ImageTk

try:                                   # PyMuPDF >= 1.24 推荐包名
    import pymupdf as fitz
except Exception:                      # 兼容老版本
    try:
        import fitz
    except Exception:
        fitz = None

PDF_ENGINE_OK = fitz is not None
PDF_ENGINE_NAME = ("PyMuPDF " + str(getattr(fitz, "VersionBind", "?"))) if fitz else "未安装"

_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
_BICUBIC = getattr(getattr(Image, "Resampling", Image), "BICUBIC", 3)

MM_TO_PT = 2.834645669          # 1 mm = 72 / 25.4 pt
PAGE_MARGIN_PT = 20.0           # 页面安全边距
POS_GRID = 10                   # 位置档位 1~10
CONFIG_NAME = "stamp_config.json"

STAMP_DEFAULTS = {
    "mode": "fixed",            # fixed=定点 / riding=骑缝
    "page_range": "all",        # first / all / custom
    "h_pos": 8, "v_pos": 8,     # 定点水平 / 垂直档位
    "stamp_size": 42,           # 定点印章长边（mm）
    "opacity": 100,             # 定点不透明度（%）
    "riding_side": "right",     # 骑缝贴边 left / right
    "vertical_pos": 5,          # 骑缝垂直档位
    "riding_width": 42,         # 骑缝整章宽度（mm）
    "riding_opacity": 80,       # 骑缝不透明度（%）
    "flatten": False,           # 是否顺带扁平化原文档批注 / 表单域
    # 印章角度（度，逆时针为正）：fixed=自定义固定角度 / range=指定区间随机角度
    "angle_mode": "fixed",
    "angle_fixed": 0,           # 固定角度 -180~180
    "angle_min": -8,            # 随机区间下限 -180~180
    "angle_max": 8,             # 随机区间上限 -180~180
    "angle_seed": 0,            # 随机序列种子，「换一组」时递增
    "stamp_source": "current",  # current=设计页当前印章 / file=本地图片
    "stamp_file": "",
}


# ─────────────────────────── 配置持久化 ───────────────────────────
def _config_paths():
    home = os.path.join(os.path.expanduser("~"), ".sealmaker")
    local = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(home, CONFIG_NAME), os.path.join(local, CONFIG_NAME)]


def load_config():
    cfg = dict(STAMP_DEFAULTS)
    for p in _config_paths():
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cfg.update({k: v for k, v in data.items() if k in STAMP_DEFAULTS})
                break
        except Exception:
            continue
    return cfg


def save_config(cfg):
    data = {k: cfg.get(k, v) for k, v in STAMP_DEFAULTS.items()}
    for p in _config_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            continue
    return False


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ─────────────────────────── 纯算法层 ───────────────────────────
def _dewhite(img):
    """白底图自动转透明；已有有效 alpha 的 PNG 原样保留。"""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] != 255:
        return img                       # 含透明像素，信任原图 alpha
    r, g, b, a = img.split()
    near_white = ImageChops.darker(
        ImageChops.darker(r.point(lambda p: 255 if p > 240 else 0),
                          g.point(lambda p: 255 if p > 240 else 0)),
        b.point(lambda p: 255 if p > 240 else 0))
    img.putalpha(ImageChops.multiply(a, ImageChops.invert(near_white)))
    return img


ANGLE_LIMIT = 180.0             # 角度取值范围：[-180°, 180°]


def resolve_page_angle(cfg, page_idx=0, doc_key=""):
    """解析某页应使用的印章旋转角度（度，逆时针为正）。

    * ``angle_mode="fixed"``：所有页统一使用 ``angle_fixed``。
    * ``angle_mode="range"``：每页在 ``[angle_min, angle_max]`` 内取一个伪随机值。
      用 ``(angle_seed, 文档名, 页码)`` 做 MD5 哈希生成，**可复现**：
      右栏预览与正式签章拿到同一角度，同时不同页 / 不同文件角度各异，
      「换一组」时递增 seed 即可得到新的一组角度。
    * 骑缝章（``mode=="riding"``）**强制退化为固定角度**，忽略区间随机配置。
    """
    fixed = _clamp(float(cfg.get("angle_fixed", 0)), -ANGLE_LIMIT, ANGLE_LIMIT)
    if cfg.get("mode") == "riding" or cfg.get("angle_mode", "fixed") != "range":
        return fixed
    lo = _clamp(float(cfg.get("angle_min", -8)), -ANGLE_LIMIT, ANGLE_LIMIT)
    hi = _clamp(float(cfg.get("angle_max", 8)), -ANGLE_LIMIT, ANGLE_LIMIT)
    if lo > hi:
        lo, hi = hi, lo
    seed = int(cfg.get("angle_seed", 0))
    digest = hashlib.md5(f"{seed}|{doc_key}|{page_idx}".encode("utf-8")).hexdigest()
    rnd = int(digest[:8], 16) / 0xFFFFFFFF
    return round(lo + (hi - lo) * rnd, 2)


def rotate_stamp(img, angle):
    """在透明底上逆时针旋转印章；expand=True 保证四角不被裁切。

    PyMuPDF 的 insert_image(rotate=) 只接受 0/90/180/270，任意角度必须
    在图像层预旋转，这样预览与 PDF 成品用的是同一张位图。
    """
    a = _clamp(float(angle), -ANGLE_LIMIT, ANGLE_LIMIT)
    if abs(a) < 0.05:
        return img
    return img.rotate(a, resample=_BICUBIC, expand=True)


def angle_rect(base_rect, rot_w, rot_h):
    """以未旋转矩形的中心为基准，按旋转后外接尺寸给出 (x0, y0, x1, y1)。

    印章的物理毫米尺寸不变，旋转后外接框变大只是几何结果；中心对齐可保证
    位置档位（1~10 档）在旋转前后指向同一个落点。
    """
    cx = (base_rect.x0 + base_rect.x1) / 2
    cy = (base_rect.y0 + base_rect.y1) / 2
    return (cx - rot_w / 2, cy - rot_h / 2,
            cx + rot_w / 2, cy + rot_h / 2)


def prepare_stamp(stamp_source, cfg):
    """把印章来源预处理成「定点基础图」与「骑缝整图」，全批次只做一次。

    stamp_source 可以是 PIL.Image.Image（设计页内存印章）或图片路径。
    定点图保留未旋转版本（逐页按需旋转）；骑缝整图按固定角度整体旋转后再切片。
    """
    if isinstance(stamp_source, Image.Image):
        base = stamp_source.convert("RGBA").copy()
    elif isinstance(stamp_source, (str, os.PathLike)) and os.path.isfile(stamp_source):
        base = Image.open(stamp_source).convert("RGBA")
    else:
        raise ValueError("印章来源无效：请先在「印章设计」页生成印章，或选择一张图片")
    base = _dewhite(base)
    w0, h0 = base.size
    if w0 < 2 or h0 < 2:
        raise ValueError("印章图片尺寸过小")
    # 上限保护：长边不超过 1200px，避免超大本地图导致 PDF 体积暴涨
    MAX_EDGE = 1200
    if max(w0, h0) > MAX_EDGE:
        c = MAX_EDGE / max(w0, h0)
        base = base.resize((max(1, int(w0 * c)), max(1, int(h0 * c))), _LANCZOS)
        w0, h0 = base.size

    # 关键修复：保留原始分辨率（约 508 DPI），不再把印章缩到「pt 像素数」，
    # 否则嵌入 PDF 后有效分辨率只剩 ~72 DPI 而发糊。落点矩形由 mm→pt 换算
    # 独立给出（下面返回的 *_w/*_h 均为 pt），PyMuPDF 自行把高清图缩进矩形。
    def _make(opacity_pct, size_pt, by_long_edge):
        src = base.copy()
        ratio = _clamp(float(opacity_pct) / 100.0, 0.1, 1.0)
        src.putalpha(src.getchannel("A").point(lambda p: int(p * ratio)))
        # 仅按比例换算「pt 尺寸」，不缩放图像本身
        s = size_pt / (max(w0, h0) if by_long_edge else w0)
        return src, w0 * s, h0 * s, s

    size_pt_fixed = float(cfg.get("stamp_size", 42)) * MM_TO_PT
    img_fixed, fwp, fhp, sf = _make(cfg.get("opacity", 100), size_pt_fixed, True)
    buf = io.BytesIO()
    img_fixed.save(buf, format="PNG")
    size_pt_riding = float(cfg.get("riding_width", 42)) * MM_TO_PT
    img_riding, rwp, rhp, sr = _make(cfg.get("riding_opacity", 80), size_pt_riding, False)
    # 骑缝章：整章按「固定角度」旋转后再等分切片。
    # 若每片角度不同，跨页就拼不回一枚完整的章，因此这里不接受区间随机角度。
    riding_angle = _clamp(float(cfg.get("angle_fixed", 0)), -ANGLE_LIMIT, ANGLE_LIMIT)
    img_riding = rotate_stamp(img_riding, riding_angle)

    return {
        "fixed_img": img_fixed,          # 未旋转，定点模式逐页按需旋转（原始分辨率）
        "fixed_bytes": buf.getvalue(),   # 兼容未旋转场景
        "fixed_w": fwp, "fixed_h": fhp, "fixed_s": sf,   # pt 尺寸 + pt/px 比例
        "riding_img": img_riding,        # 已按固定角度整体旋转（原始分辨率）
        "riding_px_w": img_riding.width, "riding_px_h": img_riding.height,
        "riding_s": sr,                  # pt/px，骑缝切片与落点共用
        "base": base,
    }


def parse_page_range(text, total_pages):
    """解析 `1,3-5,8` 形式的 1-based 页码，返回去重升序的 0-based 列表。"""
    pages = []
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if "-" in item:
                s, e = item.split("-", 1)
                s, e = int(s.strip()), int(e.strip())
                if s > e:
                    s, e = e, s
                pages.extend(range(max(1, s) - 1, min(total_pages, e)))
            else:
                n = int(item)
                if 1 <= n <= total_pages:
                    pages.append(n - 1)
        except (ValueError, TypeError):
            continue
    return sorted({p for p in pages if 0 <= p < total_pages})


def target_pages(doc, cfg, page_range_str=""):
    mode = cfg.get("mode", "fixed")
    rng = cfg.get("page_range", "all")
    if mode == "riding":                 # 骑缝章必须跨整份文档切片
        return list(range(len(doc)))
    if rng == "first":
        return [0] if len(doc) else []
    if rng == "all":
        return list(range(len(doc)))
    return parse_page_range(page_range_str, len(doc))


def fixed_rect(page, fw, fh, cfg):
    """定点章在「页面可视坐标」中的矩形（1~10 档线性映射）。"""
    pw, ph = page.rect.width, page.rect.height
    usable_w = max(0.0, pw - 2 * PAGE_MARGIN_PT - fw)
    usable_h = max(0.0, ph - 2 * PAGE_MARGIN_PT - fh)
    hp = _clamp(int(cfg.get("h_pos", 8)), 1, POS_GRID)
    vp = _clamp(int(cfg.get("v_pos", 8)), 1, POS_GRID)
    x0 = PAGE_MARGIN_PT + usable_w * (hp - 1) / (POS_GRID - 1)
    y0 = PAGE_MARGIN_PT + usable_h * (vp - 1) / (POS_GRID - 1)
    return fitz.Rect(x0, y0, x0 + fw, y0 + fh)


def riding_rect(page, slice_w, rh, cfg, y0=None):
    """骑缝章第 i 片在页面可视坐标中的矩形；y0 全文档统一，保证跨页对齐。"""
    pw, ph = page.rect.width, page.rect.height
    if y0 is None:
        usable_h = max(0.0, ph - 2 * PAGE_MARGIN_PT - rh)
        vp = _clamp(int(cfg.get("vertical_pos", 5)), 1, POS_GRID)
        y0 = PAGE_MARGIN_PT + usable_h * (vp - 1) / (POS_GRID - 1)
    x0 = 0.0 if cfg.get("riding_side", "right") == "left" else pw - slice_w
    return fitz.Rect(x0, y0, x0 + slice_w, y0 + rh)


def slice_bounds(total_w, count):
    """整章等分成 count 片；末片吃掉取整余数，避免丢像素。"""
    step = total_w / max(1, count)
    out = []
    for i in range(count):
        xs = int(i * step)
        xe = total_w if i == count - 1 else int((i + 1) * step)
        out.append((xs, max(xs + 1, xe)))
    return out


def _insert(page, rect_visible, stream):
    """插入图像；旋转页先用 derotation_matrix 把可视坐标换算回页面坐标。"""
    if not fitz:
        raise RuntimeError("未安装 PyMuPDF")
    rot = page.rotation or 0
    target = rect_visible * page.derotation_matrix if rot else rect_visible
    page.insert_image(target, stream=stream, overlay=True, keep_proportion=False)


def probe_pdf(path):
    """读取页数 / PDF 版本 / 加密状态，供文件列表展示。"""
    info = {"pages": 0, "version": "?", "encrypted": False, "error": None}
    try:
        with open(path, "rb") as f:
            m = re.search(rb"%PDF-(\d+\.\d+)", f.read(32))
        info["version"] = m.group(1).decode("ascii") if m else "未知"
    except Exception:
        pass
    if not fitz:
        return info
    doc = None
    try:
        doc = fitz.open(path)
        info["pages"] = len(doc)
        info["encrypted"] = bool(doc.is_encrypted)
    except Exception as e:
        info["error"] = str(e)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return info


def stamp_pdfs(file_list, stamp_source, cfg, page_range_str="",
               emit=None, ask_overwrite=None, is_cancelled=None):
    """批量签章（后台线程调用）。

    emit(kind, payload) 只负责把消息投递回主线程，不做任何 Tk 调用。
    ask_overwrite(msg) -> True 全部覆盖 / False 全部跳过 / None 终止。
    返回 {"total", "success", "fail", "outputs", "cancelled"}。
    """
    def _emit(kind, payload=None):
        if emit:
            emit(kind, payload)

    result = {"total": len(file_list), "success": 0, "fail": [], "outputs": [], "cancelled": False}
    if not fitz:
        _emit("fatal", "未安装 PyMuPDF，无法签章。请执行：pip install pymupdf")
        return result

    mode = cfg.get("mode", "fixed")
    prep = prepare_stamp(stamp_source, cfg)
    overwrite_all = None

    for idx, pdf_path in enumerate(file_list):
        if is_cancelled and is_cancelled():
            result["cancelled"] = True
            break
        fname = os.path.basename(pdf_path)
        _emit("progress", (idx, len(file_list), fname))
        doc = None
        try:
            suffix = "定点已签章" if mode == "fixed" else "骑缝已签章"
            out_path = os.path.splitext(pdf_path)[0] + "_" + suffix + ".pdf"
            if os.path.exists(out_path):
                if overwrite_all is None:
                    if ask_overwrite is None:
                        overwrite_all = True
                    else:
                        overwrite_all = ask_overwrite(
                            f"输出文件已存在：\n{os.path.basename(out_path)}\n\n"
                            "是 = 全部覆盖　否 = 全部跳过　取消 = 终止批次")
                    if overwrite_all is None:
                        result["cancelled"] = True
                        break
                if not overwrite_all:
                    continue

            doc = fitz.open(pdf_path)
            if doc.is_encrypted:
                raise RuntimeError("PDF 已加密 / 受密码保护，请先解密")
            pages = target_pages(doc, cfg, page_range_str)
            if not pages:
                raise RuntimeError("有效页码范围为空")
            if mode == "riding" and len(pages) < 2:
                raise RuntimeError("骑缝签章至少需要 2 页")

            if mode == "fixed":
                angle_cache = {}          # 相同角度复用 PNG bytes，避免逐页重复编码
                for pi in pages:
                    page = doc[pi]
                    ang = resolve_page_angle(cfg, pi, fname)
                    key = round(ang, 2)
                    cached = angle_cache.get(key)
                    if cached is None:
                        rot = rotate_stamp(prep["fixed_img"], ang)
                        buf = io.BytesIO()
                        rot.save(buf, format="PNG")
                        cached = (buf.getvalue(), rot.width * prep["fixed_s"],
                                  rot.height * prep["fixed_s"])
                        angle_cache[key] = cached
                    stream, rw, rh = cached
                    base_rect = fixed_rect(page, prep["fixed_w"], prep["fixed_h"], cfg)
                    rect = fitz.Rect(*angle_rect(base_rect, rw, rh))
                    _insert(page, rect, stream)
            else:
                s = prep["riding_s"]
                px_w, px_h = prep["riding_px_w"], prep["riding_px_h"]
                bounds = slice_bounds(px_w, len(pages))
                first = doc[pages[0]]
                rh_pt = px_h * s
                usable_h = max(0.0, first.rect.height - 2 * PAGE_MARGIN_PT - rh_pt)
                vp = _clamp(int(cfg.get("vertical_pos", 5)), 1, POS_GRID)
                y0 = PAGE_MARGIN_PT + usable_h * (vp - 1) / (POS_GRID - 1)
                for i, pi in enumerate(pages):
                    page = doc[pi]
                    xs, xe = bounds[i]
                    piece = prep["riding_img"].crop((xs, 0, xe, px_h))
                    buf = io.BytesIO()
                    piece.save(buf, format="PNG")
                    rect = riding_rect(page, (xe - xs) * s, rh_pt, cfg, y0=y0)
                    _insert(page, rect, buf.getvalue())

            # 说明：bake() 扁平化的是原文档的批注 / 表单域；insert_image 生成的是
            # 独立的 Image XObject，仍可被 PDF 编辑器选中。此选项不等于「印章防抠图」。
            if cfg.get("flatten"):
                try:
                    doc.bake(annots=True, widgets=True)
                except (TypeError, AttributeError):
                    try:
                        doc.bake()          # 旧版 PyMuPDF 无参数
                    except Exception:
                        pass                # 更旧版本无 bake()，跳过不影响签章
            doc.save(out_path, garbage=4, deflate=True)
            result["success"] += 1
            result["outputs"].append(out_path)
        except Exception as e:
            result["fail"].append(f"{fname}：{e}")
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
    _emit("done", result)
    return result


PREVIEW_BASE_W = 1100            # 页面底图统一渲染宽度；显示时按预览区缩放
_BLANK_PAGE_PT = (595.0, 842.0)  # 无 PDF 时的 A4 纵向白页


def render_page_base(pdf_path, cfg, page_range_str="", base_width=PREVIEW_BASE_W):
    """把「第一个目标页」渲染成位图底图，供右栏实时预览反复合成。

    未选择 PDF / 文档不可读时回退为一张空白 A4 纸（仍可实时看到印章落点），
    使「所见即所得」在签章页一打开就生效，而不是等到点了预览按钮。
    页面纵横比直接取自 PDF，横向文档得到横向底图、纵向得到纵向底图。
    """
    err = None
    if pdf_path and fitz and os.path.isfile(pdf_path):
        doc = None
        try:
            doc = fitz.open(pdf_path)
            if doc.is_encrypted:
                raise RuntimeError("PDF 已加密 / 受密码保护")
            pages = target_pages(doc, cfg, page_range_str)
            if not pages:
                raise RuntimeError("有效页码范围为空")
            page = doc[pages[0]]
            pw, ph = page.rect.width, page.rect.height
            scale = _clamp(base_width / max(1.0, pw), 0.35, 3.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            base = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return {"base": base, "pt": (pw, ph), "count": len(pages),
                    "index": pages[0] + 1, "label": os.path.basename(pdf_path),
                    "blank": False, "error": None}
        except Exception as e:
            err = str(e)
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    pw, ph = _BLANK_PAGE_PT
    scale = base_width / pw
    base = Image.new("RGB", (int(pw * scale), int(ph * scale)), "#ffffff")
    dr = ImageDraw.Draw(base)
    # 简洁的文档版式示意：标题条 + 若干段落线，避免密集横线显得像占位符
    dr.rectangle((int(58 * scale), int(46 * scale),
                  int((pw - 190) * scale), int(62 * scale)), fill="#dbe2ec")
    y = int(96 * scale)
    for i in range(11):
        if y > int((ph - 96) * scale):
            break
        width = pw - 116 - (i % 5) * 48
        dr.rectangle((int(58 * scale), y, int((58 + width) * scale),
                      y + int(5 * scale)), fill="#edf0f5")
        y += int(46 * scale)
    dr.rectangle((int((pw - 210) * scale), int((ph - 96) * scale),
                  int((pw - 58) * scale), int((ph - 90) * scale)), fill="#e4e9f0")
    return {"base": base, "pt": (pw, ph), "count": 4, "index": 1,
            "label": "未选择 PDF（空白页示意）", "blank": True, "error": err}


def compose_preview(page, stamp_prep, cfg):
    """把印章合成到页面底图上；坐标/切片与 stamp_pdfs 完全共用。

    拆成「底图」与「合成」两步是为了实时预览：拖动滑块只需重做本函数的
    PIL 合成（几十毫秒），无需重新渲染 PDF 页面。
    """
    base = page["base"]
    pw, ph = page["pt"]
    scale = base.width / max(1.0, pw)
    canvas = base.convert("RGBA")
    geom = SimpleNamespace(rect=SimpleNamespace(width=pw, height=ph))
    mode = cfg.get("mode", "fixed")

    if mode == "fixed":
        fw, fh = stamp_prep["fixed_w"], stamp_prep["fixed_h"]
        base_rect = fixed_rect(geom, fw, fh, cfg)
        # 与 stamp_pdfs 用同一个角度解析函数，保证预览=成品
        ang = resolve_page_angle(cfg, page["index"] - 1, page["label"])
        rot = rotate_stamp(stamp_prep["fixed_img"], ang)
        x0, y0, x1, y1 = angle_rect(base_rect, rot.width * stamp_prep["fixed_s"],
                                    rot.height * stamp_prep["fixed_s"])
        stamp = rot.resize((max(1, int((x1 - x0) * scale)),
                            max(1, int((y1 - y0) * scale))), _LANCZOS)
        canvas.paste(stamp, (int(x0 * scale), int(y0 * scale)), stamp)
        caption = f"定点 {page['index']}/{page['count']} 页"
    else:
        s = stamp_prep["riding_s"]
        px_w, px_h = stamp_prep["riding_px_w"], stamp_prep["riding_px_h"]
        n = max(2, page["count"])
        xs, xe = slice_bounds(px_w, n)[0]
        rect = riding_rect(geom, (xe - xs) * s, px_h * s, cfg)
        piece = stamp_prep["riding_img"].crop((xs, 0, xe, px_h))
        piece = piece.resize((max(1, int((xe - xs) * s * scale)),
                              max(1, int(px_h * s * scale))), _LANCZOS)
        canvas.paste(piece, (int(rect.x0 * scale), int(rect.y0 * scale)), piece)
        caption = f"骑缝 1/{n} 片"
    return canvas.convert("RGB"), caption


# ─────────────────────────── 界面工作台 ───────────────────────────
class _FallbackButton(ttk.Button):
    """独立运行本模块时的按钮兜底（主程序会传入 RoundedButton）。"""

    def __init__(self, parent, text, command, **kwargs):
        super().__init__(parent, text=text, command=command)

    def set_text(self, text):
        self.configure(text=text)

    def set_enabled(self, on):
        self.configure(state="normal" if on else "disabled")


# 正文可选文本 / 列表行复制构件统一由 ui_widgets 提供


class StampWorkspace(tk.Frame):
    """签章工作台：左栏设置区 + 右栏实时预览区 + 全宽动作条。

    右栏为所见即所得的页面级预览：设置项变化 140ms 防抖后重新合成印章，
    PDF 页面底图按文件缓存，只有文件 / 页码范围 / 模式变化才重新渲染。
    """

    PLACEHOLDER = "1,3-5"

    def __init__(self, master, theme, seal_provider, on_status, on_go_design, button_cls=None):
        super().__init__(master, bg=theme["bg"])
        self.theme = theme
        self.seal_provider = seal_provider
        self.on_status = on_status
        self.on_go_design = on_go_design
        self.button_cls = button_cls or _FallbackButton
        SelectableLabel.on_copy = on_status      # 正文复制后状态栏即时反馈

        self.cfg = load_config()
        self.vars = {}
        self.selected_files = []
        self.file_info = []
        self.seal_img = None
        self.seal_size_mm = None
        self._size_touched = False
        self._auto_syncing = False      # 程序写回滑块变量时置位，避免误判为用户操作
        self._thumb_ref = None
        self._preview_ref = None
        # 实时预览：页面底图缓存 / 印章预处理缓存 / 合成结果 / 防抖任务
        self._page_cache = {"key": None, "page": None}
        self._stamp_cache = {"key": None, "prep": None}
        self._composite = None
        self._caption = ""
        self._preview_job = None
        self._queue = queue.Queue()
        self._running = False
        self._cancel = False
        self._last_result = None

        self._init_styles()
        self._build()
        self._sync_from_config()
        self.after(120, self._poll_queue)

    # ── 样式 ──
    def _init_styles(self):
        t = self.theme
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        # 单选 / 复选已改用 ui_widgets.BigRadiobutton / BigCheckbutton 自绘，
        # 不再需要 ttk 样式
        st.configure("Seal.TEntry", padding=3, fieldbackground="#ffffff",
                     bordercolor=t["border"], lightcolor=t["border"], darkcolor=t["border"])
        st.configure("Seal.Horizontal.TScale", background=t["card"], troughcolor="#e4e9f0",
                     bordercolor=t["border"], lightcolor=t["accent"], darkcolor=t["accent"])
        st.configure("Seal.Horizontal.TProgressbar", background=t["accent"],
                     troughcolor="#e4e9f0", bordercolor=t["border"])
        st.configure("Seal.Vertical.TScrollbar", background="#cfd6e0", troughcolor=t["card"],
                     bordercolor=t["card"], arrowcolor="#7a8699")

    # ── 通用构件 ──
    def _titled_card(self, parent, title):
        """白卡片骨架，返回 (卡片, 内容区, 标题 Label)；由调用方决定 pack/grid。"""
        t = self.theme
        outer = tk.Frame(parent, bg=t["card"], highlightbackground=t["border"],
                         highlightthickness=1)
        head = tk.Frame(outer, bg=t["card"])
        head.pack(fill=tk.X, padx=12, pady=(7, 2))
        bar = tk.Frame(head, bg=t["accent"], width=3, height=13)
        bar.pack(side=tk.LEFT, padx=(0, 6))
        bar.pack_propagate(False)
        title_lab = tk.Label(head, text=title, bg=t["card"], fg=t["text"],
                             font=("微软雅黑", 10, "bold"))
        title_lab.pack(side=tk.LEFT)
        outer._head = head              # 供卡片在标题栏右侧放单行状态文字
        inner = tk.Frame(outer, bg=t["card"])
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=(2, 7))
        return outer, inner, title_lab

    def _scale_row(self, parent, label, key, lo, hi, unit, registry,
                   on_user=None, grid_row=None, var=None):
        """一行「标签 + 滑块 + 数值框 + 单位」。

        父容器须设置 columnconfigure(0, weight=1)，行用 sticky="ew" 撑满整宽，
        保证四行滑块长度完全一致，且窗口缩放时同步伸缩。
        """
        t = self.theme
        row = tk.Frame(parent, bg=t["card"])
        if grid_row is None:
            row.pack(fill=tk.X, pady=3)
        else:
            row.grid(row=grid_row, column=0, sticky="ew", pady=3)
        tk.Label(row, text=label, bg=t["card"], fg=t["text"], width=9, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        if var is None:
            var = tk.IntVar(value=int(self.cfg.get(key, lo)))
        sc = ttk.Scale(row, from_=lo, to=hi, orient=tk.HORIZONTAL, variable=var,
                       style="Seal.Horizontal.TScale",
                       command=lambda v, x=var: x.set(int(round(float(v)))))
        sc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        ent = ttk.Entry(row, textvariable=var, width=6, justify="center",
                        style="Seal.TEntry", font=("微软雅黑", 9))
        ent.pack(side=tk.LEFT)
        tk.Label(row, text=unit, bg=t["card"], fg=t["subtle"], width=3, anchor="w",
                 font=("微软雅黑", 8)).pack(side=tk.LEFT, padx=(3, 0))

        def _validate(_e=None):
            try:
                v = int(float(var.get()))
            except Exception:
                v = int(self.cfg.get(key, lo))
            var.set(_clamp(v, lo, hi))
            if on_user:
                on_user()

        ent.bind("<FocusOut>", _validate)
        ent.bind("<Return>", _validate)
        if on_user:
            sc.configure(command=lambda v, x=var, f=_validate: (x.set(int(round(float(v)))), f()))
        self.vars[key] = var
        registry.extend((sc, ent))
        return var

    def _btn(self, parent, text, command, primary=False, ghost=False):
        t = self.theme
        if primary:
            return self.button_cls(parent, text, command, bg=t["accent"],
                                   font=("微软雅黑", 10, "bold"), padx=22, pady=7)
        bg = "#eef2f8" if ghost else "#f3f5f9"
        fg = t["accent"] if ghost else t["text"]
        return self.button_cls(parent, text, command, bg=bg, fg=fg,
                               font=("微软雅黑", 9), padx=14, pady=5)

    # ── 组装 ──
    def _param_panel(self, parent):
        """一组签章参数面板；控件行构成单一内容块，在卡内垂直居中。

        说明文字统一放到卡片标题栏右侧（与 ①②③ 各卡一致），既避免
        「控件在上、说明沉底」的孤行，也省出一整行高度，减少设置区滚动量。
        """
        t = self.theme
        z = tk.Frame(parent, bg=t["card"])
        box = tk.Frame(z, bg=t["card"])
        box.pack(fill=tk.BOTH, expand=True)
        holder = tk.Frame(box, bg=t["card"])
        holder.columnconfigure(0, weight=1)
        holder.pack(fill=tk.X, expand=True, anchor="c")
        return z, holder

    # ── 右栏所见即所得预览 ──
    def _schedule_preview(self, *_):
        """参数变化统一 140ms 防抖；连续拖动滑块只重算一次。"""
        if self._preview_job:
            try:
                self.after_cancel(self._preview_job)
            except Exception:
                pass
        self._preview_job = self.after(140, self._refresh_preview)

    def _page_key(self, cfg):
        path = self.selected_files[0] if self.selected_files else ""
        return (path, cfg.get("mode"), cfg.get("page_range"), self._range_text())

    def _get_page_base(self, cfg):
        """PDF 页面底图按「文件 + 模式 + 页码范围」缓存，避免每次调滑块都渲染 PDF。"""
        key = self._page_key(cfg)
        if self._page_cache["key"] == key and self._page_cache["page"] is not None:
            return self._page_cache["page"]
        page = render_page_base(
            self.selected_files[0] if self.selected_files else "",
            cfg, self._range_text())
        self._page_cache = {"key": key, "page": page}
        return page

    def _get_stamp_prep(self, source, cfg):
        """印章预处理（去白底 / 透明度 / 缩放）按来源与尺寸参数缓存。"""
        key = (id(source) if isinstance(source, Image.Image) else source,
               cfg.get("stamp_size"), cfg.get("opacity"),
               cfg.get("riding_width"), cfg.get("riding_opacity"),
               cfg.get("angle_fixed"))       # 骑缝整图的旋转依赖固定角度
        if self._stamp_cache["key"] == key and self._stamp_cache["prep"] is not None:
            return self._stamp_cache["prep"]
        prep = prepare_stamp(source, cfg)
        self._stamp_cache = {"key": key, "prep": prep}
        return prep

    def _refresh_preview(self):
        self._preview_job = None
        if getattr(self, "preview_canvas", None) is None:
            return
        try:
            cfg = self.collect_config()
            source = self.get_stamp_source(quiet=True)
            if source is None:
                self._composite = None
                if self.source_var.get() == "file":
                    self._show_preview_message(
                        "点「选择」指定本地印章图片", "当前使用本地图片 · 尚未选择文件")
                else:
                    self._show_preview_message(
                        "请先在「印章设计」页生成印章", "当前章源为空 · 生成印章后自动预览")
                return
            page = self._get_page_base(cfg)
            prep = self._get_stamp_prep(source, cfg)
            self._composite, self._caption = compose_preview(page, prep, cfg)
            self._paint_preview()
            if cfg["mode"] == "fixed":
                detail = (f"水平 {cfg['h_pos']} · 垂直 {cfg['v_pos']} · "
                          f"{cfg['stamp_size']}mm · {cfg['opacity']}%")
            else:
                side = "左" if cfg["riding_side"] == "left" else "右"
                detail = (f"{side}侧贴边 · 垂直 {cfg['vertical_pos']} · "
                          f"{cfg['riding_width']}mm · {cfg['riding_opacity']}%")
            ang = resolve_page_angle(cfg, page["index"] - 1, page["label"])
            tag = ("随机" if cfg["mode"] == "fixed" and cfg["angle_mode"] == "range"
                   else "固定")
            detail += f" · {tag} {ang:g}°"
            note = ""
            if page.get("error"):
                note = " · 已回退空白页"
            elif page["blank"]:
                note = " · 空白页示意"
            elif cfg["mode"] == "riding" and page["count"] < 2:
                note = " · 骑缝需≥2页"
            self.preview_info.set_text(f"{self._caption}  |  {detail}{note}")
            self._update_file_resolution()      # 落章尺寸变化（滑块）也会刷新提示
        except Exception as e:
            self._composite = None
            self._show_preview_message(f"预览不可用：{e}")

    def _paint_preview(self, *_):
        """仅把已合成图按画布尺寸缩放显示；画布尺寸变化不触发 PDF 重渲染。"""
        c = getattr(self, "preview_canvas", None)
        if c is None or self._composite is None:
            return
        w, h = c.winfo_width(), c.winfo_height()
        if w < 30 or h < 30:
            return
        img = self._composite
        pad = 20                                   # 页面四周留白，避免纸贴边
        fit = min((w - 2 * pad) / img.width, (h - 2 * pad) / img.height, 1.0)
        dw, dh = max(1, int(img.width * fit)), max(1, int(img.height * fit))
        self._preview_ref = ImageTk.PhotoImage(img.resize((dw, dh), _LANCZOS))
        c.delete("all")
        x, y = (w - dw) // 2, (h - dh) // 2
        c.create_rectangle(x + 5, y + 5, x + dw + 5, y + dh + 5,
                           fill="#d5dce5", outline="")   # 轻阴影，让白纸有"纸感"
        c.create_image(x, y, anchor="nw", image=self._preview_ref)
        c.create_rectangle(x, y, x + dw, y + dh, outline=self.theme["border"])

    def _show_preview_message(self, text, info=None):
        """空状态：淡色纸面示意 + 中央引导；信息条给「不同的」简短状态。

        旧实现把同一句话同时画在画布中央和底部信息条上，读起来像重复报错；
        现在画布负责引导动作，信息条负责说明当前状态，两者职责分开。
        """
        c = getattr(self, "preview_canvas", None)
        if c is None:
            return
        w, h = c.winfo_width(), c.winfo_height()
        if w < 30 or h < 30:
            return
        t = self.theme
        c.delete("all")
        pw = min(w - 48, (h - 56) / 1.414)
        ph = pw * 1.414
        x, y = (w - pw) / 2, (h - ph) / 2
        c.create_rectangle(x + 4, y + 4, x + pw + 4, y + ph + 4,
                           fill="#d5dce5", outline="")
        c.create_rectangle(x, y, x + pw, y + ph, fill="#ffffff", outline=t["border"])
        for i in range(7):
            yy = y + 26 + i * 22
            if yy > y + ph - 20:
                break
            c.create_line(x + 16, yy, x + pw - 16 - (i % 3) * 24, yy,
                           fill="#eef1f6")
        c.create_text(w / 2, h / 2, text=text, fill=t["subtle"],
                      font=("微软雅黑", 10), justify="center")
        self.preview_info.set_text(info or "预览待就绪 · 设置项变化后自动刷新")

    def _build(self):
        t = self.theme
        body = tk.Frame(self, bg=t["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 6))
        # 左栏设置 / 右栏预览，各占一半；底部动作卡全宽
        body.columnconfigure(0, weight=1, uniform="lr")
        body.columnconfigure(1, weight=1, uniform="lr")
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)

        left = tk.Frame(body, bg=t["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right = tk.Frame(body, bg=t["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        # 设置项比一屏略多：左栏可纵向滚动，右栏预览始终固定可见。
        # 高度够时参数卡自动撑满（无滚动条），窗口变矮才出现滚动条。
        left_canvas = tk.Canvas(left, bg=t["bg"], highlightthickness=0, width=200)
        left_sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=left_canvas.yview,
                                style="Seal.Vertical.TScrollbar")
        left_canvas.configure(yscrollcommand=left_sb.set)
        left_sb.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(left_canvas, bg=t["bg"])
        self.left_inner = inner
        self.left_canvas = left_canvas
        self._left_win = left_canvas.create_window((0, 0), window=inner, anchor="nw")

        left_size = [0, 0, 0]

        def _sync_left(e=None):
            cw = max(1, left_canvas.winfo_width())
            ch = left_canvas.winfo_height()
            need = inner.winfo_reqheight()
            if (cw, ch, need) == tuple(left_size):
                return                       # 防止 Configure 递归抖动
            left_size[:] = [cw, ch, need]
            h = max(ch, need)
            # 富余高度全部交给「② 待签文件区」这一张卡（唯一 expand=True 的卡片）。
            # 关键：不注入卡片之间（间距恒定 6px），也不拉伸其它卡片；
            # 放大的是文件列表这个有意义的容器，全屏时设置区自然铺满、与右栏等高。
            left_canvas.itemconfigure(self._left_win, width=cw, height=h)
            left_canvas.configure(scrollregion=(0, 0, cw, h))
            # 滚动条只在真的滚得动时出现，避免常态下一根突兀的细线
            if need > ch + 2:
                if not left_sb.winfo_ismapped():
                    left_sb.pack(side=tk.RIGHT, fill=tk.Y)
            elif left_sb.winfo_ismapped():
                left_sb.pack_forget()

        inner.bind("<Configure>", _sync_left)
        left_canvas.bind("<Configure>", _sync_left)

        # ══ 左栏：设置区 ══════════════════════════════════════
        # ① 印章来源（状态文字放标题栏右侧，保持单行、不占内容高度）
        c1, s, _ = self._titled_card(inner, "① 印章来源")
        c1.pack(fill=tk.X, pady=(0, 6))
        self.seal_hint = tk.Label(c1._head, text="印章实时同步",
                                  bg=t["card"], fg=t["subtle"], font=("微软雅黑", 8))
        self.seal_hint.pack(side=tk.RIGHT)
        top = tk.Frame(s, bg=t["card"])
        top.pack(fill=tk.X)
        thumb_box = tk.Frame(top, bg="#f7f9fc", width=64, height=64,
                             highlightbackground=t["border"], highlightthickness=1)
        thumb_box.pack(side=tk.LEFT, padx=(0, 10))
        thumb_box.pack_propagate(False)
        self.thumb = tk.Label(thumb_box, bg="#f7f9fc", text="暂无印章",
                              fg=t["subtle"], font=("微软雅黑", 8))
        self.thumb.place(relx=0.5, rely=0.5, anchor="center")
        side = tk.Frame(top, bg=t["card"])
        side.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.source_var = tk.StringVar(value="current")
        src_row = tk.Frame(side, bg=t["card"])
        src_row.pack(anchor="w", pady=(0, 4))
        BigRadiobutton(src_row, text="使用当前生成的印章", value="current",
                       variable=self.source_var, bg=t["card"], fg=t["text"],
                       command=self._on_source_change).pack(side=tk.LEFT, padx=(0, 10))
        BigRadiobutton(src_row, text="使用本地图片", value="file",
                       variable=self.source_var, bg=t["card"], fg=t["text"],
                       command=self._on_source_change).pack(side=tk.LEFT)
        path_row = tk.Frame(side, bg=t["card"])
        path_row.pack(fill=tk.X, pady=(0, 0))
        self.file_var = tk.StringVar()        # 实际图片路径（仅“本地文件”模式）
        self.src_text = tk.StringVar()        # 输入框显示文本（含占位说明）
        self.file_entry = ttk.Entry(path_row, textvariable=self.src_text, state="readonly",
                                    style="Seal.TEntry", font=("微软雅黑", 8))
        self.file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.pick_img_btn = self._btn(path_row, "选择", self._choose_image)
        self.pick_img_btn.pack(side=tk.LEFT)
        # 本地图片清晰度提示：仅「本地图片」且有文件时显示，平时不占空间，不影响布局
        self.file_warn = tk.Label(side, text="", bg=t["card"], fg=t["subtle"],
                                  font=("微软雅黑", 8), anchor="w", justify="left")
        self._file_path_cache = ""
        self._file_dims = (0, 0)

        # ② 待签章 PDF
        c2, f, _ = self._titled_card(inner, "② 待签章 PDF（支持多选批量）")
        c2.pack(fill=tk.BOTH, expand=True, pady=(0, 6))    # 唯一弹性卡：列表随窗口放大
        self.files_hint = tk.Label(c2._head, text="未选择文件",
                                  bg=t["card"], fg=t["subtle"], font=("微软雅黑", 8))
        self.files_hint.pack(side=tk.RIGHT)
        frow = tk.Frame(f, bg=t["card"])
        frow.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        self._btn(frow, "选择文件", self._choose_pdfs).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(frow, "移除选中", self._remove_selected).pack(side=tk.LEFT, padx=(0, 6))
        self._btn(frow, "清空", self._clear_files).pack(side=tk.LEFT)
        list_wrap = tk.Frame(f, bg=t["card"])
        list_wrap.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(list_wrap, height=1, font=("微软雅黑", 9),
                                  bg="#fbfcfe", fg=t["text"], selectmode=tk.EXTENDED,
                                  relief="flat", highlightthickness=1,
                                  highlightbackground=t["border"],
                                  selectbackground="#f6dede", selectforeground=t["text"],
                                  selectborderwidth=0, activestyle="none")
        sb = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.listbox.yview,
                           style="Seal.Vertical.TScrollbar")
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        install_list_copy(self.listbox, self.on_status)   # 列表行：选中即可复制
        self.list_empty = SelectableLabel(
            list_wrap, text="尚未选择 PDF 文件 · 点击下方「选择文件」添加",
            bg="#fbfcfe", fg=t["subtle"], font=("微软雅黑", 9), anchor="center")
        self.list_empty.place(relx=0.5, rely=0.5, anchor="center")

        # ③ 模式与范围（合并一张卡，两行紧凑排布）
        c3, mr, _ = self._titled_card(inner, "③ 签章模式与范围")
        c3.pack(fill=tk.X, pady=(0, 6))
        self.mode_hint = tk.Label(c3._head, text="定点：每页一枚完整章",
                                  bg=t["card"], fg=t["subtle"], font=("微软雅黑", 8))
        self.mode_hint.pack(side=tk.RIGHT)
        mrow = tk.Frame(mr, bg=t["card"])
        mrow.pack(fill=tk.X, pady=(0, 6))
        tk.Label(mrow, text="模式", bg=t["card"], fg=t["text"], width=5, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="fixed")
        BigRadiobutton(mrow, text="定点签章", value="fixed", variable=self.mode_var,
                       bg=t["card"], fg=t["text"],
                       command=self._on_mode_change).pack(side=tk.LEFT, padx=(0, 16))
        BigRadiobutton(mrow, text="骑缝签章", value="riding", variable=self.mode_var,
                       bg=t["card"], fg=t["text"],
                       command=self._on_mode_change).pack(side=tk.LEFT)
        rrow = tk.Frame(mr, bg=t["card"])
        rrow.pack(fill=tk.X, pady=(0, 2))
        tk.Label(rrow, text="范围", bg=t["card"], fg=t["text"], width=5, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.range_var = tk.StringVar(value="all")
        self.range_buttons = []
        for text, val in (("仅第 1 页", "first"), ("全部页面", "all"), ("指定页码", "custom")):
            rb = BigRadiobutton(rrow, text=text, value=val, variable=self.range_var,
                                bg=t["card"], fg=t["text"],
                                command=self._on_range_change)
            rb.pack(side=tk.LEFT, padx=(0, 9))
            self.range_buttons.append((rb, val))
        self.page_entry = ttk.Entry(rrow, width=9, style="Seal.TEntry", font=("微软雅黑", 9))
        self.page_entry.insert(0, self.PLACEHOLDER)
        self.page_entry.configure(foreground="#9aa3af")
        self.page_entry.pack(side=tk.LEFT, padx=(2, 0))
        self.page_entry.bind("<FocusIn>", self._page_focus_in)
        self.page_entry.bind("<FocusOut>", self._page_focus_out)
        self.page_entry.bind("<KeyPress>", self._page_filter)
        self.page_entry.bind("<KeyRelease>", lambda e: self._schedule_preview())

        # ④ 参数卡：按模式只显示一组，滑块占满左栏整宽
        c5, p, self.param_title = self._titled_card(inner, "④ 定点签章参数")
        c5.pack(fill=tk.X)
        self.param_hint = tk.Label(c5._head, text="", bg=t["card"], fg=t["subtle"],
                                   font=("微软雅黑", 8))
        self.param_hint.pack(side=tk.RIGHT)

        # 角度变量：定点 / 骑缝共用同一个固定角度值，切换面板时保持一致
        self.angle_mode_var = tk.StringVar(value="fixed")     # fixed | range
        self.angle_min_var = tk.IntVar(value=-8)
        self.angle_max_var = tk.IntVar(value=8)
        self.angle_seed_var = tk.IntVar(value=0)

        # ── 定点面板：4 行基础滑块 + 角度模式行 + 角度值行 ──
        self.fixed_panel, fholder = self._param_panel(p)
        self.fixed_widgets = []
        self._scale_row(fholder, "水平位置", "h_pos", 1, 10, "档", self.fixed_widgets, grid_row=0)
        self._scale_row(fholder, "垂直位置", "v_pos", 1, 10, "档", self.fixed_widgets, grid_row=1)
        self._scale_row(fholder, "印章大小", "stamp_size", 20, 100, "mm", self.fixed_widgets,
                        on_user=self._mark_size_touched, grid_row=2)
        self._scale_row(fholder, "不透明度", "opacity", 10, 100, "%", self.fixed_widgets, grid_row=3)

        ang_head = tk.Frame(fholder, bg=t["card"])
        ang_head.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        tk.Label(ang_head, text="印章角度", bg=t["card"], fg=t["text"], width=9, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.angle_mode_buttons = []
        for text, val in (("固定", "fixed"), ("区间随机", "range")):
            rb = BigRadiobutton(ang_head, text=text, value=val,
                                variable=self.angle_mode_var, bg=t["card"], fg=t["text"],
                                command=self._on_angle_mode_change)
            rb.pack(side=tk.LEFT, padx=(0, 12))
            self.angle_mode_buttons.append(rb)
        # 区间随机的上下限内联在「印章角度」同一行（每页独立由卡片标题右侧提示说明）
        self.angle_range_inline = tk.Frame(ang_head, bg=t["card"])
        ttk.Entry(self.angle_range_inline, textvariable=self.angle_min_var, width=6,
                  justify="center", style="Seal.TEntry", font=("微软雅黑", 9)).pack(
            side=tk.LEFT, padx=(0, 4))
        tk.Label(self.angle_range_inline, text="° ~", bg=t["card"], fg=t["subtle"],
                 font=("微软雅黑", 8)).pack(side=tk.LEFT)
        ttk.Entry(self.angle_range_inline, textvariable=self.angle_max_var, width=6,
                  justify="center", style="Seal.TEntry", font=("微软雅黑", 9)).pack(
            side=tk.LEFT, padx=(4, 4))
        tk.Label(self.angle_range_inline, text="°", bg=t["card"], fg=t["subtle"],
                 font=("微软雅黑", 8)).pack(side=tk.LEFT)
        self.angle_reshuffle = self._btn(ang_head, "换一组", self._reshuffle_angles, ghost=True)
        self._reshuffle_shown = False       # 仅区间随机模式出现，避免默认出现灰色禁用块

        # 固定角度行与区间行共用 row=5（互斥显示），避免留空行形成孤行
        self._scale_row(fholder, "旋转角度", "angle_fixed", -180, 180, "°",
                        self.fixed_widgets, grid_row=5)
        self.angle_fixed_row = fholder.grid_slaves(row=5, column=0)[0]

        # ── 骑缝面板：只提供固定角度（整章旋转后再切片） ──
        self.riding_panel, rholder = self._param_panel(p)
        self.riding_widgets = []
        srow = tk.Frame(rholder, bg=t["card"])
        srow.grid(row=0, column=0, sticky="ew", pady=2)
        tk.Label(srow, text="贴边方向", bg=t["card"], fg=t["text"], width=9, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.side_var = tk.StringVar(value="right")
        for text, val in (("左侧", "left"), ("右侧", "right")):
            rb = BigRadiobutton(srow, text=text, value=val, variable=self.side_var,
                                bg=t["card"], fg=t["text"])
            rb.pack(side=tk.LEFT, padx=(0, 14))
            self.riding_widgets.append(rb)
        self._scale_row(rholder, "垂直位置", "vertical_pos", 1, 10, "档", self.riding_widgets, grid_row=1)
        self._scale_row(rholder, "整章宽度", "riding_width", 20, 80, "mm", self.riding_widgets, grid_row=2)
        self._scale_row(rholder, "不透明度", "riding_opacity", 10, 100, "%", self.riding_widgets, grid_row=3)
        self._scale_row(rholder, "印章角度", "angle_fixed", -180, 180, "°",
                        self.riding_widgets, grid_row=4, var=self.vars["angle_fixed"])

        # ══ 右栏：所见即所得预览 ═══════════════════════════════
        c6, pv, _ = self._titled_card(right, "签章效果预览（实时）")
        c6.pack(fill=tk.BOTH, expand=True)
        tk.Label(c6._head, text="首张目标页 · 方向随 PDF · 不修改原文件",
                 bg=t["card"], fg=t["subtle"], font=("微软雅黑", 8)).pack(side=tk.RIGHT)
        wrap = tk.Frame(pv, bg="#e9edf2", highlightbackground=t["border"],
                        highlightthickness=1)
        wrap.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas = tk.Canvas(wrap, bg="#e9edf2", highlightthickness=0,
                                        width=200, height=200)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", self._paint_preview)
        # 底部只保留一行动态信息，说明文字已移到标题栏，避免两行堆叠
        self.preview_info = SelectableLabel(pv, text="", bg=t["card"],
                                            fg=t["subtle"], font=("微软雅黑", 8))
        self.preview_info.pack(fill=tk.X, pady=(8, 0))

        # ══ 全宽底部动作卡 ═══════════════════════════════════
        act = tk.Frame(body, bg=t["card"], highlightbackground=t["border"],
                       highlightthickness=1)
        act.grid(row=1, column=0, columnspan=2, sticky="ew")
        act.columnconfigure(0, weight=1)
        # 输出说明与「扁平化」开关合并为一行（引擎状态由主窗口状态栏呈现）
        # pack 顺序：按钮最先固定右侧 → 窄窗口时优先收缩中间说明文字
        r1 = tk.Frame(act, bg=t["card"])
        r1.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 6))
        btns = tk.Frame(r1, bg=t["card"])
        btns.pack(side=tk.RIGHT)
        self.back_btn = self._btn(btns, "返回设计", self.on_go_design, ghost=True)
        self.back_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.start_btn = self._btn(btns, "开始签章", self._start, primary=True)
        self.start_btn.pack(side=tk.LEFT)
        self.flatten_var = tk.BooleanVar(value=False)
        BigCheckbutton(r1, text="扁平化原文档批注/表单域", variable=self.flatten_var,
                       bg=t["card"], fg=t["text"]).pack(side=tk.LEFT)
        SelectableLabel(r1, text="（非防抠图/数字签名）", bg=t["card"],
                        fg=t["subtle"], font=("微软雅黑", 8)).pack(
            side=tk.LEFT, padx=(2, 12))
        SelectableLabel(r1, text="输出 <原名>_已签章.pdf · 原目录另存不覆盖原件",
                        bg=t["card"], fg=t["text"], font=("微软雅黑", 9)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self.progress = ttk.Progressbar(act, mode="determinate", maximum=100,
                                        style="Seal.Horizontal.TProgressbar")

        self._update_panel_state()
        self._update_range_state()
        self._update_source_state()
        self._update_angle_state()
        for var in (self.mode_var, self.side_var, self.range_var, self.source_var,
                    self.angle_mode_var, self.angle_min_var, self.angle_max_var,
                    self.angle_seed_var):
            var.trace_add("write", self._schedule_preview)
        for key in self.vars:
            self.vars[key].trace_add("write", self._schedule_preview)

        # 滚轮转发：Tk 不会把子控件的滚轮事件冒泡给 Canvas，需递归绑定
        def _bind_wheel(w):
            if isinstance(w, tk.Listbox):
                return
            w.bind("<MouseWheel>", lambda e: left_canvas.yview_scroll(
                -1 if e.delta > 0 else 1, "units"))
            for child in w.winfo_children():
                _bind_wheel(child)

        _bind_wheel(inner)
        self.after(120, self._refresh_preview)
        if not PDF_ENGINE_OK:
            self._disable_engine_actions()

    def _sync_from_config(self):
        c = self.cfg
        # 程序写回变量同样会触发 ttk.Scale 的 command，必须置自动同步标志，
        # 否则启动时就会误判为“用户手动改过尺寸”，导致后续印章尺寸无法自动带入
        self._auto_syncing = True
        try:
            self.mode_var.set(c.get("mode", "fixed"))
            self.range_var.set(c.get("page_range", "all"))
            self.side_var.set(c.get("riding_side", "right"))
            self.flatten_var.set(bool(c.get("flatten", False)))
            self.source_var.set(c.get("stamp_source", "current"))
            self.file_var.set(c.get("stamp_file", ""))
            self.angle_mode_var.set(c.get("angle_mode", "fixed"))
            self.angle_min_var.set(c.get("angle_min", -8))
            self.angle_max_var.set(c.get("angle_max", 8))
            self.angle_seed_var.set(c.get("angle_seed", 0))
            for k in STAMP_DEFAULTS:
                if k in self.vars and k in c:
                    self.vars[k].set(c[k])
        finally:
            self._auto_syncing = False
        self._update_panel_state()
        self._update_range_state()
        self._update_source_state()
        self._refresh_seal_thumb()

    # ── 联动 ──
    @staticmethod
    def _set_state(widget, enabled):
        try:
            if isinstance(widget, ttk.Scale) or isinstance(
                    widget, (BigRadiobutton, BigCheckbutton)):
                widget.state(["!disabled"] if enabled else ["disabled"])
            else:
                widget.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _update_panel_state(self):
        """只显示当前模式的一组参数，另一组隐藏（比并排置灰更省空间、更清晰）。"""
        fixed = self.mode_var.get() == "fixed"
        show, hide = ((self.fixed_panel, self.riding_panel) if fixed
                      else (self.riding_panel, self.fixed_panel))
        hide.pack_forget()
        show.pack(fill=tk.BOTH, expand=True)
        self.param_title.configure(text="④ 定点签章参数" if fixed else "④ 骑缝签章参数")
        self.mode_hint.configure(
            text=("定点 · 每页一枚完整章" if fixed else "骑缝 · 整章跨页拼接"))
        self.param_hint.configure(
            text=("1 档最左/最上 · 角度固定或每页随机" if fixed
                  else "整章旋转后切片 · 仅固定角度"))
        self._update_angle_state()
        self._schedule_preview()

    def _update_angle_state(self):
        """角度模式联动：骑缝章强制固定角度，禁用区间随机；定点章按选择切换控件行。"""
        riding = self.mode_var.get() == "riding"
        if riding and self.angle_mode_var.get() != "fixed":
            self.angle_mode_var.set("fixed")
        is_range = (self.angle_mode_var.get() == "range") and not riding
        for rb in self.angle_mode_buttons:
            self._set_state(rb, not riding)
        if is_range:
            self.angle_fixed_row.grid_remove()
            self.angle_range_inline.pack(side=tk.LEFT, padx=(4, 0))
        else:
            self.angle_range_inline.pack_forget()
            self.angle_fixed_row.grid(row=5, column=0, sticky="ew", pady=2)
        if is_range and not self._reshuffle_shown:
            self.angle_reshuffle.pack(side=tk.RIGHT)
            self._reshuffle_shown = True
        elif not is_range and self._reshuffle_shown:
            self.angle_reshuffle.pack_forget()
            self._reshuffle_shown = False

    def _on_angle_mode_change(self):
        self._update_angle_state()
        self._schedule_preview()

    def _reshuffle_angles(self):
        """换一组随机角度：递增种子，重新生成各页角度（预览与成品同步）。"""
        if self.mode_var.get() == "riding" or self.angle_mode_var.get() != "range":
            return
        self.angle_seed_var.set(self.angle_seed_var.get() + 1)
        self._schedule_preview()

    def _update_range_state(self):
        riding = self.mode_var.get() == "riding"
        if riding:
            self.range_var.set("all")
        for rb, val in self.range_buttons:
            # 骑缝章固定「全部页面」：只禁用另两项，保持当前选中态清晰可读
            self._set_state(rb, not riding or val == "all")
        custom = (self.range_var.get() == "custom") and not riding
        self.page_entry.configure(state="normal" if custom else "disabled",
                                  foreground=self.theme["text"] if custom else "#9aa3af")

    def _update_source_state(self):
        use_file = self.source_var.get() == "file"
        if use_file:
            self.src_text.set(self.file_var.get() or "尚未选择图片")
            self.file_entry.configure(state="readonly", foreground=self.theme["text"])
        else:
            self.src_text.set("当前印章 · 实时同步")
            self.file_entry.configure(state="readonly", foreground=self.theme["subtle"])
        if hasattr(self.pick_img_btn, "set_enabled"):
            self.pick_img_btn.set_enabled(use_file)

    def _on_mode_change(self):
        self._update_panel_state()
        self._update_range_state()
        self._schedule_preview()

    def _on_range_change(self):
        self._update_range_state()
        self._schedule_preview()

    def _on_source_change(self):
        self._update_source_state()
        self._stamp_cache["key"] = None
        self._update_file_resolution()
        self._schedule_preview()

    def _mark_size_touched(self):
        # 仅统计真人操作；程序自动写值（印章尺寸同步 / 配置回读）不算
        if not self._auto_syncing:
            self._size_touched = True

    def _page_focus_in(self, _e=None):
        if self.page_entry.get() == self.PLACEHOLDER:
            self.page_entry.delete(0, tk.END)
            self.page_entry.configure(foreground=self.theme["text"])
            self._page_edited = True        # 已开始编辑，占位符不再参与判定

    def _page_focus_out(self, _e=None):
        if not self.page_entry.get().strip():
            self.page_entry.insert(0, self.PLACEHOLDER)
            self.page_entry.configure(foreground="#9aa3af")

    def _page_filter(self, event):
        if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Home", "End"):
            return
        if event.char and event.char not in "0123456789,-":
            return "break"
        if event.char:
            self._page_edited = True

    # ── 印章来源 ──
    def _refresh_seal_thumb(self):
        t = self.theme
        img = self.seal_img
        if img is None:
            self.thumb.configure(image="", text="暂无印章")
            self.seal_hint.configure(text="待生成印章")
            return
        try:
            disp = img.convert("RGBA").copy()
            disp.thumbnail((58, 58), _LANCZOS)
            self._thumb_ref = ImageTk.PhotoImage(disp)
            self.thumb.configure(image=self._thumb_ref, text="")
            size = f"{self.seal_size_mm:.1f} mm" if self.seal_size_mm else "—"
            self.seal_hint.configure(text=f"{img.width}×{img.height}px · {size} · 已同步")
        except Exception:
            traceback.print_exc()

    def set_seal_image(self, img, size_mm=None):
        """由主程序在每次印章渲染完成后调用：把内存印章实时送入签章页。"""
        self.seal_img = img
        self.seal_size_mm = size_mm
        if size_mm and not self._size_touched:
            v = _clamp(int(round(float(size_mm))), 20, 100)
            self._auto_syncing = True
            try:
                self.vars["stamp_size"].set(v)
                self.vars["riding_width"].set(_clamp(v, 20, 80))
            finally:
                self._auto_syncing = False
        self._refresh_seal_thumb()
        self._stamp_cache["key"] = None
        self._schedule_preview()

    def on_activate(self):
        """切换到签章页时，重新拉取设计页最新印章并刷新预览。"""
        try:
            img, size_mm = self.seal_provider()
            self.set_seal_image(img, size_mm)
        except Exception:
            traceback.print_exc()
        self._page_cache["key"] = None
        self._schedule_preview()

    def _disable_engine_actions(self):
        if hasattr(self.start_btn, "set_enabled"):
            self.start_btn.set_enabled(False)
        self.files_hint.configure(
            text="未检测到 PyMuPDF：请执行 pip install pymupdf 后重启",
            fg=self.theme["accent"])

    def _choose_image(self):
        p = filedialog.askopenfilename(title="选择印章图片",
                                       filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
                                                  ("所有文件", "*.*")])
        if not p:
            return
        self.file_var.set(p)
        self.source_var.set("file")
        self._update_source_state()
        self._stamp_cache["key"] = None
        self._update_file_resolution()
        self._schedule_preview()

    def _update_file_resolution(self):
        """本地图片清晰度提示：按当前落章尺寸换算有效 DPI，低于 300 DPI 标红。

        仅在「本地图片」来源显示，平时 pack_forget 不占布局空间；尺寸变化
        （stamp_size / riding_width 滑块）也会经 _refresh_preview 触发刷新。
        """
        t = self.theme
        if self.source_var.get() != "file":
            if self.file_warn.winfo_ismapped():
                self.file_warn.pack_forget()
            return
        p = (self.file_var.get() or "").strip()
        if not p or not os.path.isfile(p):
            self.file_warn.configure(
                text="提示：建议选 ≥300 DPI 的透明底 PNG / JPG，避免签章发糊",
                fg=t["subtle"])
            if not self.file_warn.winfo_ismapped():
                self.file_warn.pack(fill=tk.X, pady=(1, 2))
            return
        # 仅当路径变化时重读图片尺寸，避免拖滑块时反复打开文件
        if p != self._file_path_cache:
            try:
                with Image.open(p) as im:
                    self._file_dims = im.size
                self._file_path_cache = p
            except Exception:
                self._file_dims = (0, 0)
                self._file_path_cache = p
        w, h = self._file_dims
        if not w or not h:
            if self.file_warn.winfo_ismapped():
                self.file_warn.pack_forget()
            return
        mode = self.mode_var.get()
        size_mm = float(self.vars["riding_width"].get() if mode == "riding"
                        else self.vars["stamp_size"].get())
        long_px = max(w, h)
        dpi = long_px * 25.4 / size_mm if size_mm > 0 else 0
        if dpi < 300:
            self.file_warn.configure(
                text=f"清晰度偏低 · 图片 {w}×{h}px · 按当前 {size_mm:.0f}mm 有效约 {dpi:.0f} DPI，签章可能发糊",
                fg=t["accent"])
        else:
            self.file_warn.configure(
                text=f"图片 {w}×{h}px · 按当前 {size_mm:.0f}mm 有效约 {dpi:.0f} DPI（足够清晰）",
                fg=t["subtle"])
        if not self.file_warn.winfo_ismapped():
            self.file_warn.pack(fill=tk.X, pady=(1, 2))

    def get_stamp_source(self, quiet=False):
        """取印章来源；quiet=True 供实时预览调用（不弹窗打断用户）。"""
        def _warn(msg):
            if not quiet:
                messagebox.showwarning("提示", msg)

        if self.source_var.get() == "file":
            p = self.file_var.get().strip()
            if not p or not os.path.isfile(p):
                _warn("请先选择一张有效的印章图片")
                return None
            return p
        try:
            img, _size = self.seal_provider()
        except Exception:
            img = None
        if img is None:
            _warn("设计页还没有可用印章，请先生成或改选本地图片")
            return None
        return img

    # ── PDF 文件列表 ──
    def _choose_pdfs(self):
        paths = filedialog.askopenfilenames(title="选择待签章 PDF（可多选）",
                                            filetypes=[("PDF 文档", "*.pdf"), ("所有文件", "*.*")])
        for p in paths:
            if p not in self.selected_files:
                self.selected_files.append(p)
        self._refresh_files()

    def _remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            if 0 <= i < len(self.selected_files):
                self.selected_files.pop(i)
        self._refresh_files()

    def _clear_files(self):
        self.selected_files.clear()
        self._refresh_files()

    def _refresh_files(self):
        self.listbox.delete(0, tk.END)
        self.file_info = [probe_pdf(p) for p in self.selected_files]
        if self.selected_files:
            self.list_empty.place_forget()
        else:
            self.list_empty.place(relx=0.5, rely=0.5, anchor="center")
        for p, info in zip(self.selected_files, self.file_info):
            flag = " [加密]" if info["encrypted"] else (" [无法读取]" if info["error"] else "")
            self.listbox.insert(tk.END, f"  {os.path.basename(p)}  ·  {info['pages']} 页"
                                         f"  ·  PDF {info['version']}{flag}")
        if not self.selected_files:
            text = "未选择文件"
        else:
            pages = sum(i["pages"] for i in self.file_info)
            versions = sorted({i["version"] for i in self.file_info})
            text = (f"{len(self.selected_files)} 个文件 · {pages} 页 · "
                    f"PDF {' / '.join(versions)}")
        if PDF_ENGINE_OK:
            self.files_hint.configure(text=text, fg=self.theme["subtle"])
        self.on_status("就绪", text)
        self._page_cache["key"] = None
        self._schedule_preview()

    # ── 配置收集 ──
    def _range_text(self):
        if self.range_var.get() != "custom":
            return ""
        v = self.page_entry.get().strip()
        # 仅「未编辑且仍是占位符」才视为空；用户真实输入 1,3-5 必须生效
        if not getattr(self, "_page_edited", False) and v == self.PLACEHOLDER:
            return ""
        return re.sub(r"^[,\-]+|[,\-]+$", "", re.sub(r"[^0-9,\-]", "", v))

    def collect_config(self):
        cfg = {"mode": self.mode_var.get(),
               "page_range": self.range_var.get(),
               "riding_side": self.side_var.get(),
               "flatten": bool(self.flatten_var.get()),
               "stamp_source": self.source_var.get(),
               "stamp_file": self.file_var.get(),
               "angle_mode": self.angle_mode_var.get(),
               "angle_seed": int(self.angle_seed_var.get())}
        for k, var in self.vars.items():
            try:
                cfg[k] = int(var.get())
            except Exception:
                cfg[k] = STAMP_DEFAULTS[k]
        for k, var in (("angle_min", self.angle_min_var), ("angle_max", self.angle_max_var)):
            try:
                cfg[k] = int(var.get())
            except Exception:
                cfg[k] = STAMP_DEFAULTS[k]
        lo_hi = {"h_pos": (1, 10), "v_pos": (1, 10), "stamp_size": (20, 100),
                 "opacity": (10, 100), "vertical_pos": (1, 10),
                 "riding_width": (20, 80), "riding_opacity": (10, 100),
                 "angle_fixed": (-180, 180), "angle_min": (-180, 180),
                 "angle_max": (-180, 180)}
        for k, (lo, hi) in lo_hi.items():
            cfg[k] = _clamp(cfg[k], lo, hi)
        if cfg["angle_min"] > cfg["angle_max"]:
            cfg["angle_min"], cfg["angle_max"] = cfg["angle_max"], cfg["angle_min"]
        # 骑缝章只支持自定义固定角度：算法层仍会兜底，这里先在配置层收紧
        if cfg["mode"] == "riding":
            cfg["angle_mode"] = "fixed"
        return cfg

    # 预览已内嵌到右栏（_refresh_preview / _paint_preview），不再有独立弹窗或按钮

    # ── 执行 ──
    def _set_running_ui(self, running):
        self._running = running
        if hasattr(self.start_btn, "set_text"):
            self.start_btn.set_text("停止签章" if running else "开始签章")
        for b in (self.back_btn,):
            if hasattr(b, "set_enabled"):
                b.set_enabled(not running)
        if running:
            self.progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
            self.progress["value"] = 0
        else:
            self.progress.grid_remove()

    def _start(self):
        if not PDF_ENGINE_OK:
            messagebox.showerror("缺少组件", "未检测到 PyMuPDF。\n请执行 pip install pymupdf 后重启软件。")
            return
        if self._running:
            self._cancel = True
            self.on_status("正在停止…", "等待当前文件处理完成")
            return
        if not self.selected_files:
            messagebox.showwarning("提示", "请先选择至少一个 PDF 文件")
            return
        source = self.get_stamp_source()
        if source is None:
            return
        cfg = self.collect_config()
        if cfg["mode"] == "riding":
            bad = [self.selected_files[i] for i, info in enumerate(self.file_info)
                   if 0 < info["pages"] < 2]
            if bad:
                messagebox.showwarning("提示", "骑缝签章至少需要 2 页，以下文件将被跳过：\n" +
                                       "\n".join(os.path.basename(p) for p in bad[:5]))
        if cfg["page_range"] == "custom" and not self._range_text():
            messagebox.showwarning("提示", "请填写有效的指定页码，例如 1,3-5")
            return
        save_config(cfg)
        files = list(self.selected_files)
        range_text = self._range_text()
        self._cancel = False
        self._last_result = None
        self._set_running_ui(True)
        self.on_status("开始签章…", f"共 {len(files)} 个文件")

        def emit(kind, payload=None):
            self._queue.put((kind, payload))

        def ask_overwrite(msg):
            box = queue.Queue()
            self._queue.put(("ask", (msg, box)))
            return box.get()

        def worker():
            try:
                stamp_pdfs(files, source, cfg, range_text, emit=emit,
                           ask_overwrite=ask_overwrite,
                           is_cancelled=lambda: self._cancel)
            except Exception as e:
                self._queue.put(("fatal", f"{e}\n{traceback.format_exc(limit=3)}"))
            finally:
                self._queue.put(("finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    idx, total, fname = payload
                    self.progress["value"] = int(idx * 100 / max(1, total))
                    self.on_status(f"正在签章 {idx + 1}/{total}", fname)
                elif kind == "ask":
                    msg, box = payload
                    box.put(messagebox.askyesnocancel("输出文件已存在", msg))
                elif kind == "done":
                    self._last_result = payload
                elif kind == "fatal":
                    messagebox.showerror("签章失败", str(payload))
                elif kind == "finished":
                    self._set_running_ui(False)
                    r = self._last_result
                    if r is None:                     # 启动阶段异常，错误框已弹出
                        self.on_status("签章失败", "详见错误提示")
                        continue
                    tip = (f"批量签章{'已终止' if r.get('cancelled') else '完成'}\n\n"
                           f"总数：{r['total']}    成功：{r['success']}    失败：{len(r['fail'])}")
                    if r["fail"]:
                        tip += "\n\n失败文件：\n" + "\n".join(r["fail"][:8])
                    if r["success"]:
                        tip += f"\n\n已另存到原文件目录，例如：\n{os.path.basename(r['outputs'][0])}"
                    messagebox.showinfo("签章结果", tip)
                    self.on_status("签章完成" if r["success"] else "就绪",
                                   f"成功 {r['success']} / 共 {r['total']}")
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
        self.after(120, self._poll_queue)
