"""
文件变更追踪器
在加载文件时记录 mtime 和 MD5，保存前检测外部变更，
提示用户是否覆盖或另存为冲突文件。
"""
import os
import hashlib
import shutil
import logging
from datetime import datetime
from typing import Tuple

logger = logging.getLogger(__name__)


class FileTracker:
    """追踪文件加载时的状态，用于保存前检测外部变更。"""

    def __init__(self):
        # file_path -> (mtime, md5)
        self._tracked: dict[str, Tuple[float, str]] = {}

    def track_file(self, file_path: str) -> str:
        """记录文件的当前状态（mtime, md5），返回 MD5。文件不存在时记录空值。"""
        if not os.path.exists(file_path):
            self._tracked[os.path.normcase(os.path.abspath(file_path))] = (-1, "")
            return ""
        mtime = os.path.getmtime(file_path)
        md5 = self._calculate_md5(file_path)
        self._tracked[os.path.normcase(os.path.abspath(file_path))] = (mtime, md5)
        return md5

    def untrack_file(self, file_path: str):
        """移除文件追踪。"""
        self._tracked.pop(os.path.normcase(os.path.abspath(file_path)), None)

    def has_changed(self, file_path: str) -> bool:
        """
        检查文件自追踪以来是否有外部变更。
        如果文件被删除后又重新创建，也视为变更。
        """
        key = os.path.normcase(os.path.abspath(file_path))
        if key not in self._tracked:
            return False
        tracked_mtime, tracked_md5 = self._tracked[key]

        if not os.path.exists(file_path):
            # 文件被删除
            return tracked_mtime >= 0

        current_mtime = os.path.getmtime(file_path)
        if current_mtime != tracked_mtime:
            current_md5 = self._calculate_md5(file_path)
            return current_md5 != tracked_md5

        return False

    def save_conflict_copy(self, file_path: str) -> str:
        """
        将磁盘上的当前版本另存到工程目录的 conflicts/ 子目录中。
        格式：{原文件名}.conflict_{时间戳}{扩展名}
        返回另存为的目标路径。
        """
        base_dir = os.path.dirname(os.path.dirname(file_path))  # 工程根目录
        conflicts_dir = os.path.join(base_dir, "conflicts")
        os.makedirs(conflicts_dir, exist_ok=True)

        fname = os.path.basename(file_path)
        name, ext = os.path.splitext(fname)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        conflict_name = f"{name}.conflict_{timestamp}{ext}"
        dest_path = os.path.join(conflicts_dir, conflict_name)

        shutil.copy2(file_path, dest_path)
        logger.info(f"冲突文件已另存: {dest_path}")
        return dest_path

    def refresh_md5(self, file_path: str):
        """保存成功后刷新追踪状态。"""
        if os.path.exists(file_path):
            mtime = os.path.getmtime(file_path)
            md5 = self._calculate_md5(file_path)
            self._tracked[os.path.normcase(os.path.abspath(file_path))] = (mtime, md5)

    @staticmethod
    def _calculate_md5(file_path: str) -> str:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
