# -*- coding: utf-8 -*-
"""
标准电子印章生成工具（圆形章 / 椭圆形章，边线防伪纹精确版）
版本：v1.3.0
更新内容（基于 v1.2.0 椭圆章专项深度测评重写弧排引擎）：
  - 修复：**"公司名弧度"滑块近乎失效** —— 旧弧排步距被"字宽×1.04"钳住、与弧长解耦，
          默认参数下 180°/230°/260° 渲染出的字心距完全相同（像素级一致）。
          重写为"按请求夹角等弧长铺满"，夹角调节真实生效（自检 180°≠220°≠260°）。
  - 修复：椭圆弧排相邻字符**粘连/压边**。旧实现用"字心距 ≥ 1.06×字宽"近似，斜排末端
          必然不够；且预置的 company_width_scale=1.0 下旧算法会明显重叠。新实现用
          **旋转矩形 SAT 实测净空**，字号→字宽逐级自适应，实测 0 重叠。
  - 修复：窄椭圆（45×20mm）首尾字符侧转 146°、几乎倒立；旧模型 r_eff=b²/a 过小还会
          把字号算崩。新实现改用**侧转阻尼限幅**（|rot| ≤ 96°），位置仍按弧长铺满。
  - 修复：v1.2.0 的"长名自动扩弧"是负优化——长名同时"夹角更大 + 字号更小"，
          末字侧转更严重。已删除，改由"夹角钳制 + 字号自适应"统一求解。
  - 修复：短名 + 大弧时字符被拉散。新增**字距上限**（字心距 ≤ 3.20×字宽），超出自动
          收紧内部弧跨度。实测该上限取 2.10 时过紧，会把 6~7 字名称的滑块行程压成 0°
          （表现为"滑块又没反应了"），取 3.20 后 6 字名行程恢复 30.9°、8 字以上 39.9°，
          且 12 组用例与 6 个预置的重叠/净空指标无任何回退。
  - 修复：字形越出边框。新增 **safe 椭圆包裹约束**（四角必须落在边框内沿内缩 0.5mm
          的区域内），超界则二分回收夹角。
  - 修复：`发票专用章` 预置 company_arc=200°，**低于规范下限 210°**（且与文件内注释
          自述的 220° 相矛盾）；已按注释意图修正为 220°。
  - 优化：圆弧控件按章型分型量程——椭圆章 150~260°、圆形章 180~360°。
          原先两型共用 180~360°，椭圆章实际只用得到 180~210°，滑块大半行程"没反应"。
  - 优化：中心印文按《国家税务总局公告 2011 年第 7 号》式样定位——
          统一社会信用代码居中、"发票专用章"下刊（中心线下 4.2mm）、顺序编码最下
          （中心线下 10mm）；扁椭圆放不下时自动退回"整块居中"以免越界。
  - 优化：椭圆预置参数对齐规范（环排夹角 220°、字距线 0.6mm、税号字高 3.7mm、
          发票专用章字高 4.6mm、仿宋体）。
  - 实测（12 组用例 / dpi=20）：0 重叠、0 越界、0 出框，最大侧转 96°、字距 CV ≤ 0.009；
          6 个椭圆预置全部合格（净空 ≥ 0.47mm）。
历史版本 v1.2.0（基于 v1.1.8 深度测评修复）：
  - 修复：公司名超过约 12 字时弧形文字严重重叠（按内沿净空约束自适应缩小字号，
          长名称优先等比缩字、必要时才轻微压窄，字形不变形）
  - 修复：极限参数下五角星 / 备案号 / 中心文字互相重叠、文字越出边框
          （半径与字号做双向几何求解，冲突时自动缩小字号兜底）
  - 修复：椭圆章"章号"使用英文字体渲染全角括号（２）导致显示为方框乱码
          （按文本内容自动选择中 / 西文字体）
  - 修复：窗口默认 2200px 宽超出主流 1080p 屏幕，改为按屏幕自适应
  - 修复：数字输入框打字过程中被反复回写、光标跳动（改为失焦 / 回车时提交）
  - 修复：关闭"印章仿真"后噪点 / 模糊 / 断纹滑块无效但仍可拖动（现自动禁用）
  - 修复：numpy 全局随机种子污染进程状态（改用独立 Generator，渲染结果仍稳定）
  - 修复：参数字典缺项时仿真模块 KeyError 崩溃（全部改为带缺省值读取）
  - 优化：导出 PNG 写入 508 DPI 物理分辨率信息，Word/WPS 中插入即为真实毫米尺寸
  - 优化：Windows 字体目录改由 WINDIR 环境变量解析（兼容系统盘非 C: 的机器）
  - 优化：边框断纹圆形 / 椭圆两套重复逻辑合并为统一分段规划器
  - 优化：启动时窗口图标不再做全套仿真渲染，启动提速约 200ms
声明：仅供学习参考，请勿用于非法用途，否则后果自负！
"""

import bisect
import hashlib
import math
import os
import random
import re
import time
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk
    _DEPS_OK = True
except ImportError:
    np = None
    Image = ImageDraw = ImageFilter = ImageFont = ImageTk = None
    _DEPS_OK = False

# 赞助按钮（可选模块；缺失时不影响主功能）
try:
    from sponsor_widget import show_sponsor_button as _show_sponsor_btn
    _SPONSOR_OK = True
except Exception:
    _SPONSOR_OK = False

# ─────────────────────────── 配置层 ───────────────────────────
EXPIRE_DATE = datetime(2028, 1, 1)
APP_VERSION = "v1.3.0"

if _DEPS_OK:
    _RES = getattr(Image, "Resampling", None)
    LANCZOS = _RES.LANCZOS if _RES else getattr(Image, "LANCZOS", 1)
    BICUBIC = _RES.BICUBIC if _RES else getattr(Image, "BICUBIC", 2)
else:
    LANCZOS = BICUBIC = None

CIRCLE_PRESETS = {
    "国有企业/股份公司": {"circle_diameter": 42.0, "border_width": 1.2, "star_radius": 7.0,
                        "font_size_company": 7.0, "font_size_record": 2.0, "font_size_center": 4.0,
                        "font_size_no": 2.2, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
    "有限责任公司":   {"circle_diameter": 40.0, "border_width": 1.0, "star_radius": 6.5,
                        "font_size_company": 7.0, "font_size_record": 1.65, "font_size_center": 3.8,
                        "font_size_no": 2.0, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
    "个体/私营企业":  {"circle_diameter": 38.0, "border_width": 1.0, "star_radius": 6.0,
                        "font_size_company": 7.0, "font_size_record": 1.55, "font_size_center": 3.6,
                        "font_size_no": 2.0, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
    "全国性社会团体":  {"circle_diameter": 45.0, "border_width": 1.2, "star_radius": 7.5,
                        "font_size_company": 7.0, "font_size_record": 1.75, "font_size_center": 4.2,
                        "font_size_no": 2.5, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
    "地方性社团":     {"circle_diameter": 42.0, "border_width": 1.0, "star_radius": 7.0,
                        "font_size_company": 7.0, "font_size_record": 1.65, "font_size_center": 4.0,
                        "font_size_no": 2.4, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
    "行政/司法机关":  {"circle_diameter": 42.0, "border_width": 1.0, "star_radius": 7.0,
                        "font_size_company": 7.0, "font_size_record": 1.65, "font_size_center": 4.0,
                        "font_size_no": 2.4, "company_arc": 250, "record_arc": 80, "arc_gap": 1.2},
}

ELLIPSE_PRESETS = {
    # 依据《国家税务总局关于发票专用章式样有关问题的公告》（2011年第7号）及
    # 各地税务机关补充说明：
    #   长轴 40mm、短轴 30mm、边宽 1mm；环排夹角 210~260°；
    #   字与边线内侧距离 0.5mm；"发票专用章"字高 4.6mm 字宽 3mm（仿宋体）；
    #   纳税人名称字高 4.2mm；税号 Arial 字高 3.7mm；顺序编号字高 2.2mm。
    # company_arc 取 220°，落在规范 210~260° 区间内，兼顾"环排充分"与"首尾不倒立"。
    # 字宽缩放 0.72 ≈ 仿宋"窄长"字形（规范："发票专用章"字高 4.6mm/字宽 3mm ≈ 0.65），
    # 名称字宽"根据名称字数确定"，取 0.72 收敛横向占位。
    # 注：长名称（≥10 字）在 210° 以上环排时，受"字与边线内侧 0.5mm"净空约束，
    #     实测字高会自适应到 3.75~3.95mm（名义值 4.2mm）——这是几何硬约束下的最优解，
    #     若强行保 4.2mm 字高则字角会压到边框上，反而更不可用。
    "发票专用章": {"axis_a": 40.0, "axis_b": 30.0, "border_width": 1.0,
                 "font_family": "simfang.ttf",
                 "font_size_company": 4.2, "font_size_record": 3.7, "font_size_center": 4.6,
                 "font_size_no": 2.2, "company_arc": 210, "arc_gap": 0.6,
                 "company_width_scale": 0.72, "record_width_scale": 0.85, "center_width_scale": 0.85},
    "业务专用章": {"axis_a": 40.0, "axis_b": 30.0, "border_width": 1.0,
                 "font_size_company": 4.6, "font_size_record": 3.0, "font_size_center": 4.6,
                 "font_size_no": 2.4, "company_arc": 210, "arc_gap": 0.6,
                 "company_width_scale": 0.80, "record_width_scale": 0.92, "center_width_scale": 0.95},
    "合同专用章": {"axis_a": 42.0, "axis_b": 30.0, "border_width": 1.0,
                 "font_size_company": 4.8, "font_size_record": 3.0, "font_size_center": 4.6,
                 "font_size_no": 2.4, "company_arc": 210, "arc_gap": 0.6,
                 "company_width_scale": 0.80, "record_width_scale": 0.92, "center_width_scale": 0.95},
    "财务专用章": {"axis_a": 40.0, "axis_b": 30.0, "border_width": 1.0,
                 "font_size_company": 4.6, "font_size_record": 3.0, "font_size_center": 4.6,
                 "font_size_no": 2.4, "company_arc": 210, "arc_gap": 0.6,
                 "company_width_scale": 0.80, "record_width_scale": 0.92, "center_width_scale": 0.95},
    "报关专用章": {"axis_a": 45.0, "axis_b": 32.0, "border_width": 1.0,
                 "font_size_company": 5.0, "font_size_record": 3.0, "font_size_center": 4.8,
                 "font_size_no": 2.4, "company_arc": 210, "arc_gap": 0.6,
                 "company_width_scale": 0.80, "record_width_scale": 0.92, "center_width_scale": 0.95},
    "检验专用章": {"axis_a": 35.0, "axis_b": 26.0, "border_width": 0.8,
                 "font_size_company": 4.2, "font_size_record": 2.6, "font_size_center": 4.0,
                 "font_size_no": 2.2, "company_arc": 200, "arc_gap": 0.5,
                 "company_width_scale": 0.80, "record_width_scale": 0.92, "center_width_scale": 0.95},
}

# ── 椭圆弧排几何约束（v1.3.0 重写）──
# 名称环排夹角（company_arc）控制弧的角跨度；首尾侧转做阻尼限幅：
# 规范允许 210~260°，但"完全切线排列 + 大夹角"会让末字转到 140°+ 近似倒立，
# 因此对首尾字的旋转角做限幅（位置仍按弧长铺满），兼顾规范与可读性。
ARC_SPAN_MIN       = 150.0  # 环排夹角下限（再小名称会挤在顶部）
ARC_SPAN_MAX       = 210.0  # 环排夹角上限（税务规范上限 210°；再大文字会越过椭圆端点进入下半弧导致字倒立）
ARC_ROT_LIMIT_DEG  = 85.0   # 首尾字符最大侧转角（阻尼限幅；>90° 字会过头倒立）
ARC_STEP_MAX_RATIO = 3.20   # 字心距 / 字宽 的上限：极短名 + 大弧时收紧内部跨度
ARC_CLEAR_MARGIN   = 0.6    # 相邻字符 SAT 实测最小净空（px）
ARC_EFF_FLOOR      = 0.52   # 字宽缩放下限（字号到底仍重叠时兜底）
ARC_BORDER_CLEAR_MM = 0.5   # 字与边线内侧的最小距离（mm，规范值）

DEFAULT_PARAMS = {
    "company_name": "云南某某科技有限公司",
    "record_no": "5301234567890",
    "seal_type": "合同专用章",
    "seal_no": "2",
    "seal_shape": "circle",
    "font_family": "simsun.ttc",
    "seal_color": "red",
    "enable_simulation": True,
    "circle_diameter": 42.0, "star_radius": 7.0, "record_arc": 80.0,
    "axis_a": 40.0, "axis_b": 30.0, "border_width": 1.2,
    "font_size_company": 7.0, "font_size_record": 2.0,
    "font_size_center": 4.0, "font_size_no": 2.2,
    "arc_gap": 1.2, "company_arc": 250.0,
    "company_width_scale": 0.72, "record_width_scale": 1.0, "center_width_scale": 1.0,
    "sim_noise": 1.0, "sim_blur": 0.4, "sim_break": 0.0, "sim_security": 10.0,
}

# 仿真参数缺省值：参数字典缺项时兜底，避免 KeyError
SIM_DEFAULTS = {"sim_noise": 1.0, "sim_blur": 0.4, "sim_break": 0.0, "sim_security": 0.0}
FONT_FLOOR = 12          # 仿真渲染下最小可用字号（px）
EXPORT_DPI = 508.0       # 20 px/mm × 25.4 mm/inch，写入 PNG 物理分辨率

UI_ITEMS = [
    # (group, kind, label, key, min, max, step, unit)
    ("base", "entry", "公司名称", "company_name"),
    ("base", "entry", "备案号",   "record_no"),
    ("base", "entry", "章型",     "seal_type"),
    ("base", "entry", "章号",     "seal_no"),
    ("sim",  "slider", "噪点强度", "sim_noise", 0.1, 5.0, 0.1, ""),
    ("sim",  "slider", "模糊强度", "sim_blur",  0.1, 5.0, 0.1, ""),
    ("sim",  "slider", "外圈断纹", "sim_break", 0.0, 5.0, 0.1, ""),
    ("sim",  "slider", "防伪纹",   "sim_security", 0.0, 99.0, 1.0, ""),
    ("circle", "slider", "圆章直径",   "circle_diameter", 30.0, 50.0, 0.5, "mm"),
    ("circle", "slider", "五角星半径", "star_radius",      3.0, 10.0, 0.1, "mm"),
    ("circle", "slider", "备案号弧度", "record_arc",      60,   180,  1,    ""),
    ("ellipse", "slider", "长轴直径",  "axis_a",          30.0, 50.0, 0.5, "mm"),
    ("ellipse", "slider", "短轴直径",  "axis_b",          20.0, 40.0, 0.5, "mm"),
    ("common", "slider", "公司名字号", "font_size_company", 5.0, 10.0, 0.1, "mm"),
    ("common", "slider", "备案号字号", "font_size_record",  1.5, 4.0,  0.1, "mm"),
    ("common", "slider", "章型字号",   "font_size_center",  2.0, 6.0,  0.1, "mm"),
    ("common", "slider", "章号字号",   "font_size_no",      1.5, 4.0,  0.1, "mm"),
    ("common", "slider", "弧形间距",   "arc_gap",           0.2, 4.0,  0.1, "mm"),
    ("common", "slider", "边框宽度",   "border_width",      0.5, 2.0,  0.05, "mm"),
    ("common", "slider", "公司名弧度", "company_arc",      180, 360,  1,   ""),
]

COLORS = {"红色": "red", "蓝色": "blue", "黑色": "black",
          "绿色": "green", "紫色": "purple", "橙色": "orange"}
COLOR_RGB = {"red": (178, 22, 22), "blue": (0, 60, 200), "black": (20, 20, 20),
             "green": (0, 120, 40), "purple": (110, 0, 110), "orange": (200, 80, 0)}
FONT_MAP = {"宋体": "simsun.ttc", "仿宋": "simfang.ttf", "黑体": "simhei.ttf",
            "微软雅黑": "msyh.ttc", "楷体": "simkai.ttf"}
SHAPE_MAP = {"圆形章": "circle", "椭圆章": "ellipse"}

# 界面配色（紧致企业级：浅灰蓝底 + 白色卡片 + 印章红点缀）
BG, CARD, BORDER, ACCENT, ACCENT_DK, TEXT, SUBTLE = \
    "#eef1f6", "#ffffff", "#dde2ea", "#c62828", "#a51f1f", "#1f2937", "#6b7280"
STATUS_BG, STATUS_FG = "#222a33", "#9fb0c3"
WARN_BG, WARN_FG = "#fff7cd", "#d92121"


# ─────────────────────────── 工具函数 ───────────────────────────
def _is_expired():
    return datetime.now() >= EXPIRE_DATE


def _fmt(val, step):
    if step >= 1:
        return f"{val:.0f}"
    if step >= 0.1:
        return f"{val:.1f}"
    return f"{val:.2f}"


def _shade(hex_color, factor=0.82):
    """对 hex 颜色做加深/变浅，用于按钮悬停态"""
    try:
        s = hex_color.lstrip("#")
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
        return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"
    except Exception:
        return hex_color


def _detect_fonts():
    """收集系统中可用的候选字体。

    Windows 字体目录通过 WINDIR 环境变量解析（兼容系统盘不在 C: 的机器），
    同时保留裸文件名候选交给 Pillow 自行搜索，以及常见 Linux/macOS 路径。
    """
    windir = os.environ.get("WINDIR", r"C:\Windows")
    win_fonts = os.path.join(windir, "Fonts")
    win_names = ["simsun.ttc", "simhei.ttf", "msyh.ttc", "simkai.ttf",
                 "simfang.ttf", "arial.ttf"]
    candidates = [os.path.join(win_fonts, n) for n in win_names]
    candidates += win_names            # 交给 Pillow 自行定位
    candidates += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    available, seen = [], set()
    for fp in candidates:
        if not fp or fp in seen:
            continue
        seen.add(fp)
        if os.path.exists(fp):
            try:
                ImageFont.truetype(fp, 12)
                available.append(fp)
            except Exception:
                pass
    if not available:
        available.append("simsun.ttc")
    return available


# ─────────────────────────── 渲染引擎 ───────────────────────────
class SealEngine:
    def __init__(self, dpi=20):
        self.dpi = dpi
        self._font_cache = {}
        self.available_fonts = _detect_fonts()
        # 记录一个可用的西文字体（数字 / 字母优先用它，字形更接近真实备案号）
        latin_names = ("arial.ttf", "liberationsans-regular.ttf", "helvetica.ttf")
        self._latin_path = next(
            (fp for fp in self.available_fonts
             if os.path.basename(fp).lower() in latin_names),
            self.available_fonts[0])

    def mm2px(self, mm):
        return int(round(mm * self.dpi))

    def get_font(self, px_size, family=None):
        px_size = max(8, int(px_size))
        target = family or "simsun.ttc"
        key = (target, px_size)
        if key in self._font_cache:
            return self._font_cache[key]
        font = None
        t = str(target).lower()
        for fp in self.available_fonts:
            if os.path.basename(fp).lower() == t or t in fp.lower():
                try:
                    font = ImageFont.truetype(fp, px_size)
                    break
                except Exception:
                    continue
        if font is None:
            for fp in self.available_fonts:
                try:
                    font = ImageFont.truetype(fp, px_size)
                    break
                except Exception:
                    continue
        self._font_cache[key] = font or ImageFont.load_default()
        return self._font_cache[key]

    def get_record_font(self, px_size):
        """备案号等纯西文文本优先使用西文字体。"""
        return self.get_font(px_size, family=self._latin_path)

    @staticmethod
    def _is_ascii(text):
        return all(ord(ch) < 128 for ch in text)

    def pick_text_font(self, px_size, text, cjk_family=None):
        """按文本内容自动选择字体。

        全角括号、汉字等非 ASCII 字符在西文字体（如 arial.ttf）中没有字形，
        会渲染成方框乱码，因此必须回落到中文字体。
        """
        if text and self._is_ascii(text):
            return self.get_record_font(px_size)
        return self.get_font(px_size, family=cjk_family)

    @staticmethod
    def text_size(draw, text, font):
        if not text:
            return 1, 1
        try:
            b = draw.textbbox((0, 0), text, font=font)
            return max(1, b[2] - b[0]), max(1, b[3] - b[1])
        except Exception:
            return len(text) * font.size // 2, font.size

    def bold_text(self, draw, x, y, text, font, color):
        sw = max(1, font.size // 48)
        try:
            draw.text((x, y), text, font=font, fill=color, stroke_width=sw, stroke_fill=color)
        except TypeError:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    draw.text((x + dx, y + dy), text, font=font, fill=color)

    def stamp_h_text(self, img, cx, y, text, font, color, width_scale=1.0):
        probe = ImageDraw.Draw(img)
        b = probe.textbbox((0, 0), text, font=font)
        tw, th = b[2] - b[0], b[3] - b[1]
        ox, oy = b[0], b[1]
        asc, desc = font.getmetrics()
        if width_scale == 1.0:
            # 垂直居中：字体框（asc+desc）居中在 y+th/2，墨水自然居中
            y_draw = int((y + th / 2.0) - (asc + desc) / 2.0)
            self.bold_text(probe, cx - tw // 2 - ox, y_draw, text, font, color)
            return tw, th
        pad = 8
        tmp_h = asc + desc + pad * 2
        tmp = Image.new("RGBA", (tw + pad * 2 + ox, tmp_h), (0, 0, 0, 0))
        # 文字在临时画布中垂直居中
        tmp_y = int((tmp_h - (asc + desc)) / 2)
        self.bold_text(ImageDraw.Draw(tmp), pad + ox, tmp_y, text, font, color)
        tmp = tmp.resize((max(1, int(tmp.width * width_scale)), tmp.height), LANCZOS)
        img.paste(tmp, (int(cx - tmp.width / 2), int(y + th / 2.0 - tmp.height / 2)), tmp)
        return tmp.width, th

    def fit_font(self, draw, text, font_getter, px_size, max_w):
        px_size = max(8, int(px_size))
        while px_size > 8:
            f = font_getter(px_size)
            if self.text_size(draw, text, f)[0] <= max_w:
                return f
            px_size = int(px_size * 0.92)
        return font_getter(px_size)

    def _draw_rotated_char(self, img, x, y, ch, font, color, angle_deg, width_scale=1.0):
        s = max(int(font.size * 2), 40)
        ci = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        cd = ImageDraw.Draw(ci)
        tw, th = self.text_size(cd, ch, font)
        self.bold_text(cd, (s - tw) // 2, (s - th) // 2, ch, font, color)
        if width_scale != 1.0:
            ci = ci.resize((max(1, int(s * width_scale)), s), LANCZOS)
        try:
            rot = ci.rotate(angle_deg, expand=True, resample=BICUBIC, fillcolor=(0, 0, 0, 0))
        except TypeError:
            rot = ci.rotate(angle_deg, expand=True, resample=BICUBIC)
        img.paste(rot, (int(x - rot.width / 2), int(y - rot.height / 2)), rot)

    def _arc_layout(self, probe, text, font_getter, px_size, arc_len, r_eff,
                    width_scale, max_spread=1.6):
        """弧形文字排版：保证相邻字符沿弧线不重叠。

        关键约束：文字沿切线排布时，字符内沿所在圆的弦长 < 字符实际宽度就会重叠。
        相邻字符所在的内沿弧长为「步距 × 内沿半径比」，其中
            内沿半径比 = r_eff / (r_eff - 字高/2)
        因此不重叠条件为：natural * eff * inner_ratio <= step

        处理顺序（保字形优先）：
          1) 等比缩小字号 —— 字形不变形，长名称效果最自然；
          2) 若已到字号下限仍放不下，再用宽度缩放轻微压窄兜底；
          3) 最后按"内沿净空"反解安全步距，避免步距被二次压缩导致内沿重叠。

        step 与 eff 必须用同一套预算，否则缩字后步距被再次削减会造成内沿重叠。
        """
        n = len(text)
        font = font_getter(max(8, int(px_size)))
        if n <= 1:
            return font, 0.0, width_scale
        spread = arc_len / (n - 1)
        if spread <= 0:
            return font, 0.0, width_scale

        def metrics(px, scale):
            f = font_getter(px)
            natural = max(self.text_size(probe, ch, f)[0] for ch in text) or 1
            ratio = r_eff / max(1.0, r_eff - f.size * 0.5)
            return f, natural, ratio

        # 阶段 1：等比缩小字号，直到"内沿弦长"足够容纳字宽
        px = max(8, int(px_size))
        while px > 8:
            _, natural, ratio = metrics(px, width_scale)
            if natural * width_scale * ratio <= spread * 1.02:
                break
            px = max(8, int(px * 0.93))

        # 阶段 2：字号到底仍放不下时，用宽度缩放兜底（下限 0.55，再小字形会明显变形）
        eff = width_scale
        while eff > 0.55:
            _, natural, ratio = metrics(px, eff)
            if natural * eff * ratio <= spread * 1.02:
                break
            eff = round(eff - 0.02, 2)

        font, natural, ratio = metrics(px, eff)
        # 阶段 3：反解安全步距。
        #   - 不超出弧长：step <= spread（不溢出）
        #   - 内沿不重叠：step >= natural*eff*ratio
        #   - 短名称适度放宽字距：step <= natural*eff*max_spread
        # 三者取交集后钳制，确保"字号 + 步距 + 缩放"三者预算一致。
        step_floor = natural * eff * ratio          # 内沿刚好不重叠所需的最小步距
        step_cap = min(spread, natural * eff * max_spread)
        if step_floor <= step_cap:
            # v1.3.1：去掉旧版"字宽×1.04"钳制——它让弧度滑块失效
            # （字距恒为 1.04 倍字宽，与请求夹角解耦，60°~180° 渲染结果相同）。
            # 改为按请求弧长等弧长铺满：step=spread，仅受字距上限 max_spread 约束。
            step = step_cap
        else:
            # 极限情况：内沿净空与弧长上限无法同时满足（字号/缩放已到下限）
            step = spread
        return font, step, eff

    def arc_text_circle(self, img, cx, cy, radius, text, font_getter, px_size, color,
                        center_deg, total_deg, direction, width_scale=1.0, max_spread=1.6):
        n = len(text)
        if n == 0 or radius <= 0 or total_deg <= 0:
            return
        probe = ImageDraw.Draw(img)
        arc_len = math.radians(total_deg) * radius
        font, step_arc, width_scale = self._arc_layout(
            probe, text, font_getter, px_size, arc_len, radius, width_scale, max_spread)
        for i, ch in enumerate(text):
            s_i = (i - (n - 1) / 2) * step_arc
            ang = math.radians(center_deg + direction * math.degrees(s_i / radius))
            x = cx + radius * math.cos(ang)
            y = cy - radius * math.sin(ang)
            self._draw_rotated_char(img, x, y, ch, font, color,
                                    math.degrees(ang) + direction * 90, width_scale)

    @staticmethod
    def _rot_norm(d):
        """把角度归一到 [0,180]，用于衡量"侧转幅度"（与方向无关）"""
        return abs(((d + 180.0) % 360.0) - 180.0)

    @staticmethod
    def _char_quad(x, y, w, h, ang_deg):
        """字符旋转矩形四角（与 _draw_rotated_char 的 PIL rotate 语义一致）"""
        th = math.radians(ang_deg)
        c, s = math.cos(th), math.sin(th)
        hw, hh = max(1.0, w / 2.0), max(1.0, h / 2.0)
        return [(x + du * c + dv * s, y - du * s + dv * c)
                for du, dv in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]

    @staticmethod
    def _sat_gap(A, B):
        """两凸多边形的最小分离间隙（>0 即不重叠），用于相邻字符净空实测"""
        best = -1e18
        for P, Q in ((A, B), (B, A)):
            for i in range(len(P)):
                x1, y1 = P[i]
                x2, y2 = P[(i + 1) % len(P)]
                ax = (-(y2 - y1), x2 - x1)
                ln = math.hypot(*ax)
                if ln < 1e-9:
                    continue
                ax = (ax[0] / ln, ax[1] / ln)
                pa = [p[0] * ax[0] + p[1] * ax[1] for p in A]
                pb = [p[0] * ax[0] + p[1] * ax[1] for p in B]
                best = max(best, max(min(pa) - max(pb), min(pb) - max(pa)))
        return best

    def arc_text_ellipse(self, img, cx, cy, a, b, text, font_getter, px_size, color,
                         total_deg, width_scale=1.0, safe=None):
        """椭圆上弧文字排版（v1.3.0 重写）。

        与旧实现的区别：
          * company_arc 真实生效——文字按请求夹角等弧长铺满（旧实现步距与弧长解耦，
            导致 180°/230°/260° 渲染结果像素级相同）；
          * 相邻字符用旋转矩形 SAT 实测净空，字号→字宽逐级自适应，硬保证不重叠
            （旧实现用"字心距 ≥ 1.06×字宽"的近似，斜排末端仍会粘连）；
          * 首尾侧转阻尼限幅，末字不再转到 140° 近似倒立；
          * 字形四角受 safe 椭圆约束，绝不越出边框内沿。

        safe: (safe_a, safe_b) 允许区域半轴（px）；None 表示不做边框约束。
        """
        n = len(text)
        if n == 0 or a <= 0 or b <= 0 or total_deg <= 0:
            return
        probe = ImageDraw.Draw(img)
        sa, sb = safe if safe else (None, None)

        def raw_rot(t):
            return math.degrees(math.atan2(-math.cos(t) / a, math.sin(t) / b))

        if n == 1:
            self._draw_rotated_char(img, cx, cy - b, text[0],
                                    font_getter(max(8, int(px_size))), color, 0.0,
                                    width_scale)
            return

        def make_param(sp):
            ta = math.radians(90.0 + sp / 2.0)
            tb = math.radians(90.0 - sp / 2.0)
            ts = [ta + (tb - ta) * i / 720 for i in range(721)]
            cum = [0.0]
            for i in range(1, 721):
                dx = a * math.cos(ts[i]) - a * math.cos(ts[i - 1])
                dy = -b * math.sin(ts[i]) + b * math.sin(ts[i - 1])
                cum.append(cum[-1] + math.hypot(dx, dy))
            return ts, cum, cum[-1]

        def solve(sp):
            ts, cum, L = make_param(sp)

            def th_at(s):
                if s <= 0.0:
                    return ts[0]
                if s >= L:
                    return ts[-1]
                j = bisect.bisect_left(cum, s)
                w = (s - cum[j - 1]) / (cum[j] - cum[j - 1] + 1e-9)
                return ts[j - 1] + (ts[j] - ts[j - 1]) * w

            def T(step):
                mid = L / 2.0
                return [th_at(mid + (i - (n - 1) / 2.0) * step) for i in range(n)]

            def pts(step):
                return [(cx + a * math.cos(t), cy - b * math.sin(t)) for t in T(step)]

            def rots(step):
                r = [raw_rot(t) for t in T(step)]
                # 防倒立归一化：把角度钳制到 [-90°, 90°]，超过 90° 翻转 180° 让字正立
                # （大夹角时端点字会越过椭圆端点进入下半弧，法向量朝下导致字倒立）
                r = [((x + 90.0) % 180.0) - 90.0 for x in r]
                # 首尾侧转阻尼限幅：位置仍按弧长铺满，仅把旋转角压到上限内
                mx = max(self._rot_norm(x) for x in r)
                if mx > ARC_ROT_LIMIT_DEG > 0:
                    r = [x * (ARC_ROT_LIMIT_DEG / mx) for x in r]
                return r

            def mcd(step):
                P = pts(step)
                return min(math.hypot(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1])
                           for i in range(len(P) - 1))

            def boxes(px, eff, step):
                f = font_getter(max(8, int(px)))
                P, R = pts(step), rots(step)
                out = []
                for i, ch in enumerate(text):
                    tw, th_ = self.text_size(probe, ch, f)
                    out.append(self._char_quad(P[i][0], P[i][1], tw * eff, th_, R[i]))
                return f, out

            px, eff, step = max(8, int(px_size)), float(width_scale), L / (n - 1)

            # 字距上限：短名/大弧时收紧内部跨度，避免被拉散
            f0 = font_getter(px)
            tw0 = max(self.text_size(probe, ch, f0)[0] for ch in text) * eff
            cap = ARC_STEP_MAX_RATIO * max(1.0, tw0)
            if mcd(step) > cap:
                lo, hi = 0.0, step
                for _ in range(44):
                    m = 0.5 * (lo + hi)
                    if mcd(m) < cap:
                        lo = m
                    else:
                        hi = m
                step = hi

            # 净空 + 边框 双重自适应：先缩字号保字形，字号到底再压字宽兜底。
            # 两者都用字号收敛，因此夹角不会被"出界"逼回最小（否则滑块又会失效）。
            for _ in range(120):
                _, bs = boxes(px, eff, step)
                if min(self._sat_gap(bs[i], bs[i + 1])
                       for i in range(len(bs) - 1)) < ARC_CLEAR_MARGIN or not _all_in(bs):
                    npx = max(8, int(round(px * 0.97)))
                    if npx == px:
                        if eff > ARC_EFF_FLOOR:
                            eff = max(ARC_EFF_FLOOR, round(eff - 0.02, 3))
                        else:
                            break
                    else:
                        px = npx
                    continue
                break
            return pts(step), rots(step), px, eff

        def boxes_at(P, R, px, eff):
            f = font_getter(max(8, int(px)))
            out = []
            for i, ch in enumerate(text):
                tw, th_ = self.text_size(probe, ch, f)
                out.append(self._char_quad(P[i][0], P[i][1], tw * eff, th_, R[i]))
            return out

        def _all_in(bs):
            if not sa:
                return True
            for q in bs:
                for X, Y in q:
                    if math.hypot((X - cx) / sa, (Y - cy) / sb) > 1.0:
                        return False
            return True

        span = min(max(float(total_deg), ARC_SPAN_MIN), ARC_SPAN_MAX)
        P, R, px, eff = solve(span)

        # 极端扁平椭圆：字号已到底仍出界 → 二分回收夹角（正常尺寸下不会触发）
        if sa and not _all_in(boxes_at(P, R, px, eff)):
            lo, hi = ARC_SPAN_MIN, span
            Pl, Rl, pxl, effl = solve(lo)
            if not _all_in(boxes_at(Pl, Rl, pxl, effl)):
                P, R, px, eff = Pl, Rl, pxl, effl
            else:
                for _ in range(14):
                    m = 0.5 * (lo + hi)
                    Pm, Rm, pxm, effm = solve(m)
                    if _all_in(boxes_at(Pm, Rm, pxm, effm)):
                        lo, P, R, px, eff = m, Pm, Rm, pxm, effm
                    else:
                        hi = m

        f = font_getter(px)
        for i, ch in enumerate(text):
            self._draw_rotated_char(img, P[i][0], P[i][1], ch, f, color, R[i], eff)

    # ── 防伪纹 ──
    @staticmethod
    def _security_seed(company, record):
        return int(hashlib.md5(f"{company}{record}".encode("utf-8")).hexdigest()[:8], 16)

    @staticmethod
    def _border_break_plan(n_gaps, seed_val):
        """统一规划边框断纹分段，圆形 / 椭圆共用，消除重复逻辑。

        返回 [(start_deg, end_deg), ...] 的可见线段列表。
        """
        rng = random.Random(seed_val)
        gap_angle = rng.uniform(0.5, 0.7)
        if n_gaps * gap_angle >= 340:
            gap_angle = 340.0 / n_gaps
        remaining = 360.0 - n_gaps * gap_angle
        n_segments = n_gaps + 1
        base_seg = remaining / n_segments
        segments, cur = [], 0.0
        for i in range(n_segments):
            seg = base_seg * rng.uniform(0.9, 1.1)
            if i == n_segments - 1:
                seg = min(seg, 360.0 - cur)
            end = cur + seg
            if end > cur:
                segments.append((cur, end))
            cur = end + gap_angle
        return segments

    def draw_border_breaks(self, draw, cx, cy, radius, border_width, color, n_gaps, seed_val):
        if radius <= 0:
            return
        bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
        if n_gaps <= 0:
            draw.ellipse(bbox, outline=color, width=border_width)
            return
        for start, end in self._border_break_plan(n_gaps, seed_val):
            draw.arc(bbox, start=start, end=end, fill=color, width=border_width)

    def draw_border_breaks_ellipse(self, draw, cx, cy, a, b, border_width, color, n_gaps, seed_val):
        if a <= 0 or b <= 0:
            return
        if n_gaps <= 0:
            draw.ellipse([cx - a, cy - b, cx + a, cy + b], outline=color, width=border_width)
            return
        for start, end in self._border_break_plan(n_gaps, seed_val):
            n_pts = max(3, int((end - start) / 2))
            pts = []
            for j in range(n_pts + 1):
                t = math.radians(start + (end - start) * j / n_pts)
                pts.append((cx + a * math.cos(t), cy - b * math.sin(t)))
            for j in range(len(pts) - 1):
                draw.line([pts[j], pts[j + 1]], fill=color, width=border_width)

    # ── 仿真效果 ──
    def apply_simulation(self, img, params):
        """印泥质感仿真。

        性能优化：低频噪声（tex / combined / bn）在 1/4 分辨率生成后放大，
        边界断纹区域在 1/4 分辨率计算 dilate/erode，视觉几乎一致但大幅提速。
        随机种子由内容参数派生，同一组参数渲染结果稳定（预览 = 导出）。
        健壮性：使用独立 numpy Generator（不污染全局随机态），参数缺项自动兜底。
        """
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        seed_src = "|".join(str(params.get(k, "")) for k in (
            "company_name", "record_no", "seal_type", "seal_color",
            "sim_noise", "sim_blur", "sim_break", "sim_security", "seal_shape"))
        rng = np.random.default_rng(self._security_seed(seed_src, "sim") % (2 ** 32))

        ns = max(0.1, float(params.get("sim_noise", SIM_DEFAULTS["sim_noise"])))
        bs = max(0.1, float(params.get("sim_blur", SIM_DEFAULTS["sim_blur"])))
        br = max(0.0, float(params.get("sim_break", SIM_DEFAULTS["sim_break"])))
        base_rgb = np.array(COLOR_RGB.get(params.get("seal_color", "red"), (178, 22, 22)),
                            dtype=np.float32)
        arr = np.asarray(img, dtype=np.float32)
        alpha = arr[:, :, 3].copy()
        rgb = arr[:, :, :3].copy()
        mask = (alpha > 10)
        h, w = alpha.shape
        if not mask.any():          # 空白图直接返回，避免无意义计算
            return img

        def noise(radius, scale=0.25):
            """低频噪声：小分辨率生成 + 放大，速度约 16 倍"""
            sh, sw = max(2, int(h * scale)), max(2, int(w * scale))
            n = (rng.random((sh, sw)) * 255).astype(np.uint8)
            small = Image.fromarray(n).filter(ImageFilter.GaussianBlur(max(1.0, radius * scale)))
            return np.array(small.resize((w, h), LANCZOS)) / 255.0

        tex = noise(1.4)
        tex = (tex - tex.min()) / (tex.max() - tex.min() + 1e-6) * ns
        dark, light = base_rgb * 0.88, np.clip(base_rgb * 1.1, 0, 255)
        t = np.clip(tex[:, :, None] * 0.8, 0, 1)
        rgb[mask] = light * (1 - t[mask]) + dark * t[mask]
        alpha[mask] = alpha[mask] * (0.6 + 0.4 * tex[mask])

        # 高频颗粒保持全分辨率（真实印泥颗粒感），grain 与 mask 只乘一次
        grain = rng.standard_normal(alpha.shape, dtype=np.float32) * 15 * ns
        g = grain * mask
        rgb[:, :, 0] += g * 0.5
        rgb[:, :, 1] += g * 0.2
        rgb[:, :, 2] += g * 0.2
        rgb = np.clip(rgb, 0, 255)
        alpha = np.clip(alpha + g * 0.3, 0, 255)

        if br > 0.05:
            combined = noise(0.8) * 0.6 + noise(2.0) * 0.4
            combined = (combined - combined.min()) / (combined.max() - combined.min() + 1e-6)
            area = min(0.6, 0.05 + 0.11 * br)
            depth = min(0.95, 0.15 + 0.16 * br)
            bm = ((combined > 1.0 - area).astype(np.float32) * mask * 255).astype(np.uint8)
            alpha = alpha * (1 - depth * (np.array(
                Image.fromarray(bm).filter(ImageFilter.GaussianBlur(1.2))) / 255.0))
            # 边界断纹区域：小分辨率计算，提速明显
            sh, sw = max(2, h // 4), max(2, w // 4)
            small_mask = Image.fromarray((mask * 255).astype(np.uint8)).resize((sw, sh), LANCZOS)
            dil = np.array(small_mask.filter(ImageFilter.MaxFilter(3)))
            ero = np.array(small_mask.filter(ImageFilter.MinFilter(3)))
            zone = np.clip(dil - ero, 0, 255) / 255.0
            zone = np.array(Image.fromarray((zone * 255).astype(np.uint8))
                            .resize((w, h), LANCZOS)) / 255.0
            bn = noise(0.5)
            bth = 1.0 - min(0.8, 0.1 + 0.14 * br)
            bb = ((bn > bth).astype(np.float32) * zone * 255).astype(np.uint8)
            bb = np.array(Image.fromarray(bb).filter(ImageFilter.GaussianBlur(1.0))) / 255.0
            alpha = alpha * (1 - 0.99 * min(1.0, br / 2.5) * bb)

        a_img = Image.fromarray(np.clip(alpha, 0, 255).astype(np.uint8))
        spread = np.array(a_img.filter(ImageFilter.GaussianBlur(0.2 + 1.0 * bs))).astype(np.float32)
        alpha = np.clip(alpha + np.clip(spread - alpha, 0, 255) * 0.6, 0, 255)
        arr[:, :, :3] = rgb
        arr[:, :, 3] = np.clip(alpha, 0, 255)
        result = Image.fromarray(arr.astype(np.uint8), "RGBA")
        fb = 0.1 + 0.5 * bs
        return result.filter(ImageFilter.GaussianBlur(fb)) if fb > 0.1 else result

    # ── 圆形章 ──
    def render_circle(self, p):
        color = COLOR_RGB.get(p.get("seal_color", "red"), (178, 22, 22))
        diameter = self.mm2px(p.get("circle_diameter", 42.0))
        pad = self.mm2px(2)
        size = diameter + 2 * pad
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = cy = size // 2
        border_w = max(1, self.mm2px(p.get("border_width", 1.2)))
        border_w = min(border_w, max(1, diameter // 8))          # 防呆：边框不挤爆版面
        gap = max(1, self.mm2px(p.get("arc_gap", 1.2)))
        star_r = max(1, min(self.mm2px(p.get("star_radius", 7.0)), diameter // 4))
        path_radius = (diameter - border_w) // 2

        n_gaps = int(round(float(p.get("sim_security", SIM_DEFAULTS["sim_security"]))))
        seed = self._security_seed(p.get("company_name", ""), p.get("record_no", ""))
        self.draw_border_breaks(draw, cx, cy, path_radius, border_w, color, n_gaps, seed)

        inner_r = path_radius - border_w
        comp_px = self.mm2px(p.get("font_size_company", 7.0))
        rec_px = self.mm2px(p.get("font_size_record", 2.0))
        avail = inner_r - gap                      # 文字外沿距边框的极限位置

        # 公司名 / 备案号都要让开五角星：字号与弧半径双向求解
        # 约束：star + 字高/2 + gap ≤ 弧半径 ≤ inner - gap - 字高/2
        # 两式相加得字高上限 comp ≤ inner - star - 2*gap
        room = inner_r - star_r - 2 * gap
        comp_px = max(16, min(comp_px, max(24, room)))
        rec_px = max(16, min(rec_px, max(24, room)))

        # 中心文字块规划（先于弧线，备案号弧半径需要避让它）
        seal_type, seal_no = p.get("seal_type", "").strip(), p.get("seal_no", "").strip()
        max_w = diameter * 0.62
        cent_scale = p.get("center_width_scale", 1.0)
        type_px = self.mm2px(p.get("font_size_center", 4.0))
        no_px = self.mm2px(p.get("font_size_no", 2.2))
        gap_line = self.mm2px(0.5)
        need_h = ((type_px if seal_type else 0) + (no_px if seal_no else 0)
                  + (gap_line if (seal_type and seal_no) else 0))
        y_block = cy + star_r + max(gap // 2, self.mm2px(0.5))
        zone_bottom = cy + inner_r - gap
        y_min = cy + int(star_r * 0.55)            # 极端配置下允许上移的下限
        avail_h = zone_bottom - y_block
        if need_h > avail_h:
            if avail_h > 0:
                k = max(0.35, avail_h / need_h)
                type_px = max(16, int(type_px * k))
                no_px = max(16, int(no_px * k))
                need_h = ((type_px if seal_type else 0) + (no_px if seal_no else 0)
                          + (gap_line if (seal_type and seal_no) else 0))
            y_block = max(y_min, zone_bottom - need_h)   # 底部对齐可用区，避免裁剪

        # 正常参数下"贴边半径"天然满足五角星净空；冲突时保持贴边（文字不出框）。
        # 备案号弧下沿不得高于中心文字块底部；整体不得压到边框环。
        r_top = min(max(comp_px // 2 + gap, avail - comp_px // 2),
                    inner_r - comp_px // 2 - 2)
        r_bot = min(max(rec_px // 2 + gap, avail - rec_px // 2,
                        y_block + need_h - cy + rec_px // 2 + 2),
                    inner_r - rec_px // 2 - 2)

        fam = p.get("font_family", "simsun.ttc")
        font_getter = lambda px: self.get_font(px, family=fam)
        rec_getter = lambda px: self.pick_text_font(px, p.get("record_no", ""), fam)
        no_getter = lambda px: self.pick_text_font(px, f"（{p.get('seal_no', '')}）", fam)

        self.arc_text_circle(img, cx, cy, r_top, p.get("company_name", "").strip(),
                             font_getter, comp_px, color,
                             90, p.get("company_arc", 250.0), -1, p.get("company_width_scale", 1.0))
        self.arc_text_circle(img, cx, cy, r_bot, p.get("record_no", "").strip(),
                             rec_getter, rec_px, color,
                             270, p.get("record_arc", 80.0), +1, 1.0, max_spread=3.2)

        pts = []
        for i in range(10):
            r = star_r if i % 2 == 0 else star_r * 0.382
            ang = math.radians(90 - i * 36)
            pts.append((cx + r * math.cos(ang), cy - r * math.sin(ang)))
        draw.polygon(pts, fill=color)

        if seal_type:
            f = self.fit_font(draw, seal_type, font_getter, type_px, max_w / cent_scale)
            _, th = self.stamp_h_text(img, cx, y_block, seal_type, f, color, cent_scale)
            y_block += th + gap_line
        if seal_no:
            no_text = f"（{seal_no}）"
            f = self.fit_font(draw, no_text, no_getter, no_px, max_w)
            self.stamp_h_text(img, cx, y_block, no_text, f, color)
        return img

    # ── 椭圆章 ──
    def render_ellipse(self, p):
        color = COLOR_RGB.get(p.get("seal_color", "red"), (178, 22, 22))
        wa = self.mm2px(p.get("axis_a", 40.0))
        ha = self.mm2px(p.get("axis_b", 30.0))
        pad = self.mm2px(2)
        W, H = wa + 2 * pad, ha + 2 * pad
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx, cy = W // 2, H // 2
        border_w = max(1, self.mm2px(p.get("border_width", 1.0)))
        border_w = min(border_w, max(1, min(wa, ha) // 8))       # 防呆
        gap = max(1, min(self.mm2px(p.get("arc_gap", 1.0)), ha // 10))
        a = (wa - border_w) / 2
        b = (ha - border_w) / 2
        # 边框内沿半轴：椭圆线宽 border_w，内沿再内缩 border_w/2（与绘制/度量口径一致）
        a_in = a - border_w / 2
        b_in = b - border_w / 2
        # 字与边线内侧距离：取"弧形间距"与规范 0.5mm 的较大者
        inset = max(gap, self.mm2px(ARC_BORDER_CLEAR_MM))

        n_gaps = int(round(float(p.get("sim_security", SIM_DEFAULTS["sim_security"]))))
        seed = self._security_seed(p.get("company_name", ""), p.get("record_no", ""))
        self.draw_border_breaks_ellipse(draw, cx, cy, a, b, border_w, color, n_gaps, seed)

        comp_px = self.mm2px(p.get("font_size_company", 4.2))
        # 竖向空间不足以容纳文字带（边框 + 净空 + 字高）时自动缩小字号，
        # 避免文字弧半径被推成负值、文字越出椭圆
        space_v = max(8, b_in - inset)
        comp_px = max(16, min(comp_px, int(space_v * 0.6)))
        a_t = max(comp_px / 2, a_in - inset - comp_px / 2)
        b_t = max(comp_px / 2, b_in - inset - comp_px / 2)

        fam = p.get("font_family", "simsun.ttc")
        font_getter = lambda px: self.get_font(px, family=fam)
        rec_getter = lambda px: self.pick_text_font(px, p.get("record_no", ""), fam)
        no_getter = lambda px: self.pick_text_font(px, f"（{p.get('seal_no', '')}）", fam)

        # v1.3.0：取消"长名自动扩弧"——旧逻辑会让长名同时"夹角更大 + 字号更小"，
        # 首尾侧转反而更严重（实测末字侧转 146°，近似倒立）。
        # 现将「夹角钳制 + 字号自适应 + 字距上下限 + 边框净空」全部交由弧排求解器完成。
        # 允许区域 safe：边框内沿再内缩 ARC_BORDER_CLEAR_MM（规范：字与边线内侧 0.5mm）。
        safe_a, safe_b = a_in - inset, b_in - inset        # 字形四角的允许区域
        safe = (safe_a, safe_b) if (safe_a > 4 and safe_b > 4) else None

        name = p.get("company_name", "").strip()
        self.arc_text_ellipse(img, cx, cy, a_t, b_t, name,
                              font_getter, comp_px, color,
                              float(p.get("company_arc", 220.0)),
                              p.get("company_width_scale", 1.0), safe)

        seal_type, seal_no = p.get("seal_type", "").strip(), p.get("seal_no", "").strip()
        record = p.get("record_no", "").strip()
        rec_scale = p.get("record_width_scale", 1.0)
        cent_scale = p.get("center_width_scale", 1.0)
        items = []
        # 中心横排文字的最大可用宽度：取椭圆内沿在 y=cy 处全宽的 90%
        # （旧值 wa*0.62 过宽，长税号左右两端顶到边框被截断）
        center_max_w = 2 * safe[0] * 0.9 if safe else wa * 0.7
        if record:
            items.append((record, rec_getter, self.mm2px(p.get("font_size_record", 2.0)),
                          center_max_w, rec_scale, self.mm2px(0.8)))
        if seal_type:
            items.append((seal_type, font_getter, self.mm2px(p.get("font_size_center", 4.6)),
                          center_max_w, cent_scale, self.mm2px(0.6)))
        if seal_no:
            items.append((f"（{seal_no}）", no_getter,
                          self.mm2px(p.get("font_size_no", 2.2)), wa * 0.70, 1.0, 0))
        if items:
            # 竖向空间不足时整块等比缩小，避免文字叠到公司名弧线 / 越出椭圆
            room_v = max(FONT_FLOOR * 2, int(2 * (b_t - comp_px / 2) - gap))
            est = sum(px + g for _, _, px, _, _, g in items)
            if est > room_v:
                k = max(0.3, room_v / est)
                items = [(t, fg, max(FONT_FLOOR, int(px * k)), mw, sc, g)
                         for t, fg, px, mw, sc, g in items]
            blocks = []
            for text, fg, px, mw, sc, g in items:
                px2 = max(8, int(px))
                while px2 > 8:
                    f = fg(px2)
                    tw, th = self.text_size(draw, text, f)
                    if tw * sc <= mw:
                        break
                    px2 = int(px2 * 0.92)
                f = fg(px2)
                tw, th = self.text_size(draw, text, f)
                blocks.append((text, f, th, sc, g, tw))
            # 印文定位（《国家税务总局公告 2011 年第 7 号》式样）：
            #   中央刊统一社会信用代码/纳税人识别号 → 下刊"发票专用章" → 最下刊顺序编码。
            #   规范：中心线到"发票专用章"顶端 4.2mm、到顺序编码顶端 10mm、编码字高 2.2mm。
            #   仅当该定位在本章几何内确实放得下时才采用；放不下（如扁椭圆）则退回
            #   "整块居中略偏上"的通用排版，避免文字越出椭圆。
            use_spec = "发票专用章" in seal_type
            if use_spec:
                tops = []
                for idx, (text, f, th, sc, g, tw) in enumerate(blocks):
                    if idx == 0:
                        tops.append(cy - th / 2)             # 税号居中于章心
                    elif idx == 1:
                        tops.append(cy + self.mm2px(4.2))    # "发票专用章"下刊
                    else:
                        tops.append(cy + self.mm2px(10.0))   # 顺序编码最下
                # 逐行校验：每行按自身宽度反解椭圆允许的垂直半高，全部放得下才采用规范定位
                for (text, f, th, sc, g, tw), y in zip(blocks, tops):
                    hw = (tw * sc / 2.0) / safe[0] if safe else 0.0
                    lim = math.sqrt(max(0.0, 1.0 - hw * hw)) * (safe[1] if safe else b_t)
                    if abs(y - cy) + th / 2.0 > lim:
                        use_spec = False
                        break
            if use_spec:
                for (text, f, th, sc, g, tw), y in zip(blocks, tops):
                    self.stamp_h_text(img, cx, int(y), text, f, color, sc)
            else:
                total_h = sum(b[2] + b[4] for b in blocks) - blocks[-1][4]
                # 垂直居中略偏上：公司名占上弧，下方留白多于上方，居中偏上视觉最平衡
                y = cy - total_h / 2 - self.mm2px(0.5)
                for text, f, th, sc, g, tw in blocks:
                    self.stamp_h_text(img, cx, int(y), text, f, color, sc)
                    y += th + g
        return img

    def render(self, params):
        if params.get("seal_shape") == "ellipse":
            img = self.render_ellipse(params)
        else:
            img = self.render_circle(params)
        if params.get("enable_simulation", True):
            img = self.apply_simulation(img, params)
        return img


# ─────────────────────────── UI 层 ───────────────────────────
class RoundedButton(tk.Canvas):
    """Canvas 自绘圆角按钮，支持悬停高亮，可随布局拉伸"""

    def __init__(self, parent, text, command, bg=ACCENT, fg="#ffffff",
                 font=("微软雅黑", 10, "bold"), radius=10, padx=16, pady=6):
        self._text = text
        self._command = command
        self._bg = bg
        self._hover_bg = _shade(bg)
        self._fg = fg
        self._font = font
        self._radius = radius
        self._cur = bg
        probe = tk.Label(parent, text=text, font=font)
        tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
        probe.destroy()
        super().__init__(parent, width=tw + padx * 2, height=th + pady * 2,
                         bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", lambda e: self._draw(self._cur))

    def _draw(self, fill):
        self._cur = fill
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        r = min(self._radius, w // 2, h // 2)
        pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
        self.create_polygon(pts, smooth=True, fill=fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg, font=self._font)

    def _on_enter(self, e):
        self._draw(self._hover_bg)

    def _on_leave(self, e):
        self._draw(self._bg)

    def _on_click(self, e):
        self._command()


class SealUI:
    # 关闭仿真后仍生效的参数（防伪纹属于几何特征，不依赖仿真）
    _SIM_ONLY_KEYS = ("sim_noise", "sim_blur", "sim_break")

    def __init__(self, root, on_change, on_download, on_reset):
        self.root = root
        self.on_change = on_change
        self.on_download = on_download
        self.on_reset = on_reset
        self.widgets = {}
        self.group_frames = {}
        self._render_job = None
        self.preview_info = None
        self.status_left = None
        self.status_right = None
        self._build()
        self._set_sim_state(True)

    # ── 基础构件 ──
    @staticmethod
    def _card(parent):
        return tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)

    @staticmethod
    def _section(parent, text):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X, pady=(10, 4))
        bar = tk.Frame(row, bg=ACCENT, width=4, height=16)
        bar.pack(side=tk.LEFT, padx=(0, 6))
        bar.pack_propagate(False)
        tk.Label(row, text=text, bg=CARD, fg=TEXT,
                 font=("微软雅黑", 11, "bold")).pack(side=tk.LEFT)

    @staticmethod
    def _combo(parent, values, default, width, command):
        cb = ttk.Combobox(parent, values=values, state="readonly", width=width,
                          font=("微软雅黑", 9))
        cb.set(default)
        cb.bind("<<ComboboxSelected>>", command)
        return cb

    # ── 组装界面 ──
    def _build(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        # ttk 控件样式定制
        style.configure("TCombobox", padding=4, fieldbackground="#ffffff", background="#ffffff",
                        foreground=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        arrowcolor=ACCENT)
        style.configure("TEntry", padding=3, fieldbackground="#ffffff",
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TScale", background=CARD, troughcolor="#e4e9f0", bordercolor=BORDER,
                        lightcolor=ACCENT, darkcolor=ACCENT, gripcount=0)
        style.map("TScale", background=[("active", CARD)])
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=("微软雅黑", 9))
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Vertical.TScrollbar", background="#cfd6e0", troughcolor=CARD,
                        bordercolor=CARD, arrowcolor="#7a8699")

        main = tk.Frame(self.root, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)

        # 顶部标题栏
        top = tk.Frame(main, bg=CARD, height=52)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Frame(top, bg=ACCENT, width=6).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(top, text="标准电子印章生成工具", bg=CARD, fg=TEXT,
                 font=("微软雅黑", 15, "bold")).pack(side=tk.LEFT, padx=(14, 6))
        tk.Label(top, text="圆形章 / 椭圆形章", bg=CARD, fg=SUBTLE,
                 font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Label(top, text=APP_VERSION, bg=CARD, fg=ACCENT,
                 font=("微软雅黑", 9, "bold")).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(top, text="重要提示：仅供学习参考，请勿用于非法用途，否则后果自负！",
                 bg=WARN_BG, fg=WARN_FG, font=("微软雅黑", 10, "bold"),
                 padx=14, pady=5).pack(side=tk.RIGHT, padx=20)
        tk.Frame(main, bg=BORDER, height=1).pack(fill=tk.X)

        # 底部状态栏
        status = tk.Frame(main, bg=STATUS_BG, height=26)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        status.pack_propagate(False)
        self.status_left = tk.Label(status, text="就绪", bg=STATUS_BG, fg=STATUS_FG,
                                    font=("微软雅黑", 9))
        self.status_left.pack(side=tk.LEFT, padx=12)
        self.status_right = tk.Label(status, text="", bg=STATUS_BG, fg=STATUS_FG,
                                     font=("微软雅黑", 9))
        self.status_right.pack(side=tk.RIGHT, padx=12)

        # 三栏布局
        pane = tk.Frame(main, bg=BG)
        pane.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        pane.columnconfigure(0, weight=10, uniform="c")
        pane.columnconfigure(1, weight=13, uniform="c")
        pane.columnconfigure(2, weight=15, uniform="c")
        pane.rowconfigure(0, weight=1)

        # 左栏：基础信息 + 仿真效果
        left = self._card(pane)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        lf = tk.Frame(left, bg=CARD)
        lf.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self._section(lf, "基础信息")
        self._add_group(lf, "base")
        self._section(lf, "仿真效果")
        self._add_group(lf, "sim")
        chk = ttk.Checkbutton(lf, text="启用印章仿真", command=self._on_sim_toggle)
        chk.state(["selected"])
        chk.pack(anchor="w", pady=(6, 0))
        self.widgets["enable_simulation"] = chk
        tk.Label(lf, text="仿真开启后印泥更具真实盖章质感\n（防伪纹为几何特征，不受仿真开关影响）",
                 bg=CARD, fg=SUBTLE, font=("微软雅黑", 8), justify="left").pack(anchor="w", pady=(2, 10))

        # 中栏：样式设置
        mid = self._card(pane)
        mid.grid(row=0, column=1, sticky="nsew", padx=7)
        mf = tk.Frame(mid, bg=CARD)
        mf.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self._section(mf, "样式设置")

        row_t = tk.Frame(mf, bg=CARD)
        row_t.pack(fill=tk.X, pady=3)
        tk.Label(row_t, text="章形：", bg=CARD, fg=TEXT, width=6,
                 anchor="w", font=("微软雅黑", 9)).pack(side=tk.LEFT)
        cb_shape = self._combo(row_t, list(SHAPE_MAP.keys()), "圆形章", 8,
                               lambda e: self._on_shape_change())
        cb_shape.pack(side=tk.LEFT, padx=(0, 8))
        self.widgets["seal_shape"] = cb_shape
        tk.Label(row_t, text="类型：", bg=CARD, fg=TEXT, width=6,
                 anchor="w", font=("微软雅黑", 9)).pack(side=tk.LEFT)
        cb_type = self._combo(row_t, list(CIRCLE_PRESETS.keys()), "国有企业/股份公司", 14,
                              lambda e: self._apply_preset())
        cb_type.pack(side=tk.LEFT)
        self.widgets["circle_subtype"] = cb_type

        row1 = tk.Frame(mf, bg=CARD)
        row1.pack(fill=tk.X, pady=3)
        for label, key, options, default in [
            ("颜色：", "seal_color_combo", list(COLORS.keys()), "红色"),
            ("字体：", "font_family", list(FONT_MAP.keys()), "宋体"),
        ]:
            tk.Label(row1, text=label, bg=CARD, fg=TEXT, width=5, anchor="w",
                     font=("微软雅黑", 9)).pack(side=tk.LEFT)
            cb = self._combo(row1, options, default, 7, self._delay_change)
            cb.pack(side=tk.LEFT, padx=(0, 6))
            self.widgets[key] = cb
        RoundedButton(row1, "恢复默认", self.on_reset, bg="#f0ad4e").pack(side=tk.RIGHT)

        tk.Frame(mf, bg=BORDER, height=1).pack(fill=tk.X, pady=6)
        for g in ("circle", "ellipse", "common"):
            gf = tk.Frame(mf, bg=CARD)
            self.group_frames[g] = gf
            self._add_group(gf, g)

        # 右栏：预览 + 下载
        right = self._card(pane)
        right.grid(row=0, column=2, sticky="nsew", padx=(7, 0))
        rf = tk.Frame(right, bg=CARD)
        rf.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self._section(rf, "印章预览")
        self.preview_frame = tk.Frame(rf, bg="#f7f9fc", width=360, height=360,
                                      highlightbackground=BORDER, highlightthickness=1)
        self.preview_frame.pack(pady=8)
        self.preview_frame.pack_propagate(False)
        self.preview_label = tk.Label(self.preview_frame, bg="#f7f9fc", text="生成中…",
                                      fg=SUBTLE, font=("微软雅黑", 10))
        self.preview_label.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_info = tk.Label(rf, text="", bg=CARD, fg=SUBTLE, font=("微软雅黑", 8))
        self.preview_info.pack(pady=(0, 6))
        RoundedButton(rf, "下载印章（透明背景 PNG）", self.on_download).pack(fill=tk.X, pady=6)
        if _SPONSOR_OK:
            try:
                _show_sponsor_btn(
                    rf,
                    font=("微软雅黑", 8),
                    padx=10, pady=3,
                    bg="#f8fafc", fg="#94a3b8",
                    activebackground="#eef2f7", activeforeground="#64748b",
                    bd=0,
                ).pack(pady=(0, 4))
            except Exception:
                pass
        tk.Label(rf, text="导出约 508 DPI，已写入物理分辨率信息\n在 Word / WPS 中插入即为真实毫米尺寸",
                 bg=CARD, fg=SUBTLE, font=("微软雅黑", 8), justify="center").pack()

    def _add_group(self, parent, group):
        for cfg in UI_ITEMS:
            if cfg[0] != group:
                continue
            if cfg[1] == "entry":
                self._add_entry(parent, cfg[2], cfg[3])
            else:
                self._add_slider(parent, cfg[2], cfg[3], cfg[4], cfg[5], cfg[6], cfg[7])

    def _add_entry(self, parent, label, key):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label, bg=CARD, fg=TEXT, width=8, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        e = ttk.Entry(row, font=("微软雅黑", 9))
        e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        e.insert(0, DEFAULT_PARAMS.get(key, ""))
        e.bind("<KeyRelease>", self._delay_change)
        self.widgets[key] = e

    def _add_slider(self, parent, label, key, minv, maxv, step, unit=""):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label, bg=CARD, fg=TEXT, width=10, anchor="w",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        sl = ttk.Scale(row, from_=minv, to=maxv, orient=tk.HORIZONTAL,
                       command=lambda v, k=key, s=step: self._slider_cb(k, float(v), s))
        sl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        inp = ttk.Entry(row, width=6, font=("微软雅黑", 9))
        inp.pack(side=tk.LEFT)
        inp.bind("<KeyRelease>", lambda e, k=key, w=inp, mi=minv, ma=maxv, s=step:
                 self._entry_cb(k, w, mi, ma, s))
        inp.bind("<Return>", lambda e, k=key, w=inp, mi=minv, ma=maxv, s=step:
                 self._entry_commit(k, w, mi, ma, s))
        inp.bind("<FocusOut>", lambda e, k=key, w=inp, mi=minv, ma=maxv, s=step:
                 self._entry_commit(k, w, mi, ma, s))
        tk.Label(row, text=unit, bg=CARD, fg=SUBTLE, width=3,
                 font=("微软雅黑", 8)).pack(side=tk.LEFT, padx=2)
        self.widgets[key] = (sl, inp, unit, minv, maxv, step)
        default_val = DEFAULT_PARAMS.get(key, minv)
        sl.set(default_val)
        self._set_entry(inp, default_val, step)

    def _set_entry(self, entry, val, step):
        entry.delete(0, tk.END)
        entry.insert(0, _fmt(val, step))

    def _slider_cb(self, key, val, step):
        inp = self.widgets[key][1]
        # 输入框正在编辑时不回写，避免打字被干扰、光标跳动
        if self.root.focus_get() is not inp:
            self._set_entry(inp, val, step)
        self._delay_change()

    def _entry_cb(self, key, entry, minv, maxv, step):
        """输入过程中：只联动滑块，不改写输入框内容。"""
        s = "".join(c for c in entry.get() if c in "0123456789.")
        if s in ("", "."):
            return
        try:
            v = float(s)
        except ValueError:
            return
        v = round(max(minv, min(maxv, v)), 2)
        self.widgets[key][0].set(v)
        self._delay_change()

    def _entry_commit(self, key, entry, minv, maxv, step):
        """失焦 / 回车时提交：非法内容回退为滑块当前值。"""
        s = "".join(c for c in entry.get() if c in "0123456789.")
        try:
            v = float(s)
        except ValueError:
            v = self.widgets[key][0].get()
        v = round(max(minv, min(maxv, v)), 2)
        self.widgets[key][0].set(v)
        self._set_entry(entry, v, step)
        self._delay_change()

    def _delay_change(self, event=None):
        if self._render_job:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(120, self._do_change)

    def _do_change(self):
        self._render_job = None
        self.on_change()

    # ── 仿真开关联动 ──
    def _on_sim_toggle(self):
        self._set_sim_state("selected" in self.widgets["enable_simulation"].state())
        self._delay_change()

    def _set_sim_state(self, on):
        for key in self._SIM_ONLY_KEYS:
            widget = self.widgets.get(key)
            if not isinstance(widget, tuple):
                continue
            sl, inp = widget[0], widget[1]
            try:
                sl.state(["!disabled"] if on else ["disabled"])
            except Exception:
                pass
            inp.config(state="normal" if on else "disabled")

    def _on_shape_change(self):
        shape = SHAPE_MAP.get(self.widgets["seal_shape"].get(), "circle")
        if shape == "circle":
            self.widgets["font_family"].set("宋体")
        presets = ELLIPSE_PRESETS if shape == "ellipse" else CIRCLE_PRESETS
        cb = self.widgets["circle_subtype"]
        cb["values"] = list(presets.keys())
        cb.set(next(iter(presets)))
        self._apply_arc_range(shape)      # 先调量程，再套预置（避免预置值被旧量程夹掉）
        self._apply_preset()

    def _apply_preset(self):
        presets = (ELLIPSE_PRESETS
                   if SHAPE_MAP.get(self.widgets["seal_shape"].get()) == "ellipse"
                   else CIRCLE_PRESETS)
        name = self.widgets["circle_subtype"].get()
        preset = presets.get(name, {})
        for k, v in preset.items():
            if k in self.widgets and isinstance(self.widgets[k], tuple):
                sl, inp, _, _, _, step = self.widgets[k]
                sl.set(v)
                self._set_entry(inp, v, step)
        if "font_family" in preset:
            disp = next((k for k, v in FONT_MAP.items() if v == preset["font_family"]), "宋体")
            self.widgets["font_family"].set(disp)
        if SHAPE_MAP.get(self.widgets["seal_shape"].get()) == "ellipse" and name:
            w = self.widgets.get("seal_type")
            if w is not None:
                w.delete(0, tk.END)
                w.insert(0, name)
        self._update_visibility()
        self._delay_change()

    def _update_visibility(self):
        is_circle = SHAPE_MAP.get(self.widgets["seal_shape"].get(), "circle") == "circle"
        for f in self.group_frames.values():
            f.pack_forget()
        self.group_frames["circle" if is_circle else "ellipse"].pack(fill=tk.X)
        self.group_frames["common"].pack(fill=tk.X)

    def _apply_arc_range(self, shape):
        """按章型切换"公司名弧度"滑块量程。

        椭圆章：量程收窄到 150~260°（名称环排的合理区间），使滑块全程有效；
        圆形章：沿用 180~360°（整圈环排）。原先两型共用 180~360°，
        椭圆章实际只用到 180~210°，滑块大半行程看着"没反应"。
        """
        w = self.widgets.get("company_arc")
        if not isinstance(w, tuple):
            return
        lo, hi = (ARC_SPAN_MIN, ARC_SPAN_MAX) if shape == "ellipse" else (180.0, 360.0)
        sl, inp, unit, cur_lo, cur_hi, step = w
        if (cur_lo, cur_hi) != (lo, hi):
            sl.configure(from_=lo, to=hi)
            self.widgets["company_arc"] = (sl, inp, unit, lo, hi, step)
        try:
            val = float(sl.get())
        except Exception:
            val = lo
        val = max(lo, min(hi, val))
        sl.set(val)
        self._set_entry(inp, val, step)

    def sync_params(self, params):
        """从 UI 读取参数到字典"""
        for cfg in UI_ITEMS:
            key = cfg[3]
            w = self.widgets.get(key)
            if w is None:
                continue
            if cfg[1] == "entry":
                params[key] = w.get()
            elif isinstance(w, tuple):
                try:
                    params[key] = max(w[3], min(w[4], float(w[0].get())))
                except Exception:
                    pass
        params["enable_simulation"] = "selected" in self.widgets["enable_simulation"].state()
        params["seal_color"] = COLORS.get(self.widgets["seal_color_combo"].get(), "red")
        params["font_family"] = FONT_MAP.get(self.widgets["font_family"].get(), "simsun.ttc")
        params["seal_shape"] = SHAPE_MAP.get(self.widgets["seal_shape"].get(), "circle")

        # 同步预置中未暴露为 UI 控件的参数（如椭圆章宽度缩放系数）
        presets = ELLIPSE_PRESETS if params["seal_shape"] == "ellipse" else CIRCLE_PRESETS
        preset = presets.get(self.widgets["circle_subtype"].get(), {})
        ui_keys = {cfg[3] for cfg in UI_ITEMS}
        for k, v in preset.items():
            if k not in ui_keys and k != "font_family":
                params[k] = v

    def reset_ui(self, params):
        self._apply_arc_range(SHAPE_MAP.get("圆形章", "circle"))
        for key, w in self.widgets.items():
            if key == "enable_simulation":
                w.state(["selected"] if params[key] else ["!selected"])
            elif key == "seal_shape":
                w.set("圆形章")
            elif key == "seal_color_combo":
                w.set(next((k for k, v in COLORS.items() if v == params["seal_color"]), "红色"))
            elif key == "font_family":
                w.set(next((k for k, v in FONT_MAP.items() if v == params["font_family"]), "宋体"))
            elif key == "circle_subtype":
                w["values"] = list(CIRCLE_PRESETS.keys())
                w.set("国有企业/股份公司")
            elif isinstance(w, ttk.Entry):
                w.delete(0, tk.END)
                w.insert(0, params.get(key, ""))
            elif isinstance(w, tuple):
                sl, inp, _, minv, maxv, step = w
                val = max(minv, min(maxv, params.get(key, minv)))
                sl.set(val)
                self._set_entry(inp, val, step)
        self._update_visibility()
        self._set_sim_state(bool(params.get("enable_simulation", True)))

    def update_preview(self, img):
        if img is None:
            return
        try:
            disp = img.copy()
            disp.thumbnail((340, 340), LANCZOS)
            self.tk_img = ImageTk.PhotoImage(disp)
            self.preview_label.config(image=self.tk_img, text="", font=("微软雅黑", 10))
        except Exception:
            traceback.print_exc()

    def show_render_error(self):
        try:
            self.preview_label.config(image="", text="渲染出错，请检查参数后重试\n（详见终端日志）",
                                      fg=SUBTLE, font=("微软雅黑", 10))
            self.preview_info.config(text="")
        except Exception:
            pass

    def set_preview_info(self, text):
        try:
            self.preview_info.config(text=text)
        except Exception:
            pass

    def set_status(self, left, right):
        try:
            self.status_left.config(text=left)
            self.status_right.config(text=right)
        except Exception:
            pass


# ─────────────────────────── 主应用 ───────────────────────────
class SealMakerApp:
    def __init__(self, root):
        self.root = root
        root.title("标准电子印章生成工具")
        self._apply_window_size(root)
        self.params = dict(DEFAULT_PARAMS)
        self.engine = SealEngine(dpi=20)
        self.preview_img = None
        self._render_key = None
        self._expire_notified = False          # 过期提示每次会话只弹一次（防对话框刷屏）
        self.ui = SealUI(root, self.render, self.download, self.reset_defaults)
        self.ui.reset_ui(self.params)
        self._set_window_icon()
        if _is_expired():
            self._show_expire_notice()
        else:
            self.render()

    @staticmethod
    def _apply_window_size(root):
        """默认窗口 1800×880；小屏（小于该尺寸）自动收缩到屏幕内，避免窗口超出屏幕无法操作"""
        try:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        except Exception:
            sw, sh = 1440, 900
        w = min(1800, max(1180, sw - 80))
        h = min(880, max(680, sh - 140))
        root.geometry(f"{int(w)}x{int(h)}")
        root.minsize(1160, 660)

    def _set_window_icon(self):
        try:
            # 图标仅 64px，无需仿真渲染，节省约 200ms 启动时间
            icon_params = dict(self.params, enable_simulation=False)
            icon = self.engine.render(icon_params)
            icon.thumbnail((64, 64), LANCZOS)
            self._icon = ImageTk.PhotoImage(icon)
            self.root.iconphoto(True, self._icon)
        except Exception:
            pass

    def _show_expire_notice(self):
        # render() 是"参数变更即回调"的热路径，过期后若每次都弹模态框会严重刷屏，
        # 因此本次会话仅提示一次，其后静默拦截。
        if getattr(self, "_expire_notified", False):
            return
        self._expire_notified = True
        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  本软件已达使用截止期限\n"
            f"  截止日期：{EXPIRE_DATE.strftime('%Y 年 %m 月 %d 日')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "继续使用或获取最新版本，请联系：\n"
            "📧  1178258589@qq.com\n\n"
            "感谢您的使用！"
        )
        messagebox.showwarning("软件已过期", msg)

    def _shape_label(self):
        return "圆形章" if self.params.get("seal_shape") == "circle" else "椭圆章"

    def _param_key(self):
        """当前参数指纹，用于判断预览图是否可复用"""
        return tuple(sorted((k, str(v)) for k, v in self.params.items()))

    def _update_status(self, ms, img):
        if img is None:
            self.ui.set_status("渲染失败", "请检查参数设置")
            return
        if self.params.get("seal_shape") == "circle":
            dim = f"{self.params['circle_diameter']:.1f} mm"
        else:
            dim = f"{self.params['axis_a']:.1f} × {self.params['axis_b']:.1f} mm"
        if ms is None:
            right = f"{self._shape_label()} · {dim} · {img.width}×{img.height}px"
        else:
            right = f"{self._shape_label()} · {dim} · {img.width}×{img.height}px · 渲染 {ms:.0f}ms"
        self.ui.set_status("就绪", right)
        self.ui.set_preview_info(f"{self._shape_label()} · {img.width}×{img.height}px · 约 508 DPI")

    def render(self):
        if _is_expired():
            self._show_expire_notice()
            return
        t0 = time.perf_counter()
        try:
            self.ui.sync_params(self.params)
            self.preview_img = self.engine.render(self.params)
            self._render_key = self._param_key()
            self.ui.update_preview(self.preview_img)
            self._update_status((time.perf_counter() - t0) * 1000, self.preview_img)
        except Exception:
            traceback.print_exc()
            self.ui.show_render_error()
            self._update_status(-1, None)

    def download(self):
        if _is_expired():
            self._show_expire_notice()
            return
        if self.preview_img is None:
            messagebox.showwarning("提示", "无预览图")
            return
        if not messagebox.askyesno("法律免责", "仅供学习参考，请勿用于非法用途！\n\n确认继续下载？"):
            return

        # 同步最新参数；若与当前预览一致则直接复用（秒存），否则重新渲染
        self.ui.sync_params(self.params)
        key = self._param_key()
        reuse = (self._render_key == key and self.preview_img is not None)

        if not reuse:
            # 显示等待提示框（非阻塞，强制刷新界面）
            wait_win = tk.Toplevel(self.root)
            wait_win.title("生成中")
            wait_win.configure(bg=CARD)
            wait_win.transient(self.root)
            wait_win.grab_set()
            wait_win.resizable(False, False)
            tk.Label(wait_win, text="参数已变化，正在重新渲染…",
                     font=("微软雅黑", 12, "bold"), bg=CARD, fg=TEXT,
                     padx=40, pady=(24, 4)).pack()
            tk.Label(wait_win, text="通常在 1 秒内完成",
                     font=("微软雅黑", 9), bg=CARD, fg=SUBTLE,
                     pady=(0, 24)).pack()
            self.root.update()
            t0 = time.perf_counter()
            try:
                self.preview_img = self.engine.render(self.params)
                self._render_key = key
                self.ui.update_preview(self.preview_img)
                self._update_status((time.perf_counter() - t0) * 1000, self.preview_img)
            except Exception as e:
                traceback.print_exc()
                messagebox.showerror("导出失败", f"高清渲染出错：{e}")
                return
            finally:
                wait_win.destroy()

        hd_img = self.preview_img
        # 保存文件（文件名做长度限制，避免路径过长导致保存失败）
        name = re.sub(r'[\\/*?:"<>|]', "", self.params["company_name"]).strip() or "印章"
        name = name[:60]
        path = filedialog.asksaveasfilename(initialfile=name, defaultextension=".png",
                                            filetypes=[("PNG印章", "*.png")], title="保存印章")
        if not path:
            return
        try:
            # 写入物理分辨率信息（约 508 DPI）：Word/WPS 插入后即为真实毫米尺寸
            hd_img.save(path, "PNG", dpi=(EXPORT_DPI, EXPORT_DPI))
            messagebox.showinfo(
                "导出成功",
                f"印章已保存！\n分辨率：{hd_img.width} x {hd_img.height} 像素（约 508 DPI）\n"
                f"插入 Word / WPS 即为真实毫米尺寸")
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("保存失败", f"保存文件出错：{e}")

    def reset_defaults(self):
        if _is_expired():
            self._show_expire_notice()
            return
        self.params = dict(DEFAULT_PARAMS)
        self.ui.reset_ui(self.params)
        self.render()


def _enable_dpi_awareness():
    """Windows 高 DPI 适配，避免高分屏下界面与文字发虚"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    if not _DEPS_OK:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "缺少依赖",
            "未检测到 Pillow / numpy，程序无法启动。\n\n"
            "请先安装依赖：\n  pip install pillow numpy",
        )
        root.destroy()
        return
    _enable_dpi_awareness()
    root = tk.Tk()
    app = SealMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            err_root = tk.Tk()
            err_root.withdraw()
            messagebox.showerror("启动失败",
                                 "程序初始化出错，请查看终端输出或联系开发者。")
            err_root.destroy()
        except Exception:
            pass
