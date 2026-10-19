import json
import os
import tempfile
import unittest

from core.workspace_manager import WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "novel")
        os.makedirs(self.root)
        self.ws = WorkspaceManager(self.root)
        self.ws.init_workspace()

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_structure(self):
        for path in (
            self.ws.settings_path,
            self.ws.text_path,
            self.ws.sys_data_path,
            os.path.join(self.ws.text_path, "images"),
            self.ws.tree_json_file,
        ):
            self.assertTrue(os.path.exists(path), path)
        for dir_name in self.ws.setting_dirs:
            self.assertTrue(os.path.isdir(os.path.join(self.ws.settings_path, dir_name)))
            self.assertTrue(
                os.path.exists(
                    os.path.join(self.ws.settings_path, dir_name, "template.json")
                )
            )

    def test_markdown_roundtrip_and_md5(self):
        md5 = self.ws.save_markdown_file("场景_1.md", "# 标题\n正文")
        self.assertTrue(md5)
        full = os.path.join(self.ws.text_path, "场景_1.md")
        with open(full, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# 标题\n正文")
        self.assertEqual(self.ws.calculate_md5(full), md5)

    def test_calculate_md5_missing_file(self):
        self.assertEqual(self.ws.calculate_md5(os.path.join(self.root, "nope.md")), "")

    def test_load_outline_tree_assigns_ids(self):
        tree = {"nodes": [{"title": "第一章", "children": []}]}
        self.ws.save_outline_tree(tree)
        loaded = self.ws.load_outline_tree()
        self.assertTrue(loaded["nodes"][0].get("id"))

    def test_verify_tree_marks_missing_and_modified(self):
        rel = "场景_1.md"
        md5 = self.ws.save_markdown_file(rel, "原文")
        tree = {
            "nodes": [
                {
                    "title": "第一章",
                    "children": [
                        {
                            "title": "第1节",
                            "children": [
                                {"title": "场景1", "file_path": rel, "md5": md5},
                                {"title": "场景2", "file_path": "不存在.md", "md5": ""},
                            ],
                        }
                    ],
                }
            ]
        }
        self.ws.save_outline_tree(tree)
        loaded = self.ws.load_outline_tree()
        scenes = loaded["nodes"][0]["children"][0]["children"]
        self.assertEqual(scenes[0]["_status"], "ok")
        self.assertEqual(scenes[1]["_status"], "missing")

        # 外部修改正文后应标记 modified_externally 并刷新 md5
        self.ws.save_markdown_file(rel, "被外部改过")
        loaded2 = self.ws.load_outline_tree()
        scene = loaded2["nodes"][0]["children"][0]["children"][0]
        self.assertEqual(scene["_status"], "modified_externally")
        self.assertEqual(scene["md5"], self.ws.calculate_md5(os.path.join(self.ws.text_path, rel)))

    def test_pending_modify_lifecycle(self):
        self.assertFalse(self.ws.has_pending_modify("n1"))
        self.ws.save_pending_modify("n1", "old", "new", "改得更好")
        self.assertTrue(self.ws.has_pending_modify("n1"))
        data = self.ws.get_pending_modify("n1")
        self.assertEqual(data["modified_text"], "new")
        self.assertEqual(self.ws.get_all_pending_node_ids(), ["n1"])
        self.ws.delete_pending_modify("n1")
        self.assertFalse(self.ws.has_pending_modify("n1"))

    def test_pending_modify_persists_to_disk(self):
        self.ws.save_pending_modify("n1", "old", "new", "req")
        reopened = WorkspaceManager(self.root)
        self.assertTrue(reopened.has_pending_modify("n1"))

    def test_nsfw_flag(self):
        self.assertFalse(self.ws.get_is_nsfw())
        self.ws.set_is_nsfw(True)
        self.assertTrue(self.ws.get_is_nsfw())
        self.assertTrue(WorkspaceManager(self.root).get_is_nsfw())

    def test_instruction_profile(self):
        self.assertIsNone(self.ws.load_instruction_profile())
        self.ws.save_instruction_profile({"writing_instruction_name": "默认写作Instruction"})
        self.assertEqual(
            self.ws.load_instruction_profile()["writing_instruction_name"],
            "默认写作Instruction",
        )

    def test_mobile_issues(self):
        os.makedirs(self.ws.mobile_issues_dir, exist_ok=True)
        for idx, stamp in enumerate(("2026-01-01T10:00:00+08:00", "2026-02-01T10:00:00+08:00")):
            with open(
                os.path.join(self.ws.mobile_issues_dir, f"i{idx}.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    {"issue_id": f"i{idx}", "submitted_at": stamp, "status": "pending"},
                    handle,
                    ensure_ascii=False,
                )
        issues = self.ws.load_mobile_issues()
        self.assertEqual([item["issue_id"] for item in issues], ["i1", "i0"])
        self.assertTrue(self.ws.update_mobile_issue("i0", {"status": "done"}))
        self.assertTrue(self.ws.delete_mobile_issue("i0"))
        self.assertEqual(len(self.ws.load_mobile_issues()), 1)

    def test_save_outline_tree_accepts_file_tracker(self):
        from core.file_tracker import FileTracker

        tracker = FileTracker()
        tracker.track_file(self.ws.tree_json_file)
        self.ws.save_outline_tree({"nodes": []}, tracker)
        self.assertFalse(tracker.has_changed(self.ws.tree_json_file))


if __name__ == "__main__":
    unittest.main()
