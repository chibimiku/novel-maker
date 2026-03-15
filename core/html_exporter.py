import os
import json
import shutil
import logging

logger = logging.getLogger(__name__)

try:
    import markdown
except ImportError:
    markdown = None

class HtmlExporter:
    def __init__(self, workspace_manager):
        self.workspace = workspace_manager
        self.www_path = os.path.join(self.workspace.workspace_path, "www")

    def export(self) -> str:
        if markdown is None:
            raise ImportError("缺少 markdown 库。请在终端执行: pip install markdown")

        # 1. 创建 www 目录并同步图片资源
        os.makedirs(self.www_path, exist_ok=True)
        img_src = os.path.join(self.workspace.text_path, "images")
        img_dest = os.path.join(self.www_path, "images")
        if os.path.exists(img_src):
            shutil.copytree(img_src, img_dest, dirs_exist_ok=True)

        # 2. 加载大纲数据
        tree_data = self.workspace.load_outline_tree()
        novel_title = tree_data.get("project_name", "未命名小说")
        nodes = tree_data.get("nodes", [])

        nav_html = ""
        content_html = ""
        section_counter = 0

        # 3. 遍历生成结构
        for chapter in nodes:  # Level 1: 章
            chapter_title = chapter.get("title", "未命名章节")
            nav_html += f'<div class="nav-chapter">{chapter_title}</div>\n'
            
            for section in chapter.get("children", []):  # Level 2: 节
                section_title = section.get("title", "未命名小节")
                section_id = f"sec_{section_counter}"
                
                # 左侧导航条目
                nav_html += f'<a class="nav-section" data-target="{section_id}" onclick="showSection(\'{section_id}\')">{section_title}</a>\n'
                
                # 右侧正文容器
                section_content = f'<article id="{section_id}" class="section-content">\n'
                section_content += f'<h1 class="section-title">{section_title}</h1>\n'
                
                for scene in section.get("children", []):  # Level 3: 场景
                    rel_path = scene.get("file_path")
                    md_text = ""
                    if rel_path:
                        full_path = os.path.join(self.workspace.text_path, rel_path)
                        if os.path.exists(full_path):
                            with open(full_path, 'r', encoding='utf-8') as f:
                                md_text = f.read()
                    
                    if md_text:
                        # 将 Markdown 转换为 HTML，支持扩展语法（如表格、图片格式）
                        html_text = markdown.markdown(md_text, extensions=['extra'])
                        section_content += f'<div class="scene-body">{html_text}</div>\n'
                
                section_content += '</article>\n'
                content_html += section_content
                section_counter += 1

        # 4. 组装最终的 HTML 模板
        final_html = self._get_html_template().replace(
            "{novel_title}", novel_title
        ).replace(
            "{nav_html}", nav_html
        ).replace(
            "{content_html}", content_html
        )

        output_file = os.path.join(self.www_path, "index.html")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(final_html)

        return output_file

    def _get_html_template(self) -> str:
        """内置的响应式 HTML+CSS+JS 模板"""
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{novel_title}</title>
    <style>
        :root { --bg: #fdfdfd; --text: #2c3e50; --sidebar-bg: #f4f6f8; --border: #e0e6ed; --accent: #3498db; }
        * { box-sizing: border-box; }
        body { margin: 0; display: flex; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; height: 100vh; background: var(--bg); color: var(--text); overflow: hidden; }
        
        /* Sidebar Styles */
        #sidebar { width: 280px; background: var(--sidebar-bg); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 1000; transition: transform 0.3s ease; }
        .sidebar-header { padding: 20px 15px; font-weight: bold; font-size: 1.2em; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #fff;}
        #nav-container { flex: 1; overflow-y: auto; padding-bottom: 20px; }
        .nav-chapter { font-weight: bold; padding: 15px 15px 8px 15px; color: #7f8c8d; font-size: 0.9em; text-transform: uppercase; letter-spacing: 1px; }
        .nav-section { display: block; padding: 10px 15px 10px 30px; color: var(--text); text-decoration: none; cursor: pointer; border-left: 3px solid transparent; font-size: 1.05em; transition: background 0.2s; }
        .nav-section:hover { background: #eef2f5; }
        .nav-section.active { background: #eef2f5; border-left-color: var(--accent); color: var(--accent); font-weight: bold; }
        
        /* Content Styles */
        #main-wrapper { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        #mobile-header { display: none; background: #fff; border-bottom: 1px solid var(--border); padding: 10px 15px; align-items: center; justify-content: space-between; z-index: 900; box-shadow: 0 1px 3px rgba(0,0,0,0.05);}
        .hamburger { font-size: 24px; cursor: pointer; background: none; border: none; padding: 5px; color: var(--text); }
        #content-area { flex: 1; overflow-y: auto; padding: 30px; scroll-behavior: smooth; }
        
        /* Reading Typography */
        .section-content { display: none; max-width: 800px; margin: 0 auto; animation: fadeIn 0.4s ease; }
        .section-content.active { display: block; }
        .section-title { font-size: 2em; margin-bottom: 1em; text-align: center; border-bottom: 2px solid var(--border); padding-bottom: 15px; }
        .scene-body { font-size: 1.15em; line-height: 1.8; margin-bottom: 40px; text-align: justify; }
        .scene-body p { margin-bottom: 1.2em; }
        .scene-body img { max-width: 100%; height: auto; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin: 10px 0; }
        
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        
        /* Mobile Overlay */
        #overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 999; backdrop-filter: blur(2px); }
        .close-btn { display: none; font-size: 28px; cursor: pointer; background: none; border: none; color: #999; line-height: 1; }
        
        /* Mobile Responsiveness */
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
            <span>📚 目录</span>
            <button class="close-btn" onclick="toggleSidebar()">×</button>
        </div>
        <div id="nav-container">
            {nav_html}
        </div>
    </div>
    
    <div id="main-wrapper">
        <div id="mobile-header">
            <button class="hamburger" onclick="toggleSidebar()">☰</button>
            <span style="font-weight:bold; font-size:1.1em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:70%;">{novel_title}</span>
            <div style="width:24px;"></div> </div>
        
        <div id="content-area">
            {content_html}
        </div>
    </div>

    <script>
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
            document.getElementById('overlay').classList.toggle('show');
        }

        function showSection(id) {
            // Hide all sections
            document.querySelectorAll('.section-content').forEach(el => el.classList.remove('active'));
            // Remove active state from nav
            document.querySelectorAll('.nav-section').forEach(el => el.classList.remove('active'));
            
            // Show target section
            let target = document.getElementById(id);
            if(target) target.classList.add('active');
            
            // Highlight target nav
            let link = document.querySelector(`.nav-section[data-target="${id}"]`);
            if(link) link.classList.add('active');
            
            // Close sidebar on mobile
            if(window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
                document.getElementById('overlay').classList.remove('show');
            }
            
            // Scroll to top
            document.getElementById('content-area').scrollTo(0, 0);
        }

        // Initialize first section
        window.onload = () => {
            let firstNav = document.querySelector('.nav-section');
            if(firstNav) firstNav.click();
        }
    </script>
</body>
</html>"""