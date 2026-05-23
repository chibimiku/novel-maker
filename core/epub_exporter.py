import html
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

try:
    import markdown
except ImportError:
    markdown = None


class EpubExporter:
    def __init__(self, workspace_manager):
        self.workspace = workspace_manager
        self._image_manifest_ids = {}
        self._image_counter = 0

    def export(self) -> str:
        if markdown is None:
            raise ImportError("缺少 markdown 库。请在终端执行: pip install markdown")

        tree_data = self.workspace.load_outline_tree()
        novel_title = tree_data.get("project_name", "未命名小说")
        chapters = tree_data.get("nodes", [])

        book_uid = str(uuid.uuid4())
        book_id = "urn:uuid:" + book_uid

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', novel_title).strip()
        epub_path = os.path.join(self.workspace.workspace_path, f"{safe_name}.epub")

        content_files = []
        for idx, chapter in enumerate(chapters, start=1):
            chapter_title = chapter.get("title", f"第{idx}章")
            filename = f"chapter_{idx:03d}.xhtml"
            chapter_html = self._render_chapter_xhtml(
                novel_title=novel_title,
                chapter=chapter,
                chapter_title=chapter_title,
                chapter_index=idx,
                total_chapters=len(chapters),
            )
            content_files.append({
                "id": f"ch{idx:03d}",
                "filename": filename,
                "title": chapter_title,
                "html": chapter_html,
            })

        self._image_manifest_ids = {}
        self._image_counter = 0

        if os.path.exists(epub_path):
            try:
                os.remove(epub_path)
            except OSError as exc:
                logger.warning("删除旧 epub 失败: %s", exc)

        with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:

            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

            container_xml = self._build_container_xml()
            zf.writestr("META-INF/container.xml", container_xml)

            image_entries = self._collect_images(content_files)
            for img_filename, img_src_path in image_entries:
                zf.write(img_src_path, img_filename)

            opf_content = self._build_content_opf(
                novel_title=novel_title,
                book_uid=book_id,
                content_files=content_files,
                image_entries=image_entries,
            )
            zf.writestr("OEBPS/content.opf", opf_content)

            ncx_content = self._build_toc_ncx(
                novel_title=novel_title,
                book_uid=book_id,
                content_files=content_files,
            )
            zf.writestr("OEBPS/toc.ncx", ncx_content)

            nav_content = self._build_nav_xhtml(
                novel_title=novel_title,
                content_files=content_files,
            )
            zf.writestr("OEBPS/nav.xhtml", nav_content)

            zf.writestr("OEBPS/style.css", self._get_epub_css())

            for entry in content_files:
                zf.writestr(f"OEBPS/{entry['filename']}", entry["html"])

            zf.writestr("OEBPS/cover.xhtml", self._build_cover_xhtml(novel_title))

        logger.info("EPUB 导出成功: %s", epub_path)
        return epub_path

    def _render_chapter_xhtml(
        self,
        novel_title: str,
        chapter: dict,
        chapter_title: str,
        chapter_index: int,
        total_chapters: int,
    ) -> str:
        sections = chapter.get("children", [])
        content_parts = []

        if sections:
            for sec_idx, section in enumerate(sections):
                section_title = section.get("title", "未命名小节")
                content_parts.append(
                    f'  <section class="section-block">\n'
                    f'    <h2 class="section-title">{html.escape(section_title)}</h2>'
                )

                has_content = False
                for scene in section.get("children", []):
                    scene_html = self._build_scene_xhtml(scene)
                    if scene_html:
                        has_content = True
                        content_parts.append(f'    <div class="scene-body">{scene_html}</div>')

                if not has_content:
                    content_parts.append(
                        '    <p class="empty-content">本节暂无正文内容。</p>'
                    )
                content_parts.append("  </section>")
        else:
            content_parts.append(
                '  <section class="section-block">\n'
                '    <p class="empty-content">本章暂无内容。</p>\n'
                '  </section>'
            )

        body_content = "\n".join(content_parts)

        prev_nav = ""
        next_nav = ""
        if chapter_index > 1:
            prev_nav = f'<a class="nav-link" href="chapter_{chapter_index - 1:03d}.xhtml">← 上一章</a>'
        if chapter_index < total_chapters:
            next_nav = f'<a class="nav-link" href="chapter_{chapter_index + 1:03d}.xhtml">下一章 →</a>'

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">
<head>
  <meta charset="UTF-8"/>
  <title>{html.escape(f"{novel_title} - {chapter_title}")}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body epub:type="bodymatter">
  <header class="chapter-header">
    <h1 class="chapter-title">{html.escape(chapter_title)}</h1>
    <nav class="chapter-nav" epub:type="page-list">
      {prev_nav}
      <a class="nav-link" href="nav.xhtml">目录</a>
      {next_nav}
    </nav>
  </header>
{body_content}
  <footer class="chapter-footer">
    <nav class="chapter-nav">
      {prev_nav}
      <a class="nav-link" href="nav.xhtml">目录</a>
      {next_nav}
    </nav>
  </footer>
</body>
</html>"""

    def _build_scene_xhtml(self, scene: dict) -> str:
        rel_path = scene.get("file_path")
        md_text = ""
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    md_text = f.read()

        if not md_text:
            return ""

        html_text = markdown.markdown(md_text, extensions=["extra"])
        return html_text

    def _collect_images(self, content_files: list) -> list:
        img_pattern = re.compile(r'<img[^>]+src="([^"]+)"')
        collected = {}
        img_dir = os.path.join(self.workspace.text_path, "images")

        for entry in content_files:
            for match in img_pattern.finditer(entry["html"]):
                src = match.group(1)
                if src.startswith("http://") or src.startswith("https://"):
                    continue
                if src.startswith("data:"):
                    continue

                src_normalized = src.replace("\\", "/")
                basename = os.path.basename(src_normalized)

                if basename in collected:
                    continue

                src_path = os.path.join(img_dir, basename) if not os.path.isabs(src) else src
                if not os.path.exists(src_path):
                    candidate = os.path.join(img_dir, os.path.basename(src))
                    if os.path.exists(candidate):
                        src_path = candidate
                    else:
                        continue

                epub_path = f"OEBPS/images/{basename}"
                collected[basename] = (epub_path, src_path)

                self._image_counter += 1
                img_id = f"img{self._image_counter:03d}"
                self._image_manifest_ids[basename] = img_id

        return [(v[0], v[1]) for v in collected.values()]

    def _build_container_xml(self) -> str:
        return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    def _build_content_opf(
        self,
        novel_title: str,
        book_uid: str,
        content_files: list,
        image_entries: list,
    ) -> str:
        now = self._now_iso()
        safe_title = html.escape(novel_title)
        opf = ET.Element(
            "package",
            {
                "xmlns": "http://www.idpf.org/2007/opf",
                "unique-identifier": "book-id",
                "version": "3.0",
                "xml:lang": "zh-CN",
            },
        )

        metadata = ET.SubElement(
            opf,
            "metadata",
            {"xmlns:dc": "http://purl.org/dc/elements/1.1/"},
        )
        ET.SubElement(metadata, "dc:identifier", {"id": "book-id"}).text = book_uid
        ET.SubElement(metadata, "dc:title").text = safe_title
        ET.SubElement(metadata, "dc:language").text = "zh-CN"
        ET.SubElement(metadata, "dc:date", {"opf:event": "modification"}).text = now
        ET.SubElement(metadata, "meta", {"property": "dcterms:modified"}).text = now

        manifest = ET.SubElement(opf, "manifest")
        ET.SubElement(manifest, "item", {
            "id": "ncx",
            "href": "toc.ncx",
            "media-type": "application/x-dtbncx+xml",
        })
        ET.SubElement(manifest, "item", {
            "id": "nav",
            "href": "nav.xhtml",
            "media-type": "application/xhtml+xml",
            "properties": "nav",
        })
        ET.SubElement(manifest, "item", {
            "id": "css",
            "href": "style.css",
            "media-type": "text/css",
        })
        ET.SubElement(manifest, "item", {
            "id": "cover",
            "href": "cover.xhtml",
            "media-type": "application/xhtml+xml",
        })

        for entry in content_files:
            ET.SubElement(manifest, "item", {
                "id": entry["id"],
                "href": entry["filename"],
                "media-type": "application/xhtml+xml",
                "properties": "scripted",
            })

        for epub_img_path, _ in image_entries:
            basename = os.path.basename(epub_img_path)
            img_id = self._image_manifest_ids.get(basename, "img000")
            ext = os.path.splitext(basename)[1].lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".svg": "image/svg+xml",
                ".bmp": "image/bmp",
            }
            mime = mime_map.get(ext, "image/jpeg")
            ET.SubElement(manifest, "item", {
                "id": img_id,
                "href": f"images/{basename}",
                "media-type": mime,
            })

        spine = ET.SubElement(opf, "spine", {"toc": "ncx"})
        ET.SubElement(spine, "itemref", {"idref": "cover", "linear": "no"})
        ET.SubElement(spine, "itemref", {"idref": "nav", "linear": "no"})
        for entry in content_files:
            ET.SubElement(spine, "itemref", {"idref": entry["id"]})

        raw = ET.tostring(opf, encoding="unicode", method="xml")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + raw

    def _build_toc_ncx(
        self,
        novel_title: str,
        book_uid: str,
        content_files: list,
    ) -> str:
        safe_title = html.escape(novel_title)
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN"',
            '  "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">',
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">',
            "  <head>",
            f'    <meta name="dtb:uid" content="{html.escape(book_uid)}"/>',
            f'    <meta name="dtb:depth" content="1"/>',
            f"    <meta name=\"dtb:totalPageCount\" content=\"0\"/>",
            f"    <meta name=\"dtb:maxPageNumber\" content=\"0\"/>",
            "  </head>",
            f"  <docTitle><text>{safe_title}</text></docTitle>",
            "  <navMap>",
        ]

        for idx, entry in enumerate(content_files, start=1):
            parts.append(
                f'    <navPoint id="navPoint-{idx}" playOrder="{idx}">'
            )
            parts.append(
                f'      <navLabel><text>{html.escape(entry["title"])}</text></navLabel>'
            )
            parts.append(
                f'      <content src="{entry["filename"]}"/>'
            )
            parts.append("    </navPoint>")

        parts.append("  </navMap>")
        parts.append("</ncx>")
        return "\n".join(parts)

    def _build_nav_xhtml(self, novel_title: str, content_files: list) -> str:
        safe_title = html.escape(novel_title)
        items = []
        for entry in content_files:
            items.append(
                f'      <li><a href="{entry["filename"]}">{html.escape(entry["title"])}</a></li>'
            )
        item_list = "\n".join(items) if items else "<li>暂无章节</li>"

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">
<head>
  <meta charset="UTF-8"/>
  <title>目录 - {safe_title}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body epub:type="frontmatter">
  <header class="toc-header">
    <h1>目录</h1>
  </header>
  <nav epub:type="toc" id="toc">
    <ol epub:type="list">
{item_list}
    </ol>
  </nav>
</body>
</html>"""

    def _build_cover_xhtml(self, novel_title: str) -> str:
        safe_title = html.escape(novel_title)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
<head>
  <meta charset="UTF-8"/>
  <title>封面</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body epub:type="cover" class="cover-page">
  <section class="cover-content">
    <h1 class="cover-title">{safe_title}</h1>
    <p class="cover-subtitle">由 Novel-Maker 生成</p>
  </section>
</body>
</html>"""

    def _get_epub_css(self) -> str:
        return """@charset "UTF-8";

body {
  font-family: serif;
  line-height: 1.8;
  margin: 0;
  padding: 0;
  color: #2c3e50;
}

.cover-page {
  text-align: center;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
}

.cover-title {
  font-size: 2.4em;
  font-weight: bold;
  margin-bottom: 0.5em;
}

.cover-subtitle {
  font-size: 1.1em;
  color: #7f8c8d;
}

.toc-header {
  text-align: center;
  border-bottom: 2px solid #3498db;
  padding-bottom: 0.5em;
  margin-bottom: 1.5em;
}

.toc-header h1 {
  font-size: 1.8em;
  color: #3498db;
}

nav#toc ol {
  list-style: none;
  padding: 0;
  margin: 0;
}

nav#toc li {
  margin: 0.6em 0;
}

nav#toc a {
  display: block;
  padding: 0.6em 0.8em;
  text-decoration: none;
  color: #2c3e50;
  border-left: 3px solid transparent;
  transition: border-color 0.2s;
}

nav#toc a:hover {
  border-left-color: #3498db;
  color: #3498db;
}

.chapter-header {
  text-align: center;
  border-bottom: 2px solid #bdc3c7;
  padding-bottom: 0.8em;
  margin-bottom: 2em;
}

.chapter-title {
  font-size: 1.8em;
  font-weight: bold;
}

.chapter-nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 0.8em 0;
  font-size: 0.9em;
}

.chapter-nav .nav-link {
  text-decoration: none;
  color: #3498db;
}

.section-block {
  margin-bottom: 2em;
}

.section-title {
  font-size: 1.4em;
  border-left: 4px solid #3498db;
  padding-left: 0.6em;
  margin: 1.5em 0 0.8em;
  color: #34495e;
}

.scene-body {
  font-size: 1.05em;
  line-height: 1.9;
  text-align: justify;
  text-indent: 2em;
  margin-bottom: 1.2em;
}

.scene-body p {
  margin: 0.8em 0;
}

.scene-body img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;
}

.empty-content {
  color: #95a5a6;
  font-style: italic;
  text-align: center;
  padding: 2em;
}

.chapter-footer {
  margin-top: 3em;
  padding-top: 1em;
  border-top: 1px solid #ecf0f1;
}
"""

    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def encrypt_to_zip(self, epub_path: str, password: str) -> str:
        import pyzipper

        zip_path = epub_path.replace(".epub", "_加密.zip")
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError as exc:
                logger.warning("删除旧加密 zip 失败: %s", exc)

        epub_basename = os.path.basename(epub_path)
        with pyzipper.AESZipFile(
            zip_path,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.write(epub_path, arcname=epub_basename)

        logger.info("加密 ZIP 创建成功: %s", zip_path)
        return zip_path
