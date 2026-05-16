from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from core.llm_client import LLMClient
from core.workspace_manager import WorkspaceManager

from . import create_default_engine
from .engine import build_issue_from_dict


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件不是 JSON 对象: {path}")
    return data


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proofread standalone runner")
    parser.add_argument("--workspace", required=True, help="小说工程目录绝对路径")
    parser.add_argument("--config", required=True, help="LLM 配置 JSON 路径（至少包含 text_api）")
    parser.add_argument("--mode", choices=["find", "solve"], default="find", help="运行模式")
    parser.add_argument("--resume", action="store_true", help="从检查点恢复运行")
    parser.add_argument("--issues-file", default="", help="solve 模式可选：显式指定 issues 文件路径")
    parser.add_argument("--categories", default="", help="solve 模式可选：按类别筛选，如 L1,L2,W5")
    parser.add_argument("--chapters", default="", help="solve 模式可选：按章节标题筛选，逗号分隔")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    workspace_path = os.path.abspath(args.workspace)
    config_path = os.path.abspath(args.config)
    if not os.path.exists(workspace_path):
        print(f"[ERROR] workspace 不存在: {workspace_path}")
        return 2
    if not os.path.exists(config_path):
        print(f"[ERROR] config 不存在: {config_path}")
        return 2

    config = _load_json(config_path)
    workspace = WorkspaceManager(workspace_path)
    llm_client = LLMClient(config)
    engine = create_default_engine(workspace=workspace, llm_client=llm_client, config=config)

    if args.mode == "find":
        if args.resume:
            issues = engine.resume_find(progress_callback=lambda m: print(m))
        else:
            issues = engine.run_find(progress_callback=lambda m: print(m))
        print(f"[DONE] find 完成，问题数: {len(issues)}")
        print(f"[OUT] {os.path.join(workspace.sys_data_path, 'proofread_issues.json')}")
        return 0

    issues_path = args.issues_file or os.path.join(workspace.sys_data_path, "proofread_issues.json")
    if not os.path.exists(issues_path):
        print(f"[ERROR] issues 文件不存在: {issues_path}")
        return 2
    data = _load_json(issues_path)
    raw_issues = data.get("issues", [])
    issues = [build_issue_from_dict(x) for x in raw_issues if isinstance(x, dict)]
    categories = {x.strip() for x in args.categories.split(",") if x.strip()} if args.categories else None
    chapters = {x.strip() for x in args.chapters.split(",") if x.strip()} if args.chapters else None
    if args.resume:
        stats = engine.resume_solve(
            issues=issues,
            progress_callback=lambda m: print(m),
            include_categories=categories,
            include_chapters=chapters,
        )
    else:
        stats = engine.run_solve(
            issues=issues,
            progress_callback=lambda m: print(m),
            include_categories=categories,
            include_chapters=chapters,
        )
    print(f"[DONE] solve 完成: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
