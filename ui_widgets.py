# -*- coding: utf-8 -*-
"""SealMaker 共享 UI 构件（设计页与签章页共用，消除重复实现）。

区域划分原则（与常规软件一致）：
  * 可选中「正文内容」：状态栏信息、印章规格、参数摘要、说明文字、
    空状态提示、文件列表行，以及所有输入框。
  * 不可选中「控件文字」：按钮文字、卡片与分区标题、字段名 / 单位 /
    选项名、单选复选文字、品牌与导航。

本模块只依赖 tkinter，可被主程序与 pdf_stamper 独立导入。
"""

import tkinter as tk
import tkinter.font as tkfont

_DEFAULT_BG = "#ffffff"
_DEFAULT_FG = "#1f2937"


class SelectableLabel(tk.Entry):
    """无痕只读文本框：外观等同 tk.Label，支持左键拖选与复制。

    仅用于正文内容文字；按钮文字一律不使用本组件。
    文本光标 xterm 与按钮手型 hand2 形成可见的分区提示。
    """

    on_copy = None                 # 由宿主注入 (left, right) 状态栏回调

    def __init__(self, parent, text="", bg=None, fg=None, font=None,
                 anchor="w", **kw):
        super().__init__(parent, relief="flat", bd=0, highlightthickness=0,
                         background=bg or _DEFAULT_BG,
                         readonlybackground=bg or _DEFAULT_BG,
                         fg=fg or _DEFAULT_FG, font=font or ("微软雅黑", 9),
                         justify="left" if anchor == "w" else "center",
                         takefocus=0, cursor="xterm", **kw)
        # 字体度量缓存：Entry 的 width 单位是「0」字宽，中文约 2 倍宽，
        # 按字符数估宽会把中文截断，必须用实测像素换算
        self._mfont = tkfont.Font(font=self.cget("font"))
        self._mzero = max(1, self._mfont.measure("0"))
        self._set(text)
        self.bind("<Button-3>", self._on_right)

    def _set(self, text):
        text = str(text)
        self.configure(state="normal")
        self.delete(0, tk.END)
        self.insert(0, text)
        # width 随内容自适应（像素实测换算），避免 pack 在不 fill 时截断文字
        units = round(self._mfont.measure(text) / self._mzero) + 3
        self.configure(state="readonly", width=max(8, units))

    def set_text(self, text):
        """运行时刷新文字（状态栏 / 信息条的动态入口）。"""
        self._set(text)

    def copy_selection(self):
        """复制选区；未选中时复制整段文字，并在状态栏回显。"""
        try:
            text = self.selection_get() if self.selection_present() else self.get()
            top = self.winfo_toplevel()
            top.clipboard_clear()
            top.clipboard_append(text)
            top.update_idletasks()
            cb = type(self).on_copy
            if cb:
                cb("已复制文字", text[:36])
        except Exception:
            pass

    def select_all(self):
        self.focus_set()
        self.select_range(0, tk.END)

    def _on_right(self, e):
        menu = tk.Menu(self, tearoff=0, font=("微软雅黑", 9))
        menu.add_command(label="复制", command=self.copy_selection)
        menu.add_command(label="全选", command=self.select_all)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"


def install_list_copy(listbox, on_copy=None):
    """文件列表：左键选中行即选区，Ctrl+C / 右键「复制」复制该行文字。

    这是控件级绑定，只在列表内生效，不是全局右键。
    """
    def _copy(_e=None):
        sel = listbox.curselection()
        if not sel:
            return "break"
        text = "\n".join(str(listbox.get(i)) for i in sel)
        top = listbox.winfo_toplevel()
        top.clipboard_clear()
        top.clipboard_append(text)
        top.update_idletasks()
        if on_copy:
            on_copy("已复制文字", text[:36])
        return "break"

    def _right(e):
        if not listbox.curselection():
            return
        menu = tk.Menu(listbox, tearoff=0, font=("微软雅黑", 9))
        menu.add_command(label="复制", command=_copy)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()
        return "break"

    listbox.bind("<Control-Key-c>", _copy)
    listbox.bind("<Button-3>", _right)


# ─────────────────── 大号勾选 / 单选控件 ───────────────────
# 默认主题的复选框 / 单选圆点指示器仅约 13px，误触率高；本组控件自绘
# 约 1.8 倍大的指示器（23px 圆角样式）+ 整行手型点击热区，兼容 ttk 用法：
#   * state(["selected" | "!selected" | "disabled" | "!disabled"]) 设置
#   * state() 返回字符串供 "selected" in w.state() 查询
#   * variable 双向同步，同组单选自动互斥刷新
class BigToggleBase(tk.Canvas):
    """自绘大号指示器基类（Canvas 实现，整行可点）。"""

    BOX, HIT, PAD = 23, 30, 8         # 指示器 23px（38px 的 60%，即缩小 40%）
    ACCENT, BORDER, DISABLED = "#c0392b", "#c4cad3", "#d5d9df"
    HOVER = "#8a93a0"

    def __init__(self, parent, text="", bg=None, fg=None, font=None,
                 command=None, **kw):
        self._text = text
        self._bg = bg or _DEFAULT_BG
        self._fg = fg or _DEFAULT_FG
        self._font = font or ("微软雅黑", 10)
        self._command = command
        self._enabled = True
        self._hovered = False
        self._var = None
        self._trace = None
        super().__init__(parent, bg=self._bg, highlightthickness=0, **kw)
        self._tkfont = tkfont.Font(font=self._font)
        self.configure(width=self.BOX + self.PAD + self._tkfont.measure(text) + 8,
                       height=self.HIT, cursor="hand2")
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<Destroy>", self._on_destroy)
        self._redraw()

    # ── ttk 兼容层 ──
    def state(self, spec=None):
        if spec:
            for flag in spec:
                if flag == "selected":
                    self.set_checked(True)
                elif flag == "!selected":
                    self.set_checked(False)
                elif flag == "disabled":
                    self._enabled = False
                elif flag == "!disabled":
                    self._enabled = True
            self.configure(cursor="hand2" if self._enabled else "arrow")
            self._redraw()
            return None
        parts = []
        if self.is_checked():
            parts.append("selected")
        if not self._enabled:
            parts.append("disabled")
        return " ".join(parts)

    def is_checked(self):
        raise NotImplementedError

    def set_checked(self, value, run_command=False):
        raise NotImplementedError

    def _toggle(self):
        raise NotImplementedError

    # ── 内部 ──
    def _attach_var(self, variable):
        self._var = variable
        self._trace = variable.trace_add("write", self._var_changed)

    def _var_changed(self, *_):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._redraw()

    def _on_click(self, _e):
        if self._enabled:
            self._toggle()
        return "break"

    def _hover(self, on):
        self._hovered = on
        self._redraw()

    def _on_destroy(self, _e=None):
        if self._var is not None and self._trace:
            try:
                self._var.trace_remove("write", self._trace)
            except Exception:
                pass
            self._trace = None

    def _redraw(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.delete("all")
        on = self.is_checked()
        if self._enabled:
            if on:
                edge = mark = self.ACCENT
            else:
                edge = self.HOVER if self._hovered else self.BORDER
                mark = self.ACCENT
            tcol = self._fg
        else:
            edge = mark = self.DISABLED
            tcol = "#b7bdc6"
        self._paint_indicator(1, (self.HIT - self.BOX) // 2, self.BOX, on, edge, mark)
        if self._text:
            self.create_text(self.BOX + self.PAD, self.HIT // 2, text=self._text,
                             font=self._font, fill=tcol, anchor="w")

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        """圆角矩形（polygon smooth 拟合），r 为圆角半径。"""
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
        return self.create_polygon(pts, smooth=True, **kw)

    def _paint_indicator(self, x0, y0, box, on, edge, mark):
        raise NotImplementedError


class BigCheckbutton(BigToggleBase):
    """约 1.8 倍大的自绘复选框：选中为印章红圆角方块 + 白勾，
    支持 variable / command / state()。"""

    def __init__(self, parent, text="", variable=None, onvalue=True, offvalue=False,
                 checked=False, **kw):
        self._onvalue, self._offvalue = onvalue, offvalue
        self._checked = bool(checked)
        super().__init__(parent, text=text, **kw)
        if variable is not None:
            self._attach_var(variable)
            try:
                self._checked = self._var.get() == self._onvalue
            except Exception:
                self._checked = False
            self._redraw()

    def _var_changed(self, *_):
        """外部程序化修改变量时回读并刷新（variable → 控件方向同步）。"""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            self._checked = self._var.get() == self._onvalue
        except Exception:
            pass
        self._redraw()

    def is_checked(self):
        return self._checked

    def set_checked(self, value, run_command=False):
        self._checked = bool(value)
        if self._var is not None:
            self._var.set(self._onvalue if self._checked else self._offvalue)
        self._redraw()
        if run_command and self._command:
            self._command()

    def _toggle(self):
        self.set_checked(not self._checked, run_command=True)

    def _paint_indicator(self, x0, y0, box, on, edge, mark):
        r = max(4, box * 0.26)
        if on:
            fill = mark if self._enabled else self.DISABLED
            self._round_rect(x0, y0, x0 + box, y0 + box, r,
                             outline=fill, width=2, fill=fill)
            # 直段两笔勾（不用 smooth，避免曲线化看不出「✓」形）
            self.create_line(x0 + box * 0.20, y0 + box * 0.54,
                             x0 + box * 0.38, y0 + box * 0.72,
                             x0 + box * 0.80, y0 + box * 0.26,
                             fill="#ffffff", width=3,
                             capstyle="round", joinstyle="round")
        else:
            fill = "#fdf4f3" if (self._hovered and self._enabled) else "#ffffff"
            self._round_rect(x0, y0, x0 + box, y0 + box, r,
                             outline=edge, width=2, fill=fill)


class BigRadiobutton(BigToggleBase):
    """约 1.8 倍大的自绘单选圆点：选中为红色圆环 + 实心圆点，
    同组共享 variable，自动互斥刷新。"""

    def __init__(self, parent, text="", variable=None, value=None, **kw):
        self._value = value
        super().__init__(parent, text=text, **kw)
        if variable is not None:
            self._attach_var(variable)
            self._redraw()

    def is_checked(self):
        try:
            return self._var is not None and self._var.get() == self._value
        except Exception:
            return False

    def set_checked(self, value, run_command=False):
        if value and self._var is not None:
            self._var.set(self._value)
            if run_command and self._command:
                self._command()
        self._redraw()

    def _toggle(self):
        if self._var is None or self.is_checked():
            return                      # 已选中项再点不重复触发 command
        self.set_checked(True, run_command=True)

    def _paint_indicator(self, x0, y0, box, on, edge, mark):
        if on:
            ring = mark if self._enabled else self.DISABLED
            self.create_oval(x0, y0, x0 + box, y0 + box,
                             outline=ring, width=2, fill="#ffffff")
            inset = box * 0.30
            self.create_oval(x0 + inset, y0 + inset, x0 + box - inset,
                             y0 + box - inset, fill=ring, outline="")
        else:
            fill = "#fdf4f3" if (self._hovered and self._enabled) else "#ffffff"
            self.create_oval(x0, y0, x0 + box, y0 + box,
                             outline=edge, width=2, fill=fill)
