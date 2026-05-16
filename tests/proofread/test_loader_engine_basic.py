import json
import os
import tempfile
import unittest

from core.workspace_manager import WorkspaceManager
from modules.proofread.engine import ProofreadEngine
from modules.proofread.loader import ProofreadDataLoader
from modules.proofread.passes.punctuation import PunctuationPass


class _FakeGateway:
    def call_json(self, payload, task_id, system_instruction=None, progress_callback=None):
        if task_id.startswith("solve/"):
            return {"ok": True, "result": {"ok": True, "fixed_text": "修复后正文"}, "raw_text": "", "attempts": 1, "error": None}
        return {"ok": True, "result": {"ok": True, "issues": [], "intermediate_data": {}}, "raw_text": "", "attempts": 1, "error": None}


class LoaderEngineBasicTests(unittest.TestCase):
    def _build_workspace(self, td: str) -> WorkspaceManager:
        ws = WorkspaceManager(td)
        ws.init_workspace()
        scene_rel = "场景_1.md"
        scene_full = os.path.join(ws.text_path, scene_rel)
        with open(scene_full, "w", encoding="utf-8") as f:
            f.write("她想了想...然后出门。")
        md5 = ws.calculate_md5(scene_full)
        tree = {
            "project_name": "demo",
            "nodes": [
                {
                    "id": "c1",
                    "title": "第一章",
                    "summary": "",
                    "children": [
                        {
                            "id": "s1",
                            "title": "第一节",
                            "summary": "",
                            "children": [
                                {
                                    "id": "scene_1",
                                    "title": "场景1",
                                    "summary": "【时间】：夜\n【地点】：宿舍\n【人物】：小明\n【事件】：\n1. 出门\n【剧情总结】：测试",
                                    "children": [],
                                    "_status": "ok",
                                    "file_path": scene_rel,
                                    "md5": md5,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        ws.save_outline_tree(tree)
        return ws

    def test_loader_and_find_solve(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self._build_workspace(td)
            loader = ProofreadDataLoader(ws)
            data = loader.load_workspace_data()
            self.assertEqual(len(data["scene_list"]), 1)
            self.assertEqual(data["scene_list"][0].node_id, "scene_1")

            engine = ProofreadEngine(
                workspace=ws,
                llm_gateway=_FakeGateway(),
                pass_instances=[PunctuationPass()],
            )
            issues = engine.run_find()
            self.assertTrue(any(i.category == "W5" for i in issues))
            issues_file = os.path.join(ws.sys_data_path, "proofread_issues.json")
            self.assertTrue(os.path.exists(issues_file))

            stats = engine.run_solve(issues=issues)
            self.assertEqual(stats["ok_count"], 1)
            pending_file = os.path.join(ws.pending_modifies_dir, "scene_1.json")
            self.assertTrue(os.path.exists(pending_file))
            with open(pending_file, "r", encoding="utf-8") as f:
                pending = json.load(f)
            self.assertEqual(pending["node_id"], "scene_1")


if __name__ == "__main__":
    unittest.main()

