#!/usr/bin/env python3
"""
存储后端数据迁移脚本

用法：
  python scripts/migrate_storage.py --from json --to postgres
  python scripts/migrate_storage.py --from json --to postgres --replace
  python scripts/migrate_storage.py --from postgres --to git
  python scripts/migrate_storage.py --export accounts.json
  python scripts/migrate_storage.py --import accounts.json --replace
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

from services.storage.factory import create_storage_backend
from services.storage.base import atomic_write_text


def _merge_accounts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        token = str(item.get("access_token") or "").strip()
        if not token:
            continue
        if token not in merged:
            order.append(token)
        merged[token] = item
    return [merged[token] for token in order]


def _save_or_merge_accounts(storage, accounts: list[dict], *, replace: bool) -> None:
    if replace:
        storage.replace_accounts(accounts)
        return
    storage.save_accounts(_merge_accounts(storage.load_accounts(), accounts))


def export_to_json(output_file: str):
    """导出当前存储后端的数据到 JSON 文件"""
    print(f"[migrate] Exporting data to {output_file}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    storage = create_storage_backend(DATA_DIR)
    accounts = storage.load_accounts()
    
    output_path = Path(output_file)
    atomic_write_text(output_path, json.dumps(accounts, ensure_ascii=False, indent=2) + "\n")
    
    print(f"[migrate] Exported {len(accounts)} accounts to {output_file}")


def import_from_json(input_file: str, *, replace: bool = False):
    """从 JSON 文件导入数据到当前存储后端"""
    print(f"[migrate] Importing data from {input_file}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"[migrate] Error: File not found: {input_file}")
        sys.exit(1)
    
    try:
        accounts = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(accounts, list):
            print(f"[migrate] Error: Invalid JSON format, expected array")
            sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[migrate] Error: Invalid JSON: {e}")
        sys.exit(1)
    
    storage = create_storage_backend(DATA_DIR)
    _save_or_merge_accounts(storage, accounts, replace=replace)
    
    mode = "replaced" if replace else "imported"
    print(f"[migrate] {mode.capitalize()} {len(accounts)} accounts")


def migrate_data(from_backend: str, to_backend: str, *, replace: bool = False):
    """从一个存储后端迁移到另一个"""
    print(f"[migrate] Migrating from {from_backend} to {to_backend}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 保存原始环境变量
    original_backend = os.environ.get("STORAGE_BACKEND")
    
    try:
        # 从源后端读取数据
        os.environ["STORAGE_BACKEND"] = from_backend
        from_storage = create_storage_backend(DATA_DIR)
        accounts = from_storage.load_accounts()
        print(f"[migrate] Loaded {len(accounts)} accounts from {from_backend}")
        
        # 写入目标后端
        os.environ["STORAGE_BACKEND"] = to_backend
        to_storage = create_storage_backend(DATA_DIR)
        if replace:
            to_storage.replace_accounts(accounts)
            print(f"[migrate] Replaced target data with {len(accounts)} accounts in {to_backend}")
        else:
            _save_or_merge_accounts(to_storage, accounts, replace=False)
            print(f"[migrate] Saved {len(accounts)} accounts to {to_backend}")
        
        print(f"[migrate] Migration completed successfully!")
        
    finally:
        # 恢复原始环境变量
        if original_backend:
            os.environ["STORAGE_BACKEND"] = original_backend
        elif "STORAGE_BACKEND" in os.environ:
            del os.environ["STORAGE_BACKEND"]


def main():
    parser = argparse.ArgumentParser(
        description="Genapi 存储后端数据迁移工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从 JSON 迁移到 PostgreSQL
  python scripts/migrate_storage.py --from json --to postgres
  python scripts/migrate_storage.py --from json --to postgres --replace
  
  # 从 PostgreSQL 迁移到 Git
  python scripts/migrate_storage.py --from postgres --to git
  
  # 导出当前数据到 JSON 文件
  python scripts/migrate_storage.py --export backup.json
  
  # 从 JSON 文件导入数据
  python scripts/migrate_storage.py --import backup.json
  python scripts/migrate_storage.py --import backup.json --replace

环境变量:
  STORAGE_BACKEND  - 存储后端类型 (json, sqlite, postgres, git)
  DATABASE_URL     - 数据库连接字符串
  GIT_REPO_URL     - Git 仓库地址
  GIT_TOKEN        - Git 访问令牌
        """
    )
    
    parser.add_argument(
        "--from",
        dest="from_backend",
        choices=["json", "sqlite", "postgres", "git"],
        help="源存储后端",
    )
    parser.add_argument(
        "--to",
        dest="to_backend",
        choices=["json", "sqlite", "postgres", "git"],
        help="目标存储后端",
    )
    parser.add_argument(
        "--export",
        dest="export_file",
        metavar="FILE",
        help="导出数据到 JSON 文件",
    )
    parser.add_argument(
        "--import",
        dest="import_file",
        metavar="FILE",
        help="从 JSON 文件导入数据",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="显式替换目标账号数据；默认使用后端 upsert 语义，不删除目标端额外账号",
    )
    
    args = parser.parse_args()
    
    # 检查参数
    if args.from_backend and args.to_backend:
        migrate_data(args.from_backend, args.to_backend, replace=args.replace)
    elif args.export_file:
        if args.replace:
            print("[migrate] Error: --replace cannot be used with --export")
            sys.exit(1)
        export_to_json(args.export_file)
    elif args.import_file:
        import_from_json(args.import_file, replace=args.replace)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
