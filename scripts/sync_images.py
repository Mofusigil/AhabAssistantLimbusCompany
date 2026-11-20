#!/usr/bin/env python3
"""手动从官方 AALCImageResources 同步最新图片资源（适用于 Linux 源码运行版）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
os.chdir(REPO_DIR)
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from module.config import cfg  # noqa: E402
from module.resource_sync.service import ResourceCheckStatus, ResourceSyncService  # noqa: E402


def main() -> int:
    source = str(cfg.get_value("image_resource_source", "Auto") or "Auto")
    service = ResourceSyncService()
    check_result = service.check_for_updates(source)
    if check_result.status is ResourceCheckStatus.ERROR:
        print(f"检查图片资源失败: {check_result.message}", file=sys.stderr)
        return 1
    if check_result.remote_manifest is None:
        print(f"图片资源已是最新 ({check_result.message})")
        return 0
    sync_plan = service.build_sync_plan(check_result.remote_manifest)
    if not sync_plan.has_changes:
        service.accept_remote_state(check_result)
        print("本地图片资源已与远端一致")
        return 0
    print(
        f"发现图片资源更新（来源: {check_result.source.name if check_result.source else source}）："
        f"新增 {len(sync_plan.files_to_add)}，更新 {len(sync_plan.files_to_update)}，删除 {len(sync_plan.files_to_delete)}"
    )
    apply_result = service.apply_sync_plan(check_result=check_result, sync_plan=sync_plan)
    print(f"同步完成：更新 {apply_result.downloaded_count} 个文件，删除 {apply_result.deleted_count} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
