"""赞赏（打赏）功能：页脚入口 + 收款码弹窗。

**为什么必须有测试（2026-11-05 加）**
------------------------------------
这个功能有个**只在线上才暴露**的坑：本地 Flask 站直接读 `static/`，图片永远正常；
而静态站（gh-pages）用的是 `export_json.py` **拷贝出来**的 `site/static/`。
漏拷 `static/images/` 的后果是：本机全绿、线上图裂 —— 恰好是最难发现的那一类。

所以这里既校验模板，也**真的跑一次导出**校验图片落地。
"""

import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
IMAGES = ("alipay-qr.jpeg", "wechat-qr.jpeg")


class TestTemplate:

    def test_base_includes_tip_modal(self):
        """入口在页脚，弹窗由 base.html include —— 两个页面共用。"""
        html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "components/tip_modal.html" in html, "base.html 没有引入赞赏弹窗"
        assert "btn-tip" in html, "页脚没有赞赏按钮"
        assert 'data-bs-target="#tipModal"' in html, "按钮没指向弹窗"

    def test_modal_has_both_qr_codes(self):
        html = (TEMPLATES / "components" / "tip_modal.html").read_text(encoding="utf-8")
        assert 'id="tipModal"' in html
        for name in IMAGES:
            assert "images/" + name in html, "弹窗缺少收款码 %s" % name
        assert "支付宝" in html and "微信" in html

    def test_qr_images_are_lazy(self):
        """两张码约 460 KB，不能拖慢首屏（弹窗默认是隐藏的）。"""
        html = (TEMPLATES / "components" / "tip_modal.html").read_text(encoding="utf-8")
        for img in re.findall(r"<img[^>]*images/[^>]*>", html):
            assert 'loading="lazy"' in img, "收款码图片没加 loading=lazy"

    def test_images_exist_in_repo(self):
        for name in IMAGES:
            p = ROOT / "static" / "images" / name
            assert p.is_file(), "缺少收款码文件: %s" % p
            assert p.stat().st_size > 10_000, "收款码图片太小，可能是坏文件"

    def test_footer_no_longer_mentions_retired_feature(self):
        """页脚曾写「沪深300 选股与交易计划」—— 交易计划已下线，不该再出现。"""
        html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "交易计划" not in html, "页脚仍写着已下线的「交易计划」"


class TestStaticExport:

    def test_export_copies_qr_images(self):
        """**关键**：静态导出必须把收款码拷进 site/static/images/。

        漏拷的话本地 Flask 测不出来，线上直接图裂。
        """
        from db_repository import open_db
        from export_json import export

        tmp = Path(tempfile.mkdtemp())
        db = str(tmp / "t.db")
        open_db(db).close()
        out = tmp / "site"
        try:
            export(db, str(out))
            for name in IMAGES:
                p = out / "static" / "images" / name
                assert p.is_file(), "静态产物缺少 %s —— 线上会图裂" % name
                assert p.stat().st_size > 10_000
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_pages_reference_existing_images(self):
        """页面里的 img src（去掉 ?v=）必须都能落到真实文件。"""
        from db_repository import open_db
        from export_json import export

        tmp = Path(tempfile.mkdtemp())
        db = str(tmp / "t.db")
        open_db(db).close()
        out = tmp / "site"
        try:
            export(db, str(out))
            for page in ("index.html", "cb.html"):
                html = (out / page).read_text(encoding="utf-8")
                assert "tipModal" in html, "%s 没有赞赏弹窗" % page
                refs = re.findall(r'src="([^"]*images/[^"]+)"', html)
                assert refs, "%s 没有引用收款码" % page
                for ref in refs:
                    assert (out / ref.split("?")[0]).is_file(), \
                        "%s 引用了不存在的图片 %s" % (page, ref)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestSizing:

    def test_qr_sized_by_width_not_height(self):
        """收款码必须按**宽度**放大，不能限高。

        这两张是带大片品牌留白的长海报（支付宝 1673x2507）。若用
        `max-height` 限高，真正的二维码会被压到 ~85px，手机扫不出来。
        宽 300px 时二维码约 165px —— 所以这里锁死用 max-width。
        """
        css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        m = re.search(r"\.tip-qr\{([^}]*)\}", css)
        assert m, "app.css 里找不到 .tip-qr 规则"
        body = m.group(1)
        assert "max-width" in body, ".tip-qr 应当用 max-width 控制尺寸"
        assert "max-height" not in body, (
            ".tip-qr 又改回 max-height 了 —— 二维码会被压到扫不出来")

    def test_modal_is_large_and_stacks_on_mobile(self):
        """弹窗要够宽；小屏两列必须能叠成一列。

        否则在 500px 的默认弹窗里，两列各只 ~230px，二维码太小。
        """
        html = (TEMPLATES / "components" / "tip_modal.html").read_text(encoding="utf-8")
        assert "modal-lg" in html, "弹窗没用 modal-lg，两列太挤"
        assert "col-12 col-sm-6" in html, "小屏没有叠成一列（应 col-12 col-sm-6）"
