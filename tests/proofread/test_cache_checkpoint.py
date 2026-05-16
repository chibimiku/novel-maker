import os
import tempfile
import unittest

from modules.proofread.cache_manager import ProofreadCacheManager
from modules.proofread.checkpoint_manager import ProofreadCheckpointManager


class CacheCheckpointTests(unittest.TestCase):
    def test_cache_mark_and_validate(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = ProofreadCacheManager(td)
            scene_id = "scene_a"
            pass_name = "w5_punctuation"
            md5 = "abc123"
            mgr.save_scene_pass_data(scene_id, pass_name, {"x": 1})
            mgr.mark_scene_pass_cached(scene_id, md5, pass_name)
            self.assertTrue(mgr.is_scene_pass_cache_valid(scene_id, md5, pass_name))
            self.assertFalse(mgr.is_scene_pass_cache_valid(scene_id, "changed", pass_name))
            mgr.invalidate_scene(scene_id)
            self.assertFalse(mgr.is_scene_pass_cache_valid(scene_id, md5, pass_name))

    def test_checkpoint_init_and_task(self):
        with tempfile.TemporaryDirectory() as td:
            ck = ProofreadCheckpointManager(td)
            run_state = ck.init_run(
                phase="find",
                run_id="run-1",
                md5_snapshot={"scene_a": "m1"},
                pass_names=["w5_punctuation"],
            )
            self.assertEqual(run_state["phase"], "find")
            self.assertTrue(os.path.exists(os.path.join(td, "系统数据", "proofread_checkpoint", "run_state.json")))
            ck.save_task_state("w5_punctuation", "scene_a", {"status": "completed", "attempts": 1})
            tasks = ck.load_pass_tasks("w5_punctuation")
            self.assertEqual(tasks["scenes"]["scene_a"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

