"""
Mixin 子模块包 —— 将 NovelCreatorWindow 按职能拆分为多个 Mixin。
"""

from ui.mixins.config_mixin import ConfigMixin
from ui.mixins.workspace_mixin import WorkspaceMixin
from ui.mixins.setting_tree_mixin import SettingTreeMixin
from ui.mixins.novel_tree_mixin import NovelTreeMixin
from ui.mixins.generation_mixin import GenerationMixin
from ui.mixins.editor_mixin import EditorMixin

__all__ = [
    "ConfigMixin",
    "WorkspaceMixin",
    "SettingTreeMixin",
    "NovelTreeMixin",
    "GenerationMixin",
    "EditorMixin",
]
