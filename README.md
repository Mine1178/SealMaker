<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&height=180&section=header&text=SealMaker&fontSize=48&fontColor=fff&animation=fadeIn" width="100%"/>

<h3>🔴 标准电子印章生成工具</h3>

<p>
  <b>圆形章 / 椭圆发票章 · 毫米级精度 · 508 DPI 导出 · 直接插入 Word/WPS</b>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/GUI-Tkinter-0078D4?logo=windows&logoColor=white" alt="Tkinter">
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/Export-508%20DPI-red" alt="DPI">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Version-v1.3.0-orange" alt="Version">
</p>

<img src="images/main-window.png" alt="软件主界面" width="850"/>

</div>

---

## 📑 目录

- [✨ 功能特性](#-功能特性)
- [📸 效果展示](#-效果展示)
- [🚀 快速开始](#-快速开始)
- [🎯 使用流程](#-使用流程)
- [🛠️ 技术栈](#️-技术栈)
- [🗺️ Roadmap](#️-roadmap)
- [⚠️ 免责声明](#️-免责声明)
- [☕ 支持我](#-支持我)

---

## ✨ 功能特性

<div align="center">

| 模块 | 能力 |
|------|------|
| 🔴 **双章型** | 圆形公司章、椭圆发票专用章 |
| 📋 **6 套预置模板** | 国企 / 有限公司 / 个体 / 社团 / 行政机关 / 发票章 |
| 📏 **税务规范** | 按国税总局 2011 年第 7 号公告排版（环排 220°、税号位置、仿宋字体） |
| 📐 **毫米级精度** | 导出 PNG 写入 508 DPI，Word/WPS 插入即真实尺寸 |
| 🎨 **仿真盖章** | 噪点 / 模糊 / 外圈断纹三滑块，还原印泥缺角糊边质感 |
| 🧩 **自适应排版** | 长公司名自动缩字、字符旋转阻尼，杜绝重叠与首尾倒立 |
| 🔒 **纯本地** | 不上传任何数据，离线可用 |

</div>

---

## 📸 效果展示

<div align="center">

| 圆形合同专用章 | 椭圆发票专用章 |
|:---:|:---:|
| <img src="images/circle-seal.png" width="400"> | <img src="images/ellipse-invoice.png" width="400"> |

</div>

---

## 🚀 快速开始

### 方式一：下载安装包（推荐）

前往 [Releases](https://github.com/Mine1178/SealMaker/releases/latest) 下载最新版安装包，双击安装即可使用，无需配置 Python 环境。

### 环境要求
- **OS**：Windows 10 / 11
- **Python**：3.10 及以上（仅源码运行需要）
- **依赖**：`Pillow`、`numpy`

### 方式二：从源码运行

```bash
# 1. 克隆仓库
git clone https://github.com/Mine1178/SealMaker.git
cd SealMaker

# 2. 安装依赖
pip install pillow numpy

# 3. 启动
python SealMaker_v1.3.0.py
```

---

## 🎯 使用流程

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  ① 选章型    │ → │ ② 填基础信息 │ → │ ③ 调样式参数 │ → │ ④ 下载 PNG   │
│  圆形/椭圆   │   │ 公司名/备案号 │   │ 直径/字号/弧角 │   │ 透明背景    │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

1. **选章型**：圆形章 或 椭圆发票章
2. **填信息**：公司名称、备案号、章号
3. **调参数**：直径、五角星半径、字号、环排角度、边框宽度
4. **下载**：右侧实时预览，满意后点「下载印章（透明背景 PNG）」

---

## 🛠️ 技术栈

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Tkinter-GUI-0078D4?logo=windows&logoColor=white">
  <img src="https://img.shields.io/badge/Pillow-Image-3776AB?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/NumPy-Data-013243?logo=numpy&logoColor=white">
  <img src="https://img.shields.io/badge/PyInstaller-Build-F04E23?logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Nuitka-Compile-4A90D9?logoColor=white">
  <img src="https://img.shields.io/badge/Inno%20Setup-Installer-0078D4">
</p>

**核心算法**：自定义弧排引擎、旋转矩形 SAT 碰撞检测、印泥噪点合成、字符旋转阻尼限幅。

---

## 🗺️ Roadmap

- [x] v1.3.0 — 椭圆章弧排引擎重写，修复字符重叠/压边/首尾倒立
- [ ] v1.4.0 — 支持自定义章面底色、多色章
- [ ] v1.5.0 — 批量生成（Excel 名单一键出章）
- [ ] v1.6.0 — 方形章 / 菱形章支持

---

## ⚠️ 免责声明

> 本软件**仅供学习、设计演示、教学课件、手账素材等合法用途**。
>
> - 真实公章 / 财务章 / 发票章须到 **公安机关备案点**刻制
> - 严禁用于伪造、变造公司印章或任何违法活动
> - 使用本软件产生的一切后果由使用者自行承担

---

## ☕ 支持我

如果这个工具帮到了你，欢迎请我喝杯咖啡 ☕

<div align="center">
  <img src="sponsors/wechatpay.jpg" width="280" alt="微信赞赏">
  <img src="sponsors/alipay.jpg" width="280" alt="支付宝赞赏">
</div>

---

<div align="center">

📄 **License**：[MIT](LICENSE) ｜ ⭐ 如果喜欢，请给个 Star 鼓励一下！

</div>
