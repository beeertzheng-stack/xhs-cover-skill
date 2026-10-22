#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书美食封面合成脚本。

职责：把博主照片处理成 3:4 竖版画布，并按版式/风格确定性渲染标题文字层，
保证文字全部落在安全区内。标题文字一律由本脚本用开源字体绘制，禁止让 AI 生图写字。

v2 新增「贴纸人气风 sticker」：参照爆款美食封面——
  圆头手写大字（站酷快乐体）双色 + 粗白描边 + 交错倾斜跳排；
  黄油色胶带笔刷副标题；红色对话气泡角标；
  人物透明抠图带白色贴纸描边压在菜品上；
  菜品实拍大图做背景（顶部渐变虚化承载文字）；
  黄色放射短线 + 小爱心涂鸦点缀。

用法示例：
  python3 compose_cover.py \
      --photo 菜品照片.jpg --person 透明抠图.png --title "番茄肥牛拌面" \
      --subtitle "10分钟搞定" --badge "快手美食" --highlight "肥牛" \
      --style sticker --out 封面.png

所有输出遵循 references/spec.md 的硬性规范；文字框坐标会写进 report JSON 供校验。
"""

import argparse
import json
import math
import os
import re
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

BASE_W = 1080.0          # 规范里的字号基准宽度
SAFE_MARGIN_X = 80       # 文字距左右边缘（基准 1080 宽）
SAFE_TOP = 150           # 文字距顶部
SAFE_BOTTOM = 200        # 文字距底部
MAX_FILE_MB = 5.0

# 主标题字号区间（基准 1080）：sticker 风用大标题区间，其余风格用常规区间
TITLE_SIZE_RANGE = (60, 80)
TITLE_SIZE_RANGE_STICKER = (96, 132)
SUB_SIZE_RANGE = (32, 44)
BADGE_SIZE_RANGE = (24, 32)
MAX_TITLE_CHARS = 12
MAX_LINES = 2
MAX_PER_LINE = 8
MAX_TOTAL_CHARS = 20   # 标题+副标题+角标合计

# 需要单独放大换色的“钩子”要素：数字/价格/时长，以及情绪词
NUM_RE = re.compile(
    r"\d+(?:\.\d+)?(?:元|块|米|步|分钟|小时|天|周|个月|人|款|家|折|次|斤|g|kg|ml|寸|℃|倍|年)?"
)
EMO_WORDS = [
    "绝了", "避雷", "踩雷", "后悔", "惊了", "香哭", "好吃哭", "封神", "yyds",
    "宝藏", "私藏", "天花板", "上头", "离谱", "真香", "哭死", "血亏", "白吃",
    "第一次", "终于", "别再", "千万别", "本地人", "老顾客", "排队", "翻车", "成功",
    "巨好吃", "爆浆", "网红", "零失败", "新手", "快手",
]

FONT_DIRS = [
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/usr/share/fonts",
]

# 每套风格对应字体角色；值是按优先级排列的文件名片段（可含或不含扩展名）
STYLE_FONTS = {
    "sticker": {"title": ["ZhanKuKuaiLeTi", "站酷快乐", "HYCuYuanJ", "AlibabaPuHuiTi-3-95-ExtraBold"],
                "sub": ["HYCuYuanJ", "ZhanKuKuaiLeTi", "AlibabaPuHuiTi-3-85-Bold"],
                "badge": ["ZhanKuKuaiLeTi", "站酷快乐", "HYCuYuanJ"]},
    "bright": {"title": ["AlibabaPuHuiTi-3-95-ExtraBold", "站酷高端黑"],
               "sub": ["AlibabaPuHuiTi-3-75-SemiBold", "思源黑体-Medium"],
               "badge": ["AlibabaPuHuiTi-3-85-Bold"]},
    "cream": {"title": ["AlibabaPuHuiTi-3-85-Bold", "思源黑体-Bold"],
              "sub": ["思源黑体-Regular", "AlibabaPuHuiTi-3-55-Regular"],
              "badge": ["思源黑体-Medium"]},
    "street": {"title": ["站酷酷黑体", "HYChaoJiZhanJiaJ", "ZhanKuKuaiLeTi"],
               "sub": ["ZhanKuWenYiTi", "站酷文艺体", "思源黑体-Regular"],
               "badge": ["站酷酷黑体", "AlibabaPuHuiTi-3-85-Bold"]},
    "dark": {"title": ["思源宋体-SemiBold", "思源宋体-Bold", "HYChaoCuSongJ"],
             "sub": ["思源宋体-Regular", "思源宋体-Light"],
             "badge": ["思源宋体-Medium"]},
}

FALLBACK_FONTS = ["Arial Unicode", "PingFang", "Hiragino Sans GB", "STHeiti Medium"]

# 风格预设：文字色、强调色、色块、蒙层、校色强度
STYLES = {
    "sticker": {
        "name": "贴纸人气风",
        "title_color": "#4A2A16", "accent": "#D9401E", "accent2": "#E8890C",
        "panel": "#E0492F", "panel_text": "#FFFFFF",
        "tape": "#F7D14A", "tape_text": "#4A2A16",
        "outline": "#FFFFFF", "shadow": (120, 70, 30, 90),
        "scrim": (255, 248, 236, 120), "bg_lift": 1.08, "bg_warm": 1.06,
        "sat": 1.16, "contrast": 1.05, "bright_en": 1.05,
        "band": "none", "text_align": "center",
    },
    "bright": {
        "name": "食欲明亮风",
        "title_color": "#FFFFFF", "accent": "#FFE04A", "accent2": "#FF5A2B",
        "panel": "#E8532B", "panel_text": "#FFFFFF",
        "scrim": (58, 28, 12, 130), "bg_lift": 1.06, "bg_warm": 1.10,
        "sat": 1.20, "contrast": 1.10, "bright_en": 1.08,
        "band": "scrim", "text_align": "center",
    },
    "cream": {
        "name": "奶油治愈风",
        "title_color": "#463525", "accent": "#C9762F", "accent2": "#C9762F",
        "panel": "#FFF6E8", "panel_text": "#463525",
        "scrim": (255, 246, 232, 150), "bg_lift": 1.16, "bg_warm": 1.02,
        "sat": 1.06, "contrast": 1.02, "bright_en": 1.10,
        "band": "panel", "text_align": "center",
    },
    "street": {
        "name": "市井烟火风",
        "title_color": "#FFF6DC", "accent": "#FF4D4D", "accent2": "#2BE5D1",
        "panel": "#1A1A1A", "panel_text": "#FFE9A8",
        "scrim": (26, 20, 16, 150), "bg_lift": 0.94, "bg_warm": 1.14,
        "sat": 1.26, "contrast": 1.16, "bright_en": 0.98,
        "band": "scrim", "text_align": "center",
    },
    "dark": {
        "name": "高级暗调风",
        "title_color": "#F4EFE7", "accent": "#C9A227", "accent2": "#C9A227",
        "panel": "#14100C", "panel_text": "#F4EFE7",
        "scrim": (12, 10, 8, 165), "bg_lift": 0.62, "bg_warm": 0.98,
        "sat": 1.04, "contrast": 1.20, "bright_en": 0.86,
        "band": "none", "text_align": "center",
    },
}

# 版式：主体框（相对画布）+ 标题带 + 副文案带
LAYOUTS = {
    "sticker3": {
        "name": "贴纸人菜版",
        "subject": (0.0, 0.0, 1.0, 1.0),
        "title_band": "top", "sub_band": "tape", "fy": 0.45,
        "note": "菜品大图铺满、人物抠图贴纸压左下、大标题跳排置顶。菜谱/小吃/烘焙/快手菜首选。",
    },
    "person": {
        "name": "人物主体式",
        "subject": (0.05, 0.34, 0.95, 1.00),
        "title_band": "top", "sub_band": None, "fy": 0.12,
        "note": "博主半身/特写占画面主体，标题走上方留白区。人设向内容。",
    },
    "food": {
        "name": "菜品大特写式",
        "subject": (0.10, 0.30, 0.90, 0.97),
        "title_band": "top", "sub_band": None, "fy": 0.5,
        "note": "菜品位于视觉中心且占 60% 以上，标题走顶部。单品种草/菜谱成品。",
    },
    "three": {
        "name": "三分递进式",
        "subject": (0.07, 0.31, 0.93, 0.74),
        "title_band": "top", "sub_band": "bottom", "fy": 0.3,
        "note": "上标题、中主体、下一句悬念或补充。探店、清单、测评。",
    },
    "compare": {
        "name": "对比拼贴式",
        "subject": (0.0, 0.0, 1.0, 0.46),
        "subject2": (0.0, 0.54, 1.0, 1.0),
        "title_band": "middle", "sub_band": None, "fy": 0.12, "fy2": 0.12,
        "note": "上下二分做前后/高低对比，主标题压在中间色带上。对比类、合集类。",
    },
}

ALLOWED_SIZES = [(1242, 1656), (1080, 1440), (1104, 1472), (1140, 1520)]


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


_INDEX = None


def font_index():
    global _INDEX
    if _INDEX is None:
        files = []
        for d in FONT_DIRS:
            if os.path.isdir(d):
                for name in sorted(os.listdir(d)):
                    if name.lower().endswith((".ttf", ".otf", ".ttc")):
                        files.append(os.path.join(d, name))
        _INDEX = files
    return _INDEX


def find_font(fragments):
    idx = font_index()
    for frag in fragments:
        key = frag.lower()
        for p in idx:
            base = os.path.splitext(os.path.basename(p))[0].lower()
            if key in base:
                try:
                    ImageFont.truetype(p, 20)
                except Exception:
                    continue
                return p
    return None


def resolve_fonts(style_key, overrides=None):
    overrides = overrides or {}
    spec = STYLE_FONTS[style_key]
    out = {}
    for role, frags in spec.items():
        chosen = overrides.get(role)
        if chosen:
            p = chosen if os.path.isabs(chosen) else find_font([chosen])
            if not p:
                die("找不到指定字体：%s" % chosen)
            out[role] = p
            continue
        p = find_font(frags)
        if not p:
            p = find_font(FALLBACK_FONTS)
        if not p:
            die("本机没有可用的中文可商用字体，请先安装阿里巴巴普惠体 3 或站酷快乐体。")
        out[role] = p
    return out


def load_photo(path):
    if not os.path.isfile(path):
        die("照片不存在：%s" % path)
    try:
        im = Image.open(path)
        im.load()
    except Exception as e:
        die("照片无法打开：%r" % e)
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    if im.width < 720:
        print("WARN: 原图宽度仅 %dpx，出图会偏软，建议换 %dpx 以上的照片。" % (im.width, int(BASE_W)))
    return im


def load_cutout(path):
    """读取透明抠图人物，裁到内容包围盒并收边去杂。"""
    if not os.path.isfile(path):
        die("抠图不存在：%s" % path)
    im = Image.open(path)
    if im.mode != "RGBA":
        die("--person 需要带透明通道的 PNG 抠图（RGBA）。")
    im.load()
    a = im.getchannel("A")
    box = a.getbbox()
    if not box:
        die("抠图全透明，请检查图片。")
    im = im.crop(box)
    a = im.getchannel("A")
    a = a.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6))
    im.putalpha(a)
    return im


def cover_crop(im, w, h, fx=0.5, fy=0.5):
    scale = max(w / im.width, h / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    x0 = int(round((nw - w) * max(0.0, min(1.0, fx))))
    y0 = int(round((nh - h) * max(0.0, min(1.0, fy))))
    return im.crop((x0, y0, x0 + w, y0 + h))


def build_background(im, W, H, st, style_key, blur_px=None):
    bg = cover_crop(im, W, H)
    radius = blur_px if blur_px else max(12, int(W / 26))
    bg = bg.filter(ImageFilter.GaussianBlur(radius))
    bg = ImageEnhance.Brightness(bg).enhance(st["bg_lift"])
    bg = ImageEnhance.Color(bg).enhance(st["sat"] * 0.92)
    bg = ImageEnhance.Contrast(bg).enhance(st["contrast"])
    if style_key in ("bright", "street"):
        bg = _warm(bg, 1.06 if style_key == "street" else 1.03)
    if style_key == "dark":
        bg = _warm(bg, 0.96)
    return bg


def build_sticker_background(im, W, H, st):
    """菜品实拍大图铺满，顶部 42% 渐变虚化作为文字承载面，整体提亮加饱和。"""
    bg = cover_crop(im, W, H, fy=0.45)
    bg = ImageEnhance.Brightness(bg).enhance(st["bg_lift"])
    bg = ImageEnhance.Color(bg).enhance(st["sat"])
    bg = ImageEnhance.Contrast(bg).enhance(st["contrast"])
    bg = _warm(bg, st["bg_warm"])
    bg = bg.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    soft = bg.filter(ImageFilter.GaussianBlur(max(18, int(W / 34))))
    grad = Image.new("L", (1, H))
    split = int(H * 0.34)
    fade = int(H * 0.14)
    for y in range(H):
        if y < split:
            v = 255
        elif y < split + fade:
            v = int(255 * (1 - (y - split) / fade))
        else:
            v = 0
        grad.putpixel((0, y), v)
    grad = grad.resize((W, H))
    return Image.composite(soft, bg, grad)


def _warm(im, k):
    r, g, b = im.split()
    r = ImageEnhance.Brightness(r).enhance(k)
    b = ImageEnhance.Brightness(b).enhance(2.0 - k)
    return Image.merge("RGB", (r, g, b))


def paste_feather(base, fg, box, feather_ratio=0.16, v_feather=None):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(base.width, x1); y1 = min(base.height, y1)
    w, h = x1 - x0, y1 - y0
    if w <= 1 or h <= 1:
        return
    fg = fg.resize((w, h), Image.LANCZOS)
    f = int(max(2, min(w, h) * (v_feather if v_feather is not None else feather_ratio)))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rectangle([f, f, w - f, h - f], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(f * 0.9))
    base.paste(fg, (x0, y0), mask)


def solid_bg(W, H, color):
    return Image.new("RGB", (W, H), color)


def enhance_subject(im, st):
    im = ImageEnhance.Brightness(im).enhance(st["bright_en"])
    im = ImageEnhance.Contrast(im).enhance(st["contrast"])
    im = ImageEnhance.Color(im).enhance(st["sat"])
    im = im.filter(ImageFilter.UnsharpMask(radius=max(2, int(BASE_W * 0.012)), percent=90, threshold=3))
    return im


def wrap_title(title, max_lines=2, max_per_line=8):
    t = re.sub(r"\s+", " ", title.strip())
    total = len(t.replace(" ", ""))
    if total > MAX_TITLE_CHARS:
        die("主标题 %d 字，超过 %d 字上限。请压缩到 %d 字以内（可把多余信息放进副标题）。"
            % (total, MAX_TITLE_CHARS, MAX_TITLE_CHARS))
    if total > max_lines * max_per_line:
        die("主标题 %d 字，超出「%d 行 × 每行 %d 字」的排版上限。" % (total, max_lines, max_per_line))
    if total <= max_per_line:
        return [t]
    parts = re.split(r"[，。！？、；：,.!?;:~]| ", t)
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        acc, lines = "", []
        for p in parts:
            if len(acc) + len(p) <= max_per_line:
                acc += p
            else:
                if acc:
                    lines.append(acc)
                acc = p
        if acc:
            lines.append(acc)
        if len(lines) <= max_lines and all(len(l) <= max_per_line for l in lines):
            return lines
    mid = (total + 1) // 2
    plain = t.replace(" ", "")
    return [plain[:mid], plain[mid:]]


def tokenize(line, highlights=()):
    """把一行拆成 [(文本, 是否强调)]，数字/价格/情绪词/指定高亮词标为强调。"""
    marks = []
    for m in NUM_RE.finditer(line):
        marks.append((m.start(), m.end()))
    for w in EMO_WORDS:
        s = 0
        while True:
            i = line.find(w, s)
            if i < 0:
                break
            marks.append((i, i + len(w)))
            s = i + len(w)
    for w in highlights:
        if not w:
            continue
        s = 0
        while True:
            i = line.find(w, s)
            if i < 0:
                break
            marks.append((i, i + len(w)))
            s = i + len(w)
    marks.sort()
    merged = []
    for a, b in marks:
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    toks, pos = [], 0
    for a, b in merged:
        if a > pos:
            toks.append((line[pos:a], False))
        toks.append((line[a:b], True))
        pos = b
    if pos < len(line):
        toks.append((line[pos:], False))
    return toks or [(line, False)]


def fit_font_size(draw, lines, font_path, scale, avail_w, size_range, weight_delta=0):
    hi = int(size_range[1] * scale)
    lo = int(size_range[0] * scale)
    probe = max(lo, hi + weight_delta)
    for s in range(probe, lo - 1, -2):
        f = ImageFont.truetype(font_path, s)
        widest = max(draw.textlength(t, font=f) for line in lines for t, _ in line)
        if widest <= avail_w:
            return f, s
    return ImageFont.truetype(font_path, lo), lo


def draw_text_line(draw, toks, font, x, y, color, accent, scale, align, avail_w,
                   stroke=True, panel=None, panel_text=None, panel_pad=0, radius=0):
    widths = [draw.textlength(t, font=font) for t, _ in toks]
    total = sum(widths)
    if align == "center":
        cur = x + (avail_w - total) / 2.0
    else:
        cur = x
    box0 = [cur, y, cur + total, y + font.size * 1.3]
    for (t, is_acc), w in zip(toks, widths):
        col = accent if is_acc else color
        if stroke:
            draw.text((cur + 3 * scale, y + 4 * scale), t, font=font,
                      fill=(0, 0, 0, 120), stroke_width=0)
            draw.text((cur, y), t, font=font, fill=col,
                      stroke_width=max(1, int(2 * scale)), stroke_fill=(18, 12, 8, 220))
        else:
            draw.text((cur, y), t, font=col)
        cur += w
    return box0


def rounded_panel(draw, box, fill, radius, outline=None, outline_w=0):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=outline_w)


# --------------------------------------------------------------------------
# sticker 贴纸风渲染
# --------------------------------------------------------------------------

def _rgba(c, a=255):
    """把 '#RRGGBB' 或 (r,g,b) 统一成 RGBA 元组。"""
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (a,)
    if len(c) == 3:
        return tuple(c) + (a,)
    return tuple(c)


def render_sticker_line(toks, font_path, size, color, accent, outline_rgb, shadow_rgba,
                        outline_ratio=0.16):
    """渲染一行贴纸字：投影 → 粗白描边 → 彩色字身。返回 RGBA 图与行宽。"""
    f = ImageFont.truetype(font_path, size)
    ow = max(3, int(size * outline_ratio))
    widths = [f.getlength(t) for t, _ in toks]
    total = int(sum(widths)) + size  # 末字留呼吸
    pad = ow + size
    Wl, Hl = total + pad * 2, int(size * 1.55) + pad * 2
    img = Image.new("RGBA", (Wl, Hl), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base_y = pad + int(size * 0.12)
    xs = pad
    spans = []
    for (t, _), w in zip(toks, widths):
        spans.append((xs, t))
        xs += w
    # 投影
    for x0, t in spans:
        d.text((x0 + ow * 0.5, base_y + ow * 0.7), t, font=f, fill=shadow_rgba,
               stroke_width=ow, stroke_fill=shadow_rgba)
    # 白描边底
    for x0, t in spans:
        d.text((x0, base_y), t, font=f, fill=_rgba(outline_rgb),
               stroke_width=ow, stroke_fill=_rgba(outline_rgb))
    # 彩色字身
    for (x0, _), (t, is_acc) in zip(spans, toks):
        col = accent if is_acc else color
        d.text((x0, base_y), t, font=f, fill=_rgba(col))
    crop = img.getbbox()
    if crop:
        img = img.crop(crop)
    return img, int(sum(widths))


def paste_rotated(canvas, img, center, angle):
    rot = img.rotate(angle, expand=True, resample=Image.BICUBIC)
    x = int(center[0] - rot.width / 2)
    y = int(center[1] - rot.height / 2)
    canvas.alpha_composite(rot, (max(0, x), max(0, y)))
    return [x, y, x + rot.width, y + rot.height]


def draw_tape(canvas, text, font_path, center, scale, st, angle=-2.0):
    """黄油色胶带笔刷 + 深棕字。"""
    f = ImageFont.truetype(font_path, int(46 * scale))
    tw = int(f.getlength(text))
    pad_x, h = int(34 * scale), int(78 * scale)
    Wl, Hl = tw + pad_x * 2, h + int(24 * scale)
    img = Image.new("RGBA", (Wl, Hl), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    tape = _rgba(st["tape"])
    d.rounded_rectangle([0, int(12 * scale), Wl - 1, Hl - int(12 * scale)],
                        radius=int(14 * scale), fill=tape)
    # 笔刷两端小突起
    d.ellipse([0, int(20 * scale), int(16 * scale), int(58 * scale)], fill=tape)
    d.ellipse([Wl - int(16 * scale), int(20 * scale), Wl - 1, int(58 * scale)], fill=tape)
    d.text(((Wl - tw) / 2, (Hl - f.size * 1.25) / 2), text, font=f, fill=_rgba(st["tape_text"]))
    box = paste_rotated(canvas, img, center, angle)
    return box


def draw_bubble(canvas, text, font_path, center, scale, st, angle=3.0):
    """红色对话气泡角标：白描边 + 尾巴 + 白字。"""
    f = ImageFont.truetype(font_path, int(44 * scale))
    tw = int(f.getlength(text))
    lines_n = 1
    if tw > 160 * scale:  # 超宽自动两行
        mid = len(text) // 2
        text_lines = [text[:mid], text[mid:]]
        tw = int(max(f.getlength(t) for t in text_lines))
        lines_n = 2
    else:
        text_lines = [text]
    pad = int(26 * scale)
    Wl = tw + pad * 2 + int(20 * scale)
    Hl = int(f.size * 1.35) * lines_n + pad * 2 + int(20 * scale)
    img = Image.new("RGBA", (Wl + int(40 * scale), Hl + int(40 * scale)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox = oy = int(20 * scale)
    boxb = [ox, oy, ox + Wl, oy + Hl]
    rad = int(Hl * 0.42)
    white = (255, 255, 255, 255)
    d.rounded_rectangle([boxb[0] - 6 * scale, boxb[1] - 6 * scale,
                         boxb[2] + 6 * scale, boxb[3] + 6 * scale],
                        radius=rad + int(6 * scale), fill=white)
    d.polygon([(ox + int(24 * scale), oy + Hl - int(8 * scale)),
               (ox + int(6 * scale), oy + Hl + int(30 * scale)),
               (ox + int(56 * scale), oy + Hl - int(8 * scale))], fill=white)
    d.rounded_rectangle(boxb, radius=rad, fill=_rgba(st["panel"]))
    d.polygon([(ox + int(30 * scale), oy + Hl - int(10 * scale)),
               (ox + int(16 * scale), oy + Hl + int(18 * scale)),
               (ox + int(52 * scale), oy + Hl - int(10 * scale))], fill=_rgba(st["panel"]))
    ty = oy + pad - int(4 * scale)
    for t in text_lines:
        d.text((ox + (Wl - f.getlength(t)) / 2, ty), t, font=f, fill=_rgba(st["panel_text"]))
        ty += f.size * 1.35
    box = paste_rotated(canvas, img, center, angle)
    return box


def draw_spark(canvas, cx, cy, spread_deg=-90, n=3, r0=0.06, r1=0.16, width=8, color=(247, 201, 72)):
    """放射短线组（黄色 emphasis lines）。r 为相对画布宽度的比例半径。"""
    d = ImageDraw.Draw(canvas)
    W = canvas.width
    span = math.radians(spread_deg)
    for i in range(n):
        a = span + (i - (n - 1) / 2.0) * math.radians(22)
        x0 = cx + math.cos(a) * W * r0
        y0 = cy + math.sin(a) * W * r0
        x1 = cx + math.cos(a) * W * r1
        y1 = cy + math.sin(a) * W * r1
        d.line([(x0, y0), (x1, y1)], fill=color + (255,), width=int(width), joint="curve")


def draw_heart(canvas, cx, cy, s, color=(224, 73, 47)):
    d = ImageDraw.Draw(canvas)
    r = s / 2.0
    d.ellipse([cx - r, cy - r * 1.1, cx, cy - r * 0.1], fill=color + (255,))
    d.ellipse([cx, cy - r * 1.1, cx + r, cy - r * 0.1], fill=color + (255,))
    d.polygon([(cx - r * 0.98, cy - r * 0.28), (cx + r * 0.98, cy - r * 0.28), (cx, cy + r)],
              fill=color + (255,))


def paste_person_sticker(canvas, cut, x, y_bottom, target_h, scale,
                         outline_ratio=0.016, shadow=True):
    """透明抠图人物贴纸：白描边 + 柔和投影，压在菜品上。"""
    ratio = cut.width / cut.height
    ph = int(target_h)
    pw = int(ph * ratio)
    cut = cut.resize((pw, ph), Image.LANCZOS)
    a = cut.getchannel("A")
    ow = max(4, int(pw * outline_ratio))
    if shadow:
        sh = Image.new("L", (pw + int(60 * scale), ph + int(60 * scale)), 0)
        sh.paste(a, (int(24 * scale), int(34 * scale)))
        sh = sh.filter(ImageFilter.GaussianBlur(int(12 * scale)))
        layer = Image.new("RGBA", sh.size, (40, 24, 12, 120))
        shadow_img = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_img.paste(layer, (x - int(12 * scale), y_bottom - ph - int(12 * scale)), sh)
        canvas.alpha_composite(shadow_img)
    ring = a.filter(ImageFilter.MaxFilter(ow * 2 + 1))
    ring_img = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
    ring_img.putalpha(ring)
    canvas.alpha_composite(ring_img, (x, y_bottom - ph))
    canvas.alpha_composite(cut, (x, y_bottom - ph))


def srgb_profile_bytes():
    for p in ["/System/Library/ColorSync/Profiles/sRGB Profile.icc",
              "/usr/share/color/icc/sRGB.icc"]:
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    return f.read()
            except Exception:
                pass
    return None


def load_banned():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "banned-words.txt")
    words = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
    return words


CONTEXT_SAFE = {
    "第一": ["次", "回", "趟", "口", "天", "顿", "时间", "次做", "碗", "盘"],
    "唯一": [],
    "绝对": [],
}


def check_copy(title, subtitle, badge):
    text = "%s%s%s" % (title, subtitle, badge)
    plain = re.sub(r"\s+", "", text)
    if len(plain) > MAX_TOTAL_CHARS:
        die("文案总字数 %d（标题+副标题+角标），超过 %d 字上限，请精简后重试。" % (len(plain), MAX_TOTAL_CHARS))
    hard, soft = [], []
    for w in load_banned():
        for m in re.finditer(re.escape(w), text):
            tail = text[m.end():m.end() + 2]
            if w in CONTEXT_SAFE and any(tail.startswith(s) for s in CONTEXT_SAFE[w]):
                soft.append("%s（在“%s”语境中属经历叙述，非排位宣称）" % (w, w + tail.strip()))
            else:
                hard.append(w)
    if hard:
        die("文案命中禁用词：%s。请改写后重试（参考 references/banned-words.txt）。"
            % "、".join(sorted(set(hard))))
    for s in soft:
        print("WARN: 命中绝对化用字词但属正常口语语境 —— %s；建议确认无排位暗示。" % s)


def render_sticker(args, st, fonts, W, H, scale, mx, top, avail_w, photo, person, report):
    """贴纸人气风主渲染。"""
    canvas = build_sticker_background(photo, W, H, st).convert("RGBA")
    boxes = []

    lines = [[tk for tk in tokenize(l, args.highlights)] for l in wrap_title(args.title)]

    # 自适应字号：行宽 + 白描边必须放进安全区
    hi = int(TITLE_SIZE_RANGE_STICKER[1] * scale)
    lo = int(TITLE_SIZE_RANGE_STICKER[0] * scale)
    size = lo
    for s in range(hi, lo - 1, -2):
        ok = True
        for ln in lines:
            f = ImageFont.truetype(fonts["title"], s)
            lw = sum(f.getlength(t) for t, _ in ln) + s * 0.4 + 2 * int(s * 0.16) * 2
            if lw > avail_w:
                ok = False
                break
        if ok:
            size = s
            break
    else:
        size = lo

    color = st["title_color"]
    accent = st["accent"]
    shadow = st["shadow"]
    angles = [-3.0, 2.6]
    offsets = [-int(W * 0.018), int(W * 0.022)]
    line_h = int(size * 1.22)
    y = top + int(size * 0.72) + int(26 * scale)  # 预留旋转抬升，保证顶边在安全区内
    for i, ln in enumerate(lines):
        img, _ = render_sticker_line(ln, fonts["title"], size, color, accent,
                                     (255, 255, 255), shadow)
        cx = W / 2 + offsets[i % 2]
        box = paste_rotated(canvas, img, (cx, y), angles[i % 2])
        boxes.append({"role": "title", "bbox": box})
        y += line_h
    band_bottom = y

    # 副标题：胶带笔刷
    if args.subtitle:
        tcx, tcy = W * 0.52, band_bottom + int(58 * scale)
        tbox = draw_tape(canvas, args.subtitle, fonts["sub"], (tcx, tcy), scale, st)
        boxes.append({"role": "subtitle", "bbox": tbox})

    # 角标：红色对话气泡（默认右侧中上，避开人物）
    if args.badge:
        bx, by = W * 0.775, band_bottom + int(150 * scale) if not args.subtitle else band_bottom + int(170 * scale)
        bub = draw_bubble(canvas, args.badge, fonts["badge"], (bx, by), scale, st)
        boxes.append({"role": "badge", "bbox": bub})

    # 人物贴纸：左下，压住菜品一角
    if person is not None:
        ph = H * args.person_h
        paste_person_sticker(canvas, person, int(W * 0.012), int(H * 0.985), ph, scale)

    # 涂鸦：放射短线 + 小爱心（合计 ≤2 组装饰）
    draw_spark(canvas, int(W * 0.085), int(top * 0.62), spread_deg=-55, n=3,
               r0=0.055, r1=0.13, width=int(9 * scale))
    draw_spark(canvas, int(W * 0.93), int(band_bottom * 0.52), spread_deg=-115, n=2,
               r0=0.05, r1=0.115, width=int(8 * scale))
    if person is not None:
        draw_spark(canvas, int(W * 0.36), int(H * (0.985 - args.person_h * 0.78)),
                   spread_deg=-40, n=2, r0=0.045, r1=0.10, width=int(8 * scale))
    draw_heart(canvas, int(W * 0.615), int(band_bottom + (int(30 * scale) if not args.subtitle else 110 * scale)),
               int(26 * scale), color=(224, 73, 47))

    out = canvas.convert("RGB")
    report["title_size_px"] = size
    report["title_lines"] = ["".join(t for t, _ in ln) for ln in lines]
    report["text_boxes"] = [{"role": b["role"], "bbox": [round(v, 1) for v in b["bbox"]]}
                            for b in boxes]
    return out


def main():
    ap = argparse.ArgumentParser(description="小红书美食封面合成")
    ap.add_argument("--photo", required=True, help="菜品/场景照片路径")
    ap.add_argument("--photo2", help="对比拼贴式下半图（可选，缺省用同一照片的不同裁切）")
    ap.add_argument("--person", help="透明抠图人物 PNG（RGBA），sticker 风贴纸叠加用")
    ap.add_argument("--title", required=True, help="主标题，<=12 字")
    ap.add_argument("--subtitle", default="", help="副标题/补充信息")
    ap.add_argument("--badge", default="", help="角标/标签，如 人均38")
    ap.add_argument("--highlight", default="", help="额外换色强调词，逗号分隔")
    ap.add_argument("--layout", default="sticker3", choices=sorted(LAYOUTS))
    ap.add_argument("--style", default="sticker", choices=sorted(STYLES))
    ap.add_argument("--size", default="1242x1656")
    ap.add_argument("--person-h", type=float, default=0.52, help="sticker 风人物高度占画布比例")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", help="文字框坐标报告，默认 <out>.report.json")
    ap.add_argument("--jpeg", action="store_true", help="同时另存一份 JPG")
    ap.add_argument("--subject-fill", type=float, help="覆盖主体框高度占比（0.4~0.95）")
    args = ap.parse_args()

    args.highlights = [h.strip() for h in args.highlight.split(",") if h.strip()]

    check_copy(args.title, args.subtitle, args.badge)

    try:
        W, H = [int(v) for v in args.size.lower().split("x")]
    except Exception:
        die("--size 需形如 1242x1656")
    if abs(W / H - 0.75) > 0.002:
        die("画布必须严格 3:4 竖版，当前 %dx%d 不合规。" % (W, H))
    if W < 1080:
        die("宽度不得低于 1080px，当前 %d。" % W)
    if (W, H) not in ALLOWED_SIZES:
        print("WARN: %dx%d 不在推荐尺寸（1242x1656 / 1080x1440），建议改回推荐值。" % (W, H))

    st = STYLES[args.style]
    lay = LAYOUTS[args.layout]
    scale = W / BASE_W
    mx = int(SAFE_MARGIN_X * scale)
    top = int(SAFE_TOP * scale)
    bottom = int(SAFE_BOTTOM * scale)
    avail_w = W - 2 * mx

    fonts = resolve_fonts(args.style)
    photo = load_photo(args.photo)
    person = load_cutout(args.person) if args.person else None

    report = {
        "image": os.path.abspath(args.out),
        "size": [W, H],
        "ratio": round(W / H, 4),
        "layout": args.layout, "layout_name": lay["name"],
        "style": args.style, "style_name": st["name"],
        "title": args.title, "subtitle": args.subtitle, "badge": args.badge,
        "title_lines": [], "title_font": fonts["title"], "title_size_px": 0,
        "safe": {"margin_x": mx, "top": top, "bottom": bottom},
        "text_boxes": [],
        "file_mb": 0,
        "color_groups": [st["title_color"], st["accent"], st["panel"]],
    }

    if args.style == "sticker":
        canvas = render_sticker(args, st, fonts, W, H, scale, mx, top, avail_w,
                                photo, person, report)
    else:
        # ---------- 旧四风格渲染 ----------
        canvas = build_background(photo, W, H, st, args.style)
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        sub_box = list(lay["subject"])
        if args.subject_fill:
            f = max(0.4, min(0.95, args.subject_fill))
            y0 = sub_box[1]
            sub_box[3] = min(1.0, y0 + f)
        subject = enhance_subject(photo, st)
        box_px = tuple(v * W if i % 2 == 0 else v * H for i, v in enumerate(
            [sub_box[0], sub_box[1], sub_box[2], sub_box[3]]))
        rel_w = sub_box[2] - sub_box[0]
        rel_h = sub_box[3] - sub_box[1]
        inner = cover_crop(subject, max(1, int(rel_w * W)), max(1, int(rel_h * H)),
                           fy=lay.get("fy", 0.5))
        if args.layout == "person":
            paste_feather(canvas, inner, box_px, feather_ratio=0.10, v_feather=0.06)
        elif args.layout == "compare":
            paste_feather(canvas, inner, box_px, feather_ratio=0.05, v_feather=0.12)
            box2 = lay.get("subject2")
            p2 = photo
            if args.photo2:
                p2 = load_photo(args.photo2)
            inner2 = cover_crop(enhance_subject(p2, st),
                                int((box2[2] - box2[0]) * W), int((box2[3] - box2[1]) * H),
                                fy=lay.get("fy2", 0.5))
            b2 = (box2[0] * W, box2[1] * H, box2[2] * W, box2[3] * H)
            paste_feather(canvas, inner2, b2, feather_ratio=0.05, v_feather=0.12)
        else:
            paste_feather(canvas, inner, box_px, feather_ratio=0.12)

        canvas = canvas.convert("RGBA")

        lines = [[tk for tk in tokenize(l)] for l in wrap_title(args.title)]
        band = lay["title_band"]
        boxes = []

        title_font, tsize = fit_font_size(od, lines, fonts["title"], scale, avail_w - int(24 * scale),
                                          TITLE_SIZE_RANGE)
        line_gap = tsize * 1.45

        if band == "top":
            block_h = line_gap * len(lines)
            y_start = top + int(6 * scale)
            band_bottom = y_start + block_h
            if st["band"] == "scrim":
                pad = int(26 * scale)
                od.rectangle([0, 0, W, band_bottom + pad], fill=st["scrim"])
            elif st["band"] == "panel":
                pad = int(22 * scale)
                rounded_panel(od, [mx - pad, y_start - pad, W - mx + pad, band_bottom + pad],
                              st["panel"] + "E6" if len(st["panel"]) == 7 else st["panel"],
                              radius=int(26 * scale))
            y = y_start
            for toks in lines:
                b = draw_text_line(od, toks, title_font, mx, y, st["title_color"], st["accent"],
                                   scale, st["text_align"], avail_w)
                boxes.append({"role": "title", "bbox": b})
                y += line_gap
        elif band == "middle":
            pad = int(30 * scale)
            block_h = line_gap * len(lines)
            y_center = H * 0.5
            y_start = y_center - block_h / 2
            rounded_panel(od, [0, y_start - pad, W, y_start + block_h + pad],
                          st["scrim"], radius=0)
            y = y_start
            for toks in lines:
                b = draw_text_line(od, toks, title_font, mx, y, st["title_color"], st["accent"],
                                   scale, st["text_align"], avail_w)
                boxes.append({"role": "title", "bbox": b})
                y += line_gap
        else:
            die("未知标题带位置：%s" % band)

        padx, pady = int(22 * scale), int(9 * scale)
        if args.badge:
            bfont = ImageFont.truetype(fonts["badge"], int(BADGE_SIZE_RANGE[1] * scale))
            bw = od.textlength(args.badge, font=bfont)
            bh = int(bfont.size * 1.42)
            if band == "middle":
                by = H * 0.5 + line_gap * len(lines) / 2 + int(34 * scale)
            else:
                by = top + int(6 * scale) + line_gap * len(lines) + int(16 * scale)
            by = max(top, min(by, H - bottom - bh))
            bx = (W - (bw + 2 * padx)) / 2.0
            rounded_panel(od, [bx, by, bx + bw + 2 * padx, by + bh],
                          st["panel"], radius=int(bh * 0.42))
            od.text((bx + padx, by + pady), args.badge, font=bfont, fill=st["panel_text"])
            boxes.append({"role": "badge", "bbox": [bx, by, bx + bw + 2 * padx, by + bh]})

        if args.subtitle:
            txt = args.subtitle
            sfont = ImageFont.truetype(fonts["sub"], int(SUB_SIZE_RANGE[1] * scale))
            for s in range(int(SUB_SIZE_RANGE[1] * scale), int(SUB_SIZE_RANGE[0] * scale) - 1, -2):
                sfont = ImageFont.truetype(fonts["sub"], s)
                if od.textlength(txt, font=sfont) <= avail_w - int(80 * scale):
                    break
            sw = od.textlength(txt, font=sfont)
            bh = int(sfont.size * 1.5)
            pad = int(20 * scale)
            y0 = H - bottom - bh - pad
            x0 = (W - sw) / 2.0
            if st["band"] == "panel":
                rounded_panel(od, [x0 - padx, y0 - pady, x0 + sw + padx, y0 + bh + pady],
                              st["panel"], radius=int(bh * 0.5))
                od.text((x0, y0), txt, font=sfont, fill=st["panel_text"])
            else:
                od.rectangle([0, y0 - pad, W, H], fill=st["scrim"])
                od.text((x0, y0), txt, font=sfont, fill=st["title_color"],
                        stroke_width=max(1, int(1.5 * scale)), stroke_fill=(14, 10, 8, 200))
            boxes.append({"role": "subtitle", "bbox": [x0, y0, x0 + sw, y0 + bh]})

        canvas = Image.alpha_composite(canvas, overlay).convert("RGB")
        report["title_size_px"] = tsize
        report["title_lines"] = ["".join(t for t, _ in ln) for ln in lines]
        report["text_boxes"] = [{"role": b["role"],
                                 "bbox": [round(v, 1) for v in b["bbox"]]} for b in boxes]

    # ---------- 输出 ----------
    outdir = os.path.dirname(os.path.abspath(args.out))
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)
    icc = srgb_profile_bytes()
    canvas.save(args.out, format="PNG", icc_profile=icc, optimize=True)
    size_mb = os.path.getsize(args.out) / 1024.0 / 1024.0
    report["file_mb"] = round(size_mb, 3)
    if size_mb > MAX_FILE_MB:
        print("WARN: 文件 %.2fMB 超过 5MB，建议加 --jpeg 或改用 1080x1440。" % size_mb)
    out_paths = [args.out]
    if args.jpeg:
        j = os.path.splitext(args.out)[0] + ".jpg"
        canvas.save(j, format="JPEG", quality=90, icc_profile=icc, optimize=True, subsampling=1)
        out_paths.append(j)

    report_path = args.report or (os.path.splitext(args.out)[0] + ".report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("已生成封面：%s（%dx%d，%.2fMB）" % (args.out, W, H, size_mb))
    print("版式：%s｜风格：%s｜主标题字号 %dpx" % (lay["name"], st["name"], report["title_size_px"]))
    print("分行：%s" % " / ".join(report["title_lines"]))
    print("校验报告：%s" % report_path)


if __name__ == "__main__":
    main()
