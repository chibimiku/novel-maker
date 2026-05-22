import html
import json
import logging
import os
import re
import shutil

logger = logging.getLogger(__name__)

try:
    import markdown
except ImportError:
    markdown = None


class HtmlExporter:
    def __init__(self, workspace_manager):
        self.workspace = workspace_manager
        self.www_path = os.path.join(self.workspace.workspace_path, "www")
        self.chapter_pages_dir = os.path.join(self.www_path, "chapters")

    def export(self) -> str:
        if markdown is None:
            raise ImportError("缺少 markdown 库。请在终端执行: pip install markdown")

        os.makedirs(self.www_path, exist_ok=True)
        os.makedirs(self.chapter_pages_dir, exist_ok=True)
        self._clear_generated_chapter_pages()
        self._sync_images()

        tree_data = self.workspace.load_outline_tree()
        novel_title = tree_data.get("project_name", "未命名小说")
        chapters = tree_data.get("nodes", [])

        chapter_entries = []
        for idx, chapter in enumerate(chapters, start=1):
            chapter_title = chapter.get("title", f"第{idx}章")
            chapter_filename = self._build_chapter_filename(idx, chapter_title)
            chapter_output = os.path.join(self.chapter_pages_dir, chapter_filename)
            chapter_entry = {
                "title": chapter_title,
                "filename": chapter_filename,
                "path": chapter_output,
                "sections": chapter.get("children", []),
            }
            chapter_entries.append(chapter_entry)

        for chapter_index, chapter in enumerate(chapters, start=1):
            chapter_entry = chapter_entries[chapter_index - 1]
            chapter_html = self._render_chapter_page(
                novel_title=novel_title,
                chapters=chapters,
                chapter_entries=chapter_entries,
                current_chapter=chapter,
                current_chapter_index=chapter_index,
            )
            with open(chapter_entry["path"], "w", encoding="utf-8") as f:
                f.write(chapter_html)

        self._write_php_api_script()

        output_file = os.path.join(self.www_path, "index.html")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(self._render_index_page(novel_title, chapter_entries))

        return output_file

    def _sync_images(self):
        img_src = os.path.join(self.workspace.text_path, "images")
        img_dest = os.path.join(self.www_path, "images")
        if os.path.exists(img_src):
            shutil.copytree(img_src, img_dest, dirs_exist_ok=True)

    def _clear_generated_chapter_pages(self):
        if not os.path.isdir(self.chapter_pages_dir):
            return
        for name in os.listdir(self.chapter_pages_dir):
            if name.lower().endswith(".html"):
                try:
                    os.remove(os.path.join(self.chapter_pages_dir, name))
                except OSError as exc:
                    logger.warning("删除旧章节页面失败 %s: %s", name, exc)

    def _write_php_api_script(self):
        api_dir = os.path.join(self.workspace.workspace_path, "www-php", "api")
        os.makedirs(api_dir, exist_ok=True)
        php_file = os.path.join(api_dir, "issues.php")
        with open(php_file, "w", encoding="utf-8") as f:
            f.write(self._get_php_api_script())

    def _build_chapter_filename(self, index: int, title: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_").lower()
        if not slug:
            slug = "chapter"
        return f"chapter_{index:03d}_{slug}.html"

    def _render_chapter_page(
        self,
        novel_title: str,
        chapters: list,
        chapter_entries: list,
        current_chapter: dict,
        current_chapter_index: int,
    ) -> str:
        current_entry = chapter_entries[current_chapter_index - 1]
        current_title = current_entry["title"]
        current_sections = current_chapter.get("children", [])

        nav_html = self._build_chapter_nav_html(
            chapters=chapters,
            chapter_entries=chapter_entries,
            current_chapter_index=current_chapter_index,
            current_sections=current_sections,
        )
        content_html = self._build_section_content_html(current_title, current_sections)

        return (
            self._get_chapter_template()
            .replace("{page_title}", html.escape(f"{novel_title} - {current_title}"))
            .replace("{novel_title}", html.escape(novel_title))
            .replace("{current_chapter_title}", html.escape(current_title))
            .replace("{nav_html}", nav_html)
            .replace("{content_html}", content_html)
            .replace("{index_href}", "../index.html")
        )

    def _build_chapter_nav_html(
        self,
        chapters: list,
        chapter_entries: list,
        current_chapter_index: int,
        current_sections: list,
    ) -> str:
        parts = []
        section_counter = 0

        for idx, (chapter, entry) in enumerate(zip(chapters, chapter_entries), start=1):
            chapter_title = html.escape(chapter.get("title", f"第{idx}章"))
            chapter_href = html.escape(entry["filename"])
            current_class = " current" if idx == current_chapter_index else ""
            parts.append(
                f'<a class="nav-chapter-link{current_class}" href="{chapter_href}">{chapter_title}</a>'
            )
            if idx != current_chapter_index:
                continue

            if not current_sections:
                parts.append('<div class="nav-empty">本章暂无小节</div>')
                continue

            for section in current_sections:
                section_title = html.escape(section.get("title", "未命名小节"))
                section_id = f"sec_{section_counter}"
                parts.append(
                    f'<a class="nav-section" data-target="{section_id}" '
                    f'onclick="showSection(\'{section_id}\'); return false;" href="#{section_id}">{section_title}</a>'
                )
                section_counter += 1

        return "\n".join(parts)

    def _build_section_content_html(self, chapter_title: str, sections: list) -> str:
        if not sections:
            empty_title = html.escape(chapter_title)
            return (
                '<article class="section-content active">'
                f'<h1 class="section-title">{empty_title}</h1>'
                '<div class="empty-content">本章暂无可展示内容。</div>'
                "</article>"
            )

        content_parts = []
        for section_counter, section in enumerate(sections):
            section_title = section.get("title", "未命名小节")
            section_id = f"sec_{section_counter}"
            section_html = [
                f'<article id="{section_id}" class="section-content">',
                f'<h1 class="section-title">{html.escape(section_title)}</h1>',
            ]

            has_scene_content = False
            for scene in section.get("children", []):
                scene_html = self._build_scene_html(chapter_title, section_title, scene)
                if scene_html:
                    has_scene_content = True
                    section_html.append(scene_html)

            if not has_scene_content:
                section_html.append('<div class="empty-content">本节暂无正文内容。</div>')

            section_html.append("</article>")
            content_parts.append("\n".join(section_html))

        return "\n".join(content_parts)

    def _build_scene_html(self, chapter_title: str, section_title: str, scene: dict) -> str:
        rel_path = scene.get("file_path")
        md_text = ""
        if rel_path:
            full_path = os.path.join(self.workspace.text_path, rel_path)
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8") as f:
                    md_text = f.read()

        if not md_text:
            return ""

        scene_id = html.escape(scene.get("id", ""))
        scene_title = scene.get("title", "")
        node_path = html.escape(f"{chapter_title} > {section_title} > {scene_title}")
        html_text = markdown.markdown(md_text, extensions=["extra"])
        return (
            f'<div class="scene-body" data-node-id="{scene_id}" data-node-path="{node_path}">'
            f"{html_text}</div>"
        )

    def _render_index_page(self, novel_title: str, chapter_entries: list) -> str:
        chapter_cards = []
        if chapter_entries:
            for idx, entry in enumerate(chapter_entries, start=1):
                section_count = len(entry["sections"])
                label = f"{section_count} 个小节" if section_count else "暂无小节"
                chapter_cards.append(
                    '<a class="chapter-card" href="chapters/{filename}">'
                    '<div class="chapter-card-index">第 {index} 章</div>'
                    '<div class="chapter-card-title">{title}</div>'
                    '<div class="chapter-card-meta">{meta}</div>'
                    "</a>".format(
                        filename=html.escape(entry["filename"]),
                        index=idx,
                        title=html.escape(entry["title"]),
                        meta=html.escape(label),
                    )
                )
            cards_html = "\n".join(chapter_cards)
        else:
            cards_html = '<div class="empty-state">当前还没有章节可导出。</div>'

        first_chapter_href = ""
        if chapter_entries:
            first_chapter_href = f' href="chapters/{html.escape(chapter_entries[0]["filename"])}"'

        return (
            self._get_index_template()
            .replace("{page_title}", html.escape(f"{novel_title} - 网页目录"))
            .replace("{novel_title}", html.escape(novel_title))
            .replace("{chapter_cards}", cards_html)
            .replace("{first_chapter_href}", first_chapter_href)
        )

    def _get_index_template(self) -> str:
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <style>
        :root { --bg: #f7f9fb; --card: #ffffff; --text: #243447; --muted: #6b7a89; --accent: #3498db; --border: #e2e8f0; }
        * { box-sizing: border-box; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: linear-gradient(180deg, #f7f9fb 0%, #eef3f8 100%); color: var(--text); }
        .page { max-width: 1100px; margin: 0 auto; padding: 48px 24px 64px; }
        .hero { background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 32px; box-shadow: 0 12px 30px rgba(36,52,71,0.08); }
        .eyebrow { color: var(--accent); font-weight: bold; letter-spacing: 0.08em; text-transform: uppercase; font-size: 0.85rem; }
        h1 { margin: 10px 0 12px; font-size: 2.4rem; }
        .subtitle { margin: 0; color: var(--muted); line-height: 1.8; }
        .actions { margin-top: 20px; }
        .primary-btn { display: inline-block; background: var(--accent); color: #fff; padding: 12px 18px; border-radius: 10px; text-decoration: none; font-weight: bold; }
        .chapter-grid { margin-top: 28px; display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 18px; }
        .chapter-card { display: block; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; text-decoration: none; color: inherit; box-shadow: 0 8px 20px rgba(36,52,71,0.06); transition: transform 0.18s ease, box-shadow 0.18s ease; }
        .chapter-card:hover { transform: translateY(-2px); box-shadow: 0 14px 26px rgba(36,52,71,0.10); }
        .chapter-card-index { color: var(--accent); font-size: 0.9rem; font-weight: bold; margin-bottom: 8px; }
        .chapter-card-title { font-size: 1.1rem; font-weight: bold; line-height: 1.5; }
        .chapter-card-meta { margin-top: 10px; color: var(--muted); font-size: 0.92rem; }
        .empty-state { margin-top: 28px; background: var(--card); border: 1px dashed var(--border); border-radius: 16px; padding: 28px; color: var(--muted); text-align: center; }
        @media (max-width: 768px) {
            .page { padding: 24px 16px 40px; }
            .hero { padding: 24px 20px; }
            h1 { font-size: 1.8rem; }
        }
    </style>
</head>
<body>
    <main class="page">
        <section class="hero">
            <div class="eyebrow">WWW 浏览模式</div>
            <h1>{novel_title}</h1>
            <p class="subtitle">已按章节拆分为独立网页文件，避免整本小说导出成超大单页。点击章节即可进入对应页面，每章内仍按小节聚合展示三级场景正文。</p>
            <div class="actions">
                <a class="primary-btn"{first_chapter_href}>开始阅读</a>
            </div>
        </section>
        <section class="chapter-grid">
            {chapter_cards}
        </section>
    </main>
</body>
</html>"""

    def _get_chapter_template(self) -> str:
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{page_title}</title>
    <style>
        :root { --bg: #fdfdfd; --text: #2c3e50; --sidebar-bg: #f4f6f8; --border: #e0e6ed; --accent: #3498db; }
        * { box-sizing: border-box; }
        body { margin: 0; display: flex; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; height: 100vh; background: var(--bg); color: var(--text); overflow: hidden; }
        #sidebar { width: 300px; background: var(--sidebar-bg); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 1000; transition: transform 0.3s ease; }
        .sidebar-header { padding: 20px 15px; font-weight: bold; font-size: 1.1em; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #fff; gap: 10px; }
        .sidebar-header-main { display: flex; flex-direction: column; min-width: 0; }
        .sidebar-header-main span:last-child { font-size: 0.9em; color: #7f8c8d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .back-link { display: inline-block; margin: 12px 15px 0; color: var(--accent); text-decoration: none; font-size: 0.92em; }
        #nav-container { flex: 1; overflow-y: auto; padding: 12px 0 20px; }
        .nav-chapter-link { display: block; padding: 11px 15px; color: var(--text); text-decoration: none; font-weight: bold; border-left: 3px solid transparent; }
        .nav-chapter-link:hover { background: #eef2f5; }
        .nav-chapter-link.current { color: var(--accent); border-left-color: var(--accent); background: #eef2f5; }
        .nav-section { display: block; padding: 9px 15px 9px 34px; color: var(--text); text-decoration: none; cursor: pointer; border-left: 3px solid transparent; font-size: 0.98em; transition: background 0.2s; }
        .nav-section:hover { background: #eef2f5; }
        .nav-section.active { background: #eef2f5; border-left-color: var(--accent); color: var(--accent); font-weight: bold; }
        .nav-empty { padding: 10px 15px 10px 34px; color: #7f8c8d; font-size: 0.92em; }
        #main-wrapper { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        #mobile-header { display: none; background: #fff; border-bottom: 1px solid var(--border); padding: 10px 15px; align-items: center; justify-content: space-between; z-index: 900; box-shadow: 0 1px 3px rgba(0,0,0,0.05); gap: 12px; }
        .mobile-title { font-weight: bold; font-size: 1.05em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
        .hamburger { font-size: 24px; cursor: pointer; background: none; border: none; padding: 5px; color: var(--text); }
        #content-area { flex: 1; overflow-y: auto; padding: 30px; scroll-behavior: smooth; }
        .section-content { display: none; max-width: 820px; margin: 0 auto; animation: fadeIn 0.4s ease; }
        .section-content.active { display: block; }
        .section-title { font-size: 2em; margin-bottom: 1em; text-align: center; border-bottom: 2px solid var(--border); padding-bottom: 15px; }
        .scene-body { font-size: 1.15em; line-height: 1.8; margin-bottom: 40px; text-align: justify; }
        .scene-body p { margin-bottom: 1.2em; }
        .scene-body img { max-width: 100%; height: auto; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin: 10px 0; }
        .empty-content { max-width: 820px; margin: 0 auto; padding: 36px 28px; border: 1px dashed var(--border); border-radius: 12px; color: #7f8c8d; background: #fff; text-align: center; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        #overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 999; backdrop-filter: blur(2px); }
        .close-btn { display: none; font-size: 28px; cursor: pointer; background: none; border: none; color: #999; line-height: 1; }
        #issue-fab { position: fixed; bottom: 24px; right: 24px; width: 52px; height: 52px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 24px; border: none; cursor: pointer; z-index: 1100; box-shadow: 0 4px 14px rgba(52,152,219,0.4); display: flex; align-items: center; justify-content: center; transition: transform 0.2s, opacity 0.2s; }
        #issue-fab:active { transform: scale(0.92); }
        #issue-fab.hidden { opacity: 0; pointer-events: none; transform: scale(0.6); }
        #issue-panel { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; z-index: 1200; border-top: 1px solid var(--border); box-shadow: 0 -4px 20px rgba(0,0,0,0.15); transform: translateY(100%); transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1); max-height: 70vh; display: flex; flex-direction: column; }
        #issue-panel.open { transform: translateY(0); }
        .issue-panel-header { padding: 14px 18px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; font-weight: bold; font-size: 1.05em; background: #fafbfc; }
        .issue-close-btn { font-size: 24px; cursor: pointer; background: none; border: none; color: #999; line-height: 1; padding: 0 4px; }
        .issue-panel-body { padding: 16px 18px; overflow-y: auto; flex: 1; }
        .issue-node-info { font-size: 0.85em; color: #7f8c8d; margin-bottom: 10px; word-break: break-all; }
        #issue-requirement { width: 100%; min-height: 100px; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-size: 0.95em; font-family: inherit; resize: vertical; line-height: 1.6; }
        #issue-requirement:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(52,152,219,0.15); }
        .issue-quote-section { margin-top: 10px; background: #f8f9fa; border-radius: 8px; padding: 10px 12px; border-left: 3px solid #e0c36a; }
        .issue-quote-label { font-size: 0.8em; color: #95a5a6; margin-bottom: 4px; }
        .issue-quote-text { font-size: 0.85em; color: #555; line-height: 1.5; max-height: 80px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; }
        .issue-quote-clear { font-size: 0.78em; color: #e74c3c; background: none; border: none; cursor: pointer; padding: 2px 0; margin-top: 4px; }
        #issue-submit-btn { display: block; width: 100%; margin-top: 14px; padding: 12px; background: var(--accent); color: #fff; font-size: 1em; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; transition: background 0.2s; }
        #issue-submit-btn:active { background: #2980b9; }
        #issue-submit-btn:disabled { background: #bdc3c7; cursor: not-allowed; }
        #issue-toast { margin-top: 10px; text-align: center; font-size: 0.9em; min-height: 22px; }
        #issue-toast.success { color: #27ae60; }
        #issue-toast.error { color: #e74c3c; }
        @media (max-width: 768px) {
            #sidebar { position: fixed; left: 0; top: 0; bottom: 0; transform: translateX(-100%); }
            #sidebar.open { transform: translateX(0); box-shadow: 4px 0 15px rgba(0,0,0,0.15); }
            #mobile-header { display: flex; }
            #content-area { padding: 20px 15px; }
            .close-btn { display: block; }
            #overlay.show { display: block; }
            .section-title { font-size: 1.5em; }
        }
    </style>
</head>
<body>
    <div id="overlay" onclick="toggleSidebar()"></div>
    <div id="sidebar">
        <div class="sidebar-header">
            <div class="sidebar-header-main">
                <span>{novel_title}</span>
                <span>{current_chapter_title}</span>
            </div>
            <button class="close-btn" onclick="toggleSidebar()">×</button>
        </div>
        <a class="back-link" href="{index_href}">返回章节目录</a>
        <div id="nav-container">
            {nav_html}
        </div>
    </div>
    <div id="main-wrapper">
        <div id="mobile-header">
            <button class="hamburger" onclick="toggleSidebar()">☰</button>
            <span class="mobile-title">{current_chapter_title}</span>
            <a class="back-link" href="{index_href}" style="margin:0;">目录</a>
        </div>
        <div id="content-area">
            {content_html}
        </div>
    </div>
    <button id="issue-fab" class="hidden" onclick="toggleIssuePanel()" title="提交修改建议">💡</button>
    <div id="issue-panel">
        <div class="issue-panel-header">
            <span>📝 提交修改建议</span>
            <button class="issue-close-btn" onclick="toggleIssuePanel()">×</button>
        </div>
        <div class="issue-panel-body">
            <div class="issue-node-info" id="issue-node-path-display"></div>
            <textarea id="issue-requirement" placeholder="描述你希望怎么修改这段内容，比如：语气太生硬、需要增加环境描写、战斗不够激烈..."></textarea>
            <div class="issue-quote-section" id="issue-quote-section" style="display:none">
                <div class="issue-quote-label">📎 引用的原文：</div>
                <div class="issue-quote-text" id="issue-quote-preview"></div>
                <button class="issue-quote-clear" onclick="clearQuote()">清除引用</button>
            </div>
            <button id="issue-submit-btn" onclick="submitIssue()">提交建议</button>
            <div id="issue-toast"></div>
        </div>
    </div>
    <script>
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }

        function showSection(id) {
            document.querySelectorAll('.section-content').forEach(function(el) {
                el.classList.remove('active');
            });
            document.querySelectorAll('.nav-section').forEach(function(el) {
                el.classList.remove('active');
            });

            var target = document.getElementById(id);
            if (target) {
                target.classList.add('active');
            }

            var link = document.querySelector('.nav-section[data-target="' + id + '"]');
            if (link) {
                link.classList.add('active');
            }

            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
                document.getElementById('overlay').classList.remove('show');
            }

            document.getElementById('content-area').scrollTo(0, 0);
            if (window.location.hash !== '#' + id) {
                history.replaceState(null, '', '#' + id);
            }
            updateIssueContext();
        }

        var currentIssueNodeId = null;
        var currentIssueNodePath = '';
        var selectedQuoteText = '';

        document.querySelectorAll('.scene-body').forEach(function(el) {
            el.addEventListener('click', function() {
                currentIssueNodeId = this.dataset.nodeId || null;
                currentIssueNodePath = this.dataset.nodePath || '';
                document.getElementById('issue-node-path-display').textContent =
                    currentIssueNodePath ? '📍 ' + currentIssueNodePath : '';
                if (currentIssueNodeId) {
                    document.getElementById('issue-fab').classList.remove('hidden');
                }
            });
        });

        document.addEventListener('mouseup', captureSelection);
        document.addEventListener('touchend', function() {
            setTimeout(captureSelection, 100);
        });

        function captureSelection() {
            var sel = window.getSelection();
            var text = sel.toString().trim();
            if (text.length > 0) {
                selectedQuoteText = text;
                document.getElementById('issue-quote-preview').textContent = text;
                document.getElementById('issue-quote-section').style.display = 'block';
            }
        }

        function clearQuote() {
            selectedQuoteText = '';
            document.getElementById('issue-quote-section').style.display = 'none';
            window.getSelection().removeAllRanges();
        }

        function toggleIssuePanel() {
            var panel = document.getElementById('issue-panel');
            if (!currentIssueNodeId && !panel.classList.contains('open')) {
                showToast('请先点击要修改的场景内容', true);
                return;
            }
            panel.classList.toggle('open');
            if (panel.classList.contains('open')) {
                document.getElementById('issue-node-path-display').textContent =
                    currentIssueNodePath ? '📍 ' + currentIssueNodePath : '';
            }
        }

        function updateIssueContext() {
            var activeSection = document.querySelector('.section-content.active');
            if (!activeSection) {
                currentIssueNodeId = null;
                return;
            }
            var sceneBody = activeSection.querySelector('.scene-body');
            if (sceneBody) {
                currentIssueNodeId = sceneBody.dataset.nodeId || null;
                currentIssueNodePath = sceneBody.dataset.nodePath || '';
                if (currentIssueNodeId) {
                    document.getElementById('issue-fab').classList.remove('hidden');
                }
            } else {
                currentIssueNodeId = null;
                currentIssueNodePath = '';
                document.getElementById('issue-fab').classList.add('hidden');
            }
        }

        function submitIssue() {
            var requirement = document.getElementById('issue-requirement').value.trim();
            if (!requirement) { showToast('请填写修改需求', true); return; }
            if (!currentIssueNodeId) { showToast('请先点击要修改的场景内容', true); return; }

            var btn = document.getElementById('issue-submit-btn');
            btn.disabled = true;
            btn.textContent = '提交中...';

            var apiUrl = window.location.origin + '/www-php/api/issues.php';

            fetch(apiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    node_id: currentIssueNodeId,
                    requirement: requirement,
                    quoted_text: selectedQuoteText,
                    submitted_at: new Date().toISOString()
                })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.ok) {
                    showToast('✅ 建议已提交！', false);
                    document.getElementById('issue-requirement').value = '';
                    clearQuote();
                } else {
                    showToast('❌ 提交失败: ' + (data.error || '未知错误'), true);
                }
            })
            .catch(function() {
                showToast('❌ 网络错误，请确保已连接', true);
            })
            .finally(function() {
                btn.disabled = false;
                btn.textContent = '提交建议';
            });
        }

        function showToast(msg, isError) {
            var toast = document.getElementById('issue-toast');
            toast.textContent = msg;
            toast.className = isError ? 'error' : 'success';
            setTimeout(function() {
                toast.textContent = '';
                toast.className = '';
            }, 3000);
        }

        window.onload = function() {
            var hash = window.location.hash ? window.location.hash.slice(1) : '';
            var hashedNav = hash ? document.querySelector('.nav-section[data-target="' + hash + '"]') : null;
            if (hashedNav) {
                showSection(hash);
                return;
            }

            var firstNav = document.querySelector('.nav-section');
            if (firstNav) {
                showSection(firstNav.dataset.target);
                return;
            }

            var firstContent = document.querySelector('.section-content');
            if (firstContent) {
                firstContent.classList.add('active');
            }
            updateIssueContext();
        };
    </script>
</body>
</html>"""

    def _get_php_api_script(self) -> str:
        return """<?php
header('Content-Type: application/json; charset=utf-8');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header('Access-Control-Allow-Methods: POST, OPTIONS');
    header('Access-Control-Allow-Headers: Content-Type');
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['ok' => false, 'error' => '仅支持POST'], JSON_UNESCAPED_UNICODE);
    exit;
}

$input = json_decode(file_get_contents('php://input'), true);
if (!$input || empty($input['node_id']) || empty($input['requirement'])) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => '缺少必填字段(node_id, requirement)'], JSON_UNESCAPED_UNICODE);
    exit;
}

$issue = [
    'issue_id'    => bin2hex(random_bytes(16)),
    'node_id'     => $input['node_id'],
    'requirement' => $input['requirement'],
    'quoted_text' => $input['quoted_text'] ?? '',
    'submitted_at'=> $input['submitted_at'] ?? date('c'),
    'status'      => 'pending'
];

$issuesDir = __DIR__ . '/../../系统数据/mobile_issues';
if (!is_dir($issuesDir)) {
    mkdir($issuesDir, 0777, true);
}

$filePath = $issuesDir . '/' . $issue['issue_id'] . '.json';
file_put_contents(
    $filePath,
    json_encode($issue, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT)
);

echo json_encode(['ok' => true, 'issue_id' => $issue['issue_id']], JSON_UNESCAPED_UNICODE);
"""
