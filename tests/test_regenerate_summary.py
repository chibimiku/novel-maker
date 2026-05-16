import unittest
from unittest.mock import patch

from ui.mixins.generation_mixin import GenerationMixin


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, cb):
        self.callbacks.append(cb)


class _FakeThread:
    instances = []

    def __init__(self, llm_client, prompt_content, override_system_instruction=None, parent=None, running=False):
        self.llm_client = llm_client
        self.prompt_content = prompt_content
        self.override_system_instruction = override_system_instruction
        self.success_signal = _FakeSignal()
        self.error_signal = _FakeSignal()
        self.started = False
        self._running = running
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True
        self._running = True

    def isRunning(self):
        return self._running


class _FakeButton:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = enabled


class _FakeEditor:
    def __init__(self, text=""):
        self._text = text

    def toPlainText(self):
        return self._text

    def setText(self, text):
        self._text = text


class _FakeLog:
    def __init__(self):
        self.messages = []

    def append(self, msg):
        self.messages.append(msg)


class _FakeWorkspace:
    def __init__(self):
        self.saved_payloads = []

    def save_outline_tree(self, payload):
        self.saved_payloads.append(payload)


class _FakeItem:
    def __init__(self, node_id, text="节点"):
        self._node_id = node_id
        self._text = text

    def data(self, col, role):
        return self._node_id

    def text(self, col):
        return self._text


class _FakeTree:
    def __init__(self, item=None):
        self._item = item

    def currentItem(self):
        return self._item


class _FakeBuilder:
    def __init__(self, workspace, **kwargs):
        self.workspace = workspace

    def build_summary_prompt(self, current_node, tree_data, checked_paths):
        return [{"role": "user", "content": f"prompt for {current_node.get('id')}"}]


class _DummyWindow(GenerationMixin):
    def __init__(self):
        self.workspace = _FakeWorkspace()
        self.outline_tree_data = {"nodes": []}
        self.config = {}
        self.llm_client = type("LLM", (), {"summary_system_instruction": "summary-sys"})()
        self.current_editing_node = None
        self.current_editing_item = None
        self.node_map = {}
        self.generate_thread = None
        self.summary_editor = _FakeEditor("")
        self.content_editor = _FakeEditor("")
        self.log_console = _FakeLog()
        self.btn_generate = _FakeButton()
        self.btn_rewrite = _FakeButton()
        self.btn_save = _FakeButton()
        self.btn_delete = _FakeButton()
        self.btn_regenerate_summary = _FakeButton()
        self.novel_tree = _FakeTree()
        self.saved_called = 0
        self.undo_saved = []
        self.restore_called = 0
        self._sender_obj = None
        self.is_batch_generating = False

    def _get_current_node_from_tree(self):
        return GenerationMixin._get_current_node_from_tree(self)

    def _save_to_undo_stack(self, change_type, old_value):
        self.undo_saved.append((change_type, old_value))

    def save_current_node(self):
        self.saved_called += 1

    def get_checked_settings(self):
        return []

    def statusBar(self):
        return None

    def _restore_generate_ui_state(self):
        self.restore_called += 1
        self.generate_thread = None

    def sender(self):
        return self._sender_obj


class RegenerateSummaryTests(unittest.TestCase):
    def setUp(self):
        _FakeThread.instances = []

    @patch("ui.mixins.generation_mixin.ContextBuilder", _FakeBuilder)
    @patch("ui.mixins.generation_mixin.GenerateTaskThread", _FakeThread)
    def test_regenerate_summary_saves_first_and_disables_button(self):
        win = _DummyWindow()
        node = {"id": "n1", "title": "节点1", "summary": "old"}
        win.current_editing_node = node
        win.current_editing_item = _FakeItem("n1")
        win.node_map["n1"] = node
        win.summary_editor = _FakeEditor("old-summary")
        win.content_editor = _FakeEditor("unsaved content")

        win.regenerate_summary()

        self.assertEqual(win.saved_called, 1)
        self.assertFalse(win.btn_regenerate_summary.enabled)
        self.assertEqual(len(_FakeThread.instances), 1)
        thread = _FakeThread.instances[0]
        self.assertTrue(thread.started)
        self.assertEqual(getattr(thread, "_summary_target_node_id"), "n1")
        self.assertTrue(getattr(thread, "_summary_request_id"))

    @patch("ui.mixins.generation_mixin.ContextBuilder", _FakeBuilder)
    @patch("ui.mixins.generation_mixin.GenerateTaskThread", _FakeThread)
    def test_success_callback_writes_to_target_node_only(self):
        win = _DummyWindow()
        node_a = {"id": "A", "title": "A", "summary": "old-a"}
        node_b = {"id": "B", "title": "B", "summary": "old-b"}
        win.node_map = {"A": node_a, "B": node_b}
        win.current_editing_node = node_b
        win.current_editing_item = _FakeItem("B")
        win.summary_editor = _FakeEditor("editor-b")

        win.regenerate_summary = lambda: None  # 避免干扰，本测试只测回调
        win._active_summary_regen_request_id = "req-2"
        sender = type("Sender", (), {"_summary_request_id": "req-2", "_summary_target_node_id": "A"})()
        win._sender_obj = sender

        win.on_summary_generate_success("new-summary-a")

        self.assertEqual(node_a["summary"], "new-summary-a")
        self.assertEqual(node_b["summary"], "old-b")
        self.assertEqual(win.summary_editor.toPlainText(), "editor-b")
        self.assertEqual(len(win.workspace.saved_payloads), 1)
        self.assertIsNone(getattr(win, "_active_summary_regen_request_id", None))
        self.assertEqual(win.restore_called, 1)

    def test_stale_callback_is_ignored(self):
        win = _DummyWindow()
        node_a = {"id": "A", "title": "A", "summary": "old-a"}
        win.node_map = {"A": node_a}
        win._active_summary_regen_request_id = "latest"
        sender = type("Sender", (), {"_summary_request_id": "old", "_summary_target_node_id": "A"})()
        win._sender_obj = sender

        win.on_summary_generate_success("new-summary-a")

        self.assertEqual(node_a["summary"], "old-a")
        self.assertEqual(len(win.workspace.saved_payloads), 0)
        self.assertEqual(win.restore_called, 0)

    @patch("ui.mixins.generation_mixin.ContextBuilder", _FakeBuilder)
    @patch("ui.mixins.generation_mixin.GenerateTaskThread", _FakeThread)
    def test_regenerate_summary_ignores_repeated_click_while_running(self):
        win = _DummyWindow()
        node = {"id": "n1", "title": "节点1", "summary": "old"}
        win.current_editing_node = node
        win.current_editing_item = _FakeItem("n1")
        win.node_map["n1"] = node
        win.generate_thread = _FakeThread(None, "", running=True)

        win.regenerate_summary()

        self.assertEqual(len(_FakeThread.instances), 1)
        self.assertTrue(any("已有生成任务在进行中" in msg for msg in win.log_console.messages))

    @patch("ui.mixins.generation_mixin.ContextBuilder", _FakeBuilder)
    @patch("ui.mixins.generation_mixin.GenerateTaskThread", _FakeThread)
    def test_regenerate_summary_allows_stale_finished_thread_reference(self):
        win = _DummyWindow()
        node = {"id": "n1", "title": "节点1", "summary": "old"}
        win.current_editing_node = node
        win.current_editing_item = _FakeItem("n1")
        win.node_map["n1"] = node
        win.generate_thread = _FakeThread(None, "", running=False)

        win.regenerate_summary()

        self.assertEqual(len(_FakeThread.instances), 2)
        self.assertTrue(_FakeThread.instances[-1].started)

    @patch("ui.mixins.generation_mixin.ContextBuilder", _FakeBuilder)
    @patch("ui.mixins.generation_mixin.GenerateTaskThread", _FakeThread)
    def test_regenerate_summary_uses_tree_current_item_as_fallback(self):
        win = _DummyWindow()
        node = {"id": "n2", "title": "节点2", "summary": "old"}
        win.node_map["n2"] = node
        win.current_editing_item = None
        win.current_editing_node = None
        win.novel_tree = _FakeTree(_FakeItem("n2"))

        win.regenerate_summary()

        self.assertEqual(len(_FakeThread.instances), 1)
        self.assertTrue(_FakeThread.instances[0].started)


if __name__ == "__main__":
    unittest.main()
