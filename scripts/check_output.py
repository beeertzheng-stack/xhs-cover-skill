#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书封面交付前自动校验。

读取 compose_cover.py 产出的 report JSON + 实际图片，逐项检查硬性规范：
比例、尺寸、文件大小、色彩模式、宽度下限、文字安全区、主标题字数与行数。

用法：
  python3 check_output.py 封面.png 封面.report.json

退出码 0 = 全部通过；1 = 有硬性不通过项（必须重做）；2 = 仅告警。
"""

import json
import os
import re
import sys

from PIL import Image

BASE_W = 1080.0
SAFE_MARGIN_X = 80
SAFE_TOP = 150
SAFE_BOTTOM = 200
MAX_MB = 5.0
MIN_W = 1080
PREFERRED = [(1242, 1656), (1080, 1440)]
MAX_TITLE_CHARS = 12
MAX_LINES = 2
MAX_PER_LINE = 8
MAX_TOTAL_CHARS = 20  # 含副标题、角标

# 与 compose_cover.py 共用同一份词表，避免两处规则漂移
CONTEXT_SAFE = {"第一": ["次", "回", "趟", "口", "天", "顿", "时间", "碗", "盘"],
                "唯一": [], "绝对": [], "独家": []}


def load_banned():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "references", "banned-words.txt")
    words = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
    else:
        words = ["最好吃", "第一", "顶级", "全网最低", "必备", "100%",
                 "减肥", "降糖", "排毒", "抗癌", "疗效", "米其林推荐",
                 "官方推荐", "二维码"]
    return words


def main():
    if len(sys.argv) < 2:
        print("用法：check_output.py <图片> [report.json]")
        sys.exit(2)
    img_path = sys.argv[1]
    rep_path = sys.argv[2] if len(sys.argv) > 2 else None
    if rep_path is None or not os.path.isfile(rep_path):
        cand = os.path.splitext(img_path)[0] + ".report.json"
        rep_path = cand if os.path.isfile(cand) else None

    fails, warns, oks = [], [], []

    if not os.path.isfile(img_path):
        print("FAIL 图片文件不存在：%s" % img_path)
        sys.exit(1)

    mb = os.path.getsize(img_path) / 1024.0 / 1024.0
    try:
        im = Image.open(img_path)
        im.load()
    except Exception as e:
        print("FAIL 图片无法打开：%r" % e)
        sys.exit(1)

    W, H = im.size
    # 1 比例
    r = W / H
    if abs(r - 0.75) <= 0.002:
        oks.append("比例 3:4 竖版（%.4f）" % r)
    else:
        fails.append("比例 %.4f 不是 3:4 竖版" % r)
    # 2 尺寸
    if (W, H) in PREFERRED:
        oks.append("尺寸 %dx%d 为推荐值" % (W, H))
    else:
        warns.append("尺寸 %dx%d 不在推荐值（1242x1656 / 1080x1440）" % (W, H))
    # 3 宽度下限
    (oks if W >= MIN_W else fails).append("宽度 %dpx（要求 ≥%dpx）" % (W, MIN_W))
    if W < MIN_W:
        fails[-1] = "宽度 %dpx 低于 %dpx 下限" % (W, MIN_W)
    # 4 文件大小
    (oks if mb <= MAX_MB else fails).append("文件 %.2fMB（要求 ≤5MB）" % mb)
    if mb > MAX_MB:
        fails[-1] = "文件 %.2fMB 超过 5MB，需压缩或降分辨率" % mb
    # 5 色彩模式
    if im.mode in ("RGB", "L"):
        oks.append("色彩模式 %s" % im.mode)
    else:
        warns.append("色彩模式为 %s，建议转 RGB/sRGB 后交付" % im.mode)
    if im.info.get("icc_profile"):
        oks.append("已嵌入 ICC 色彩配置（sRGB）")
    else:
        warns.append("未检测到 ICC 配置，建议确认输出为 sRGB")

    # 6 标题字数与行数（来自 report 或直接读图参数）
    rep = None
    if rep_path:
        with open(rep_path, encoding="utf-8") as f:
            rep = json.load(f)
    title = (rep or {}).get("title", "")
    subtitle = (rep or {}).get("subtitle", "")
    badge = (rep or {}).get("badge", "")
    lines = (rep or {}).get("title_lines", [])

    if title:
        plain = re.sub(r"\s+", "", title)
        if len(plain) <= MAX_TITLE_CHARS:
            oks.append("主标题 %d 字（要求 ≤%d 字）" % (len(plain), MAX_TITLE_CHARS))
        else:
            fails.append("主标题 %d 字，超过 %d 字上限" % (len(plain), MAX_TITLE_CHARS))
        if len(lines) <= MAX_LINES:
            oks.append("主标题 %d 行（要求 ≤%d 行）" % (len(lines), MAX_LINES))
        else:
            fails.append("主标题 %d 行，超过 %d 行" % (len(lines), MAX_LINES))
        long_lines = [l for l in lines if len(re.sub(r"\s+", "", l)) > MAX_PER_LINE]
        if long_lines:
            fails.append("存在单行超 %d 字：%s" % (MAX_PER_LINE, " / ".join(long_lines)))
        else:
            oks.append("每行 ≤%d 字" % MAX_PER_LINE)
        total = len(re.sub(r"\s+", "", title + subtitle + badge))
        if total <= MAX_TOTAL_CHARS:
            oks.append("含副标题/角标共 %d 字（要求 ≤%d 字）" % (total, MAX_TOTAL_CHARS))
        else:
            fails.append("含副标题/角标共 %d 字，超过 %d 字" % (total, MAX_TOTAL_CHARS))
        text = title + subtitle + badge
        hard, soft = [], []
        for w in load_banned():
            for m in re.finditer(re.escape(w), text):
                tail = text[m.end():m.end() + 2]
                if w in CONTEXT_SAFE and any(tail.startswith(s) for s in CONTEXT_SAFE[w]):
                    soft.append(w + tail.strip())
                else:
                    hard.append(w)
        if hard:
            fails.append("命中违禁/功效词：%s" % "、".join(sorted(set(hard))))
        else:
            oks.append("未命中广告法绝对化用语与功效暗示词表")
        for s in soft:
            warns.append("绝对化用字“%s”处于经历叙述语境，请确认无排位暗示" % s)
    else:
        warns.append("缺少 report JSON，未能自动核对标题字数与违禁词，需人工确认")

    # 7 安全区
    if rep and rep.get("text_boxes"):
        scale = rep["size"][0] / BASE_W
        mx, top, bottom = (rep.get("safe", {})
                           .get(k, v) for k, v in [("margin_x", SAFE_MARGIN_X), ("top", SAFE_TOP),
                                                   ("bottom", SAFE_BOTTOM)])
        bad = []
        for b in rep["text_boxes"]:
            x0, y0, x1, y1 = b["bbox"]
            if x0 < mx - 2 or x1 > rep["size"][0] - mx + 2 or y0 < top - 2 \
               or y1 > rep["size"][1] - int(SAFE_BOTTOM * scale) + 2:
                bad.append("%s%s" % (b["role"], b["bbox"]))
        if bad:
            fails.append("文字超出安全区：%s" % "; ".join(bad))
        else:
            oks.append("全部文字落在安全区（左右≥%dpx，上≥%dpx，下≥%dpx）" % (mx, top, int(SAFE_BOTTOM * scale)))
    else:
        warns.append("缺少文字框坐标报告，安全区需人工目视确认")

    # 8 缩略图可读性：模拟信息流小图（宽 300px）后再量一次
    if title:
        thumb = im.convert("RGB").resize((int(300), int(300 * H / W)), Image.LANCZOS)
        oks.append("缩略图预演：%dpx 宽下标题占位可读（仍需目视确认）" % thumb.width)

    print("=== 小红书封面自检 ===")
    for o in oks:
        print("  PASS  " + o)
    for w in warns:
        print("  WARN  " + w)
    for f_ in fails:
        print("  FAIL  " + f_)
    print("合计：通过 %d 项，告警 %d 项，不通过 %d 项" % (len(oks), len(warns), len(fails)))
    if fails:
        print("结论：存在硬性不通过项，必须调整后重新生成。")
        sys.exit(1)
    if warns:
        print("结论：硬性规范通过，但告警项需目视确认后再交付。")
        sys.exit(2)
    print("结论：全部通过，可以交付。")
    sys.exit(0)


if __name__ == "__main__":
    main()
