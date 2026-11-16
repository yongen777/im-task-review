# -*- coding: utf-8 -*-
"""2026-11-16 紧急体验修复 · H5 图片上传「文件头魔数与扩展名不匹配」误杀专项。

背景：真实用户（杨继全等）在 H5 上传正常图片频繁被拒——旧逻辑把图片扩展名与魔数
一一硬绑定（.jpg 里装合法 PNG/WEBP 即拒绝）。微信/密信转存图、截图工具导出图、
手机兼容图大量属于这类「扩展名与内容真实格式不一致但都是合法图片」的场景。

新口径（用户拍板）：
  · 扩展名 ∈ 图片白名单（jpg/jpeg/png/webp/bmp/gif）且内容嗅探为**任意**合法图片格式
    （JPEG/PNG/WEBP/GIF/BMP）→ 放行，并按嗅探出的真实 MIME 与真实扩展名落盘保存；
  · 非图片魔数（HTML/脚本/EXE 伪装成 .jpg）→ 严格拒绝；
  · HEIC/HEIF 原图（iPhone 拍摄默认格式）→ 友好提示「请截图或转为 JPG/PNG」；
  · 前端：accept 收窄 + HEIC 预检 + FILE_TYPE_REJECTED 人话提示。

绝对时刻 fake clock + 内存假件，零真实网络。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from app.api.h5 import (  # noqa: E402
    MIME_TO_IMAGE_EXT,
    process_h5_task_upload,
    sniff_heic_like,
    sniff_image_mime,
    sniff_magic_mime,
)
from app.scheduler import FakeNotifier, Scheduler  # noqa: E402
from app.state import StateStore  # noqa: E402

PHONE = "13900005001"
CLOCK = {"now": datetime(2026, 11, 16, 8, 40, 0)}

# ── 真实魔数字节流（合法图片的最小真实头 + 填充） ──
REAL_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
REAL_JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 40
REAL_WEBP = b"RIFF\xf4\x00\x00\x00WEBPVP8 " + b"\x00" * 40
REAL_GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 40
REAL_BMP = b"BM" + (100).to_bytes(4, "little") + b"\x00" * 8 + (40).to_bytes(4, "little") + b"\x00" * 40
# ── 伪装/非法字节流 ──
EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 40
SCRIPT_BYTES = b"<script>alert(1)</script>\n" + b" " * 20
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heicmif1" + b"\x00" * 20
HEIF_MIF1_BYTES = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic" + b"\x00" * 20
RIFF_WAVE_BYTES = b"RIFF$\x08\x00\x00WAVEfmt " + b"\x00" * 40  # RIFF 容器但不是 WEBP 图片


def _setup():
    """图片任务（无截止）→ tick 预生成实例 → 签发 H5 token。返回 (store, uploads_dir, token)。"""
    CLOCK["now"] = datetime(2026, 11, 16, 8, 40, 0)
    root = Path(mkdtemp(prefix="imtask_imgsniff_"))
    store = StateStore(root / "im.db", now_fn=lambda: CLOCK["now"])
    rid = store.create_role("拍照组", actor="t", platform="zdxlz")["role_id"]
    store.add_role_member(rid, PHONE, "甲一", "一部", actor="t")
    store.upsert_delivery_target(PHONE, "uid_5001", platform="zdxlz", external_type="union_id")
    tpl = store.create_template({
        "name": "工位拍照", "kind": "image", "role_ids": [rid], "text_tpl": "请拍照",
        "remind_at": "09:00", "has_deadline": False, "period": "day",
        "need_review": False, "enabled": True,
    }, actor="t")
    ntf = FakeNotifier(now_fn=lambda: CLOCK["now"])
    sched = Scheduler(state=store, public_base_url="http://t.example.com", notifier=ntf,
                      now_fn=lambda: CLOCK["now"])
    sched.run_once()  # 当天预生成实例（无截止 → 窗口常开）
    inst = store.get_instance_by_key(tpl["template_id"], PHONE, "2026-11-16")
    assert inst is not None, "tick 应预生成图片任务实例"
    tok = store.issue_h5_token(PHONE, tpl["template_id"], "2026-11-16", inst["id"])
    uploads = root / "uploads"
    return store, uploads, tok, tpl["template_id"]


def _upload(store, uploads, tok, filename: str, content: bytes):
    return process_h5_task_upload(
        state=store, uploads_dir=uploads, raw_token=tok,
        filename=filename, content_bytes=content,
    )


def _disk_files(uploads: Path):
    if not uploads.exists():
        return []
    return [p for p in uploads.rglob("*") if p.is_file()]


# ═══════════════════ 纯函数层：内容嗅探 ═══════════════════

def test_sniff_image_mime_by_content() -> None:
    """内容嗅探：五种合法图片魔数各自命中；文本/EXE/空/RIFF非WEBP 一律 None。"""
    assert sniff_image_mime(REAL_JPG[:32]) == "image/jpeg"
    assert sniff_image_mime(REAL_PNG[:32]) == "image/png"
    assert sniff_image_mime(REAL_WEBP[:32]) == "image/webp"
    assert sniff_image_mime(REAL_GIF[:32]) == "image/gif"
    assert sniff_image_mime(REAL_BMP[:32]) == "image/bmp"
    assert sniff_image_mime(SCRIPT_BYTES[:32]) is None
    assert sniff_image_mime(EXE_BYTES[:32]) is None
    assert sniff_image_mime(RIFF_WAVE_BYTES[:32]) is None  # RIFF 容器 ≠ WEBP 图片
    assert sniff_image_mime(b"") is None
    # 纯文本以 "BM" 开头（如 "BMW is…"）不得误判为 BMP（靠 DIB 头尺寸字段拦截）
    assert sniff_image_mime(b"BMW is a great car brand\x00\x00\x00\x00") is None


def test_sniff_magic_mime_image_any_match() -> None:
    """白名单扩展名 × 任意合法图片内容 = 放行（旧逻辑的一一硬绑定是本次要修的根因）。"""
    assert sniff_magic_mime("jpg", REAL_PNG[:32]) == "image/png"   # ⛔ 旧版这里返回 None
    assert sniff_magic_mime("jpg", REAL_WEBP[:32]) == "image/webp"  # ⛔ 旧版 None
    assert sniff_magic_mime("png", REAL_JPG[:32]) == "image/jpeg"   # ⛔ 旧版 None
    assert sniff_magic_mime("jpeg", REAL_JPG[:32]) == "image/jpeg"
    assert sniff_magic_mime("webp", REAL_WEBP[:32]) == "image/webp"
    assert sniff_magic_mime("bmp", REAL_BMP[:32]) == "image/bmp"
    assert sniff_magic_mime("gif", REAL_GIF[:32]) == "image/gif"
    # 非图片内容伪装图片扩展名 → 拒绝
    assert sniff_magic_mime("jpg", SCRIPT_BYTES[:32]) is None
    assert sniff_magic_mime("jpg", EXE_BYTES[:32]) is None
    assert sniff_magic_mime("gif", SCRIPT_BYTES[:32]) is None
    # PDF / 文档类保持原有严格绑定（不受本次改动影响）
    assert sniff_magic_mime("pdf", b"%PDF-1.7\n" + b"\x00" * 24) == "application/pdf"
    assert sniff_magic_mime("pdf", REAL_PNG[:32]) is None


def test_mime_to_image_ext_mapping() -> None:
    assert MIME_TO_IMAGE_EXT == {
        "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "image/gif": "gif", "image/bmp": "bmp",
    }


def test_sniff_heic_like_brands() -> None:
    assert sniff_heic_like(HEIC_BYTES[:32]) is True
    assert sniff_heic_like(HEIF_MIF1_BYTES[:32]) is True
    assert sniff_heic_like(REAL_JPG[:32]) is False
    assert sniff_heic_like(REAL_PNG[:32]) is False
    assert sniff_heic_like(b"") is False


# ═══════════════════ 上传放行：真实内容决定格式并落盘 ═══════════════════

def test_jpg_ext_png_content_allowed_and_saved_as_png() -> None:
    """.jpg 后缀 + 真实 PNG 内容 → 200 放行，落盘为 .png，MIME=image/png，原名保留。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "转存图.jpg", REAL_PNG)
        assert code == 200, res
        assert res["mime"] == "image/png", res
        assert res["rel_path"].endswith(".png"), res
        assert res["file_name"] == "转存图.jpg", res  # 显示名保留用户原文件名
        files = _disk_files(uploads)
        assert len(files) == 1 and files[0].suffix == ".png", files
        assert files[0].read_bytes() == REAL_PNG, "落盘字节必须与上传内容一致"
    finally:
        store.close()


def test_jpg_ext_webp_content_allowed() -> None:
    """.jpg 后缀 + 真实 WEBP 内容（截图工具常见）→ 200，按 .webp 落盘。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "截图.jpg", REAL_WEBP)
        assert code == 200 and res["mime"] == "image/webp", res
        assert res["rel_path"].endswith(".webp"), res
        assert _disk_files(uploads)[0].suffix == ".webp"
    finally:
        store.close()


def test_png_ext_jpg_content_allowed() -> None:
    """.png 后缀 + 真实 JPEG 内容 → 200，按 .jpg 落盘（反向同理）。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "微信图片.png", REAL_JPG)
        assert code == 200 and res["mime"] == "image/jpeg", res
        assert res["rel_path"].endswith(".jpg"), res
    finally:
        store.close()


def test_bmp_and_gif_newly_whitelisted() -> None:
    """bmp/gif 纳入图片白名单：真实内容各自放行并按真实扩展名落盘。"""
    store, uploads, tok, _ = _setup()
    try:
        code_gif, res_gif = _upload(store, uploads, tok, "动图.gif", REAL_GIF)
        assert code_gif == 200 and res_gif["mime"] == "image/gif", res_gif
        assert res_gif["rel_path"].endswith(".gif")

        code_bmp, res_bmp = _upload(store, uploads, tok, "老图.bmp", REAL_BMP)
        assert code_bmp == 200 and res_bmp["mime"] == "image/bmp", res_bmp
        assert res_bmp["rel_path"].endswith(".bmp")
        assert len(_disk_files(uploads)) == 2
    finally:
        store.close()


# ═══════════════════ 上传拒绝：伪装内容零残留 ═══════════════════

def test_jpg_ext_script_strictly_rejected() -> None:
    """.jpg 后缀 + <script> 内容 → 400 FILE_TYPE_REJECTED，磁盘零残留。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "恶意.jpg", SCRIPT_BYTES)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
        assert "魔数" not in str(res.get("message")), "新文案不得再出现晦涩的「魔数」术语"
        assert _disk_files(uploads) == [], "拒绝路径必须保证磁盘零残留"
    finally:
        store.close()


def test_jpg_ext_exe_strictly_rejected() -> None:
    """.jpg 后缀 + MZ 可执行头 → 400，磁盘零残留。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "木马改名的图.jpg", EXE_BYTES)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
        assert _disk_files(uploads) == []
    finally:
        store.close()


def test_webp_ext_riff_wave_rejected() -> None:
    """webp 后缀 + RIFF/WAVE 音频头 → 400（RIFF 容器不等于 WEBP 图片）。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "录音.webp", RIFF_WAVE_BYTES)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
    finally:
        store.close()


def test_text_starting_bm_not_bmp() -> None:
    """bmp 后缀 + 纯文本 "BMW…" → 400（防 "BM" 弱前缀导致的纯文本误放行）。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "营销话术.bmp", b"BMW is a great car brand" + b"\x00" * 20)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
        assert _disk_files(uploads) == []
    finally:
        store.close()


def test_heic_rename_to_jpg_gets_friendly_message() -> None:
    """HEIC 原图改名 .jpg 混传 → 400 + 「暂不支持 HEIC 原图格式，请截图或转为 JPG/PNG 后上传」。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "IMG_0001.jpg", HEIC_BYTES)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
        assert res["message"] == "暂不支持 HEIC 原图格式，请截图或转为 JPG/PNG 后上传", res["message"]
        # mif1（HEIF 序列）同理
        code2, res2 = _upload(store, uploads, tok, "IMG_0002.jpg", HEIF_MIF1_BYTES)
        assert code2 == 400 and "HEIC" in res2["message"], res2
        assert _disk_files(uploads) == []
    finally:
        store.close()


def test_image_task_rejects_non_image_ext_with_friendly_text() -> None:
    """图片任务传 .xlsx（在总白名单但不在图片白名单）→ 400，提示完整格式清单。"""
    store, uploads, tok, _ = _setup()
    try:
        code, res = _upload(store, uploads, tok, "报表.xlsx", b"PK\x03\x04" + b"\x00" * 40)
        assert code == 400 and res["code"] == "FILE_TYPE_REJECTED", res
        assert res["message"] == "图片类任务仅允许上传 jpg/png/webp/bmp/gif 格式", res["message"]
    finally:
        store.close()


# ═══════════════════ 前端契约（task.html 静态断言） ═══════════════════

def test_task_html_friendly_contract() -> None:
    """前端契约：accept 收窄、HEIC 预检友好提示、FILE_TYPE_REJECTED 人话映射、旧晦涩文案清除。"""
    html = (Path(__file__).resolve().parents[1] / "app" / "h5" / "task.html").read_text(encoding="utf-8")
    # accept 从 image/* 收窄到后端支持的五种图片（iOS 因 accept 不含 HEIC 会自动转码 JPEG）
    assert 'image/jpeg,image/png,image/webp,image/bmp,image/gif' in html, "accept 未收窄"
    assert '"image/*"' not in html, "accept 不得再用通配 image/*（会把 HEIC 放进选择器）"
    # HEIC 预检 + 友好提示（用户拍板文案）
    assert "暂不支持 HEIC 原图格式，请截图或转为 JPG/PNG 后上传" in html, "缺 HEIC 预检提示"
    assert "endswith(\".heic\")" in html or ".heic" in html, "缺 .heic 扩展名预检"
    # 后端 FILE_TYPE_REJECTED 在图片模块的人话映射
    assert "图片校验未通过" in html, "缺 FILE_TYPE_REJECTED 人话映射"
    # 旧晦涩术语必须从**用户可见文案**中整体消失（代码注释描述机制本身不算用户文案）
    py_src = (Path(__file__).resolve().parents[1] / "app" / "api" / "h5.py").read_text(encoding="utf-8")
    assert "文件头魔数与扩展名不匹配，拒绝上传" not in py_src, "后端仍向用户返回旧晦涩错误文案"
