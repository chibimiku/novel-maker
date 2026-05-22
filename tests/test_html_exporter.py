import os
import tempfile
import unittest

from core.html_exporter import HtmlExporter
from core.workspace_manager import WorkspaceManager


class HtmlExporterTests(unittest.TestCase):
    def _build_workspace(self, td: str) -> WorkspaceManager:
        ws = WorkspaceManager(td)
        ws.init_workspace()

        scene_1_rel = "chapter1_scene1.md"
        scene_2_rel = "chapter1_scene2.md"
        scene_3_rel = "chapter2_scene1.md"

        for rel_path, content in [
            (scene_1_rel, "# 场景一\n\n第一章第一节第一场。"),
            (scene_2_rel, "第一章第一节第二场。"),
            (scene_3_rel, "# 场景三\n\n第二章唯一场景。"),
        ]:
            full_path = os.path.join(ws.text_path, rel_path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        tree = {
            "project_name": "导出测试小说",
            "nodes": [
                {
                    "id": "chapter_1",
                    "title": "第一章",
                    "children": [
                        {
                            "id": "section_1",
                            "title": "第一节",
                            "children": [
                                {
                                    "id": "scene_1",
                                    "title": "场景1",
                                    "file_path": scene_1_rel,
                                    "md5": ws.calculate_md5(os.path.join(ws.text_path, scene_1_rel)),
                                    "children": [],
                                },
                                {
                                    "id": "scene_2",
                                    "title": "场景2",
                                    "file_path": scene_2_rel,
                                    "md5": ws.calculate_md5(os.path.join(ws.text_path, scene_2_rel)),
                                    "children": [],
                                },
                            ],
                        }
                    ],
                },
                {
                    "id": "chapter_2",
                    "title": "第二章",
                    "children": [
                        {
                            "id": "section_2",
                            "title": "第二节",
                            "children": [
                                {
                                    "id": "scene_3",
                                    "title": "场景3",
                                    "file_path": scene_3_rel,
                                    "md5": ws.calculate_md5(os.path.join(ws.text_path, scene_3_rel)),
                                    "children": [],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        ws.save_outline_tree(tree)
        return ws

    def test_export_splits_pages_by_chapter(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self._build_workspace(td)
            exporter = HtmlExporter(ws)

            output_file = exporter.export()

            self.assertTrue(os.path.exists(output_file))
            self.assertTrue(output_file.endswith(os.path.join("www", "index.html")))

            with open(output_file, "r", encoding="utf-8") as f:
                index_html = f.read()
            self.assertIn('href="chapters/chapter_001_', index_html)
            self.assertIn('href="chapters/chapter_002_', index_html)

            chapter_dir = os.path.join(ws.workspace_path, "www", "chapters")
            chapter_files = sorted(name for name in os.listdir(chapter_dir) if name.endswith(".html"))
            self.assertEqual(len(chapter_files), 2)

            first_page = os.path.join(chapter_dir, chapter_files[0])
            second_page = os.path.join(chapter_dir, chapter_files[1])

            with open(first_page, "r", encoding="utf-8") as f:
                first_html = f.read()
            with open(second_page, "r", encoding="utf-8") as f:
                second_html = f.read()

            self.assertIn("第一章第一节第一场。", first_html)
            self.assertIn("第一章第一节第二场。", first_html)
            self.assertNotIn("第二章唯一场景。", first_html)

            self.assertIn("第二章唯一场景。", second_html)
            self.assertNotIn("第一章第一节第一场。", second_html)


if __name__ == "__main__":
    unittest.main()
