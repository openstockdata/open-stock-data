#!/usr/bin/env python3
"""
Fetcher 迁移脚本

演示如何为现有 Fetcher 添加能力声明的实际步骤。

使用方法:
    python scripts/migrate_fetcher.py tickflow
    python scripts/migrate_fetcher.py --all
    python scripts/migrate_fetcher.py --dry-run tushare
"""

import os
import re
import sys
from pathlib import Path


# 为每个 Fetcher 定义需要插入的代码
CAPABILITY_IMPORTS = """
from .capability_definitions import create_{fetcher_lower}_capability
"""

CAPABILITY_INIT = """
        # 声明能力
        self.capability = create_{fetcher_lower}_capability()

        # 根据能力更新可用性（可选）
        if hasattr(self, '_available'):
            self._available = self.capability.is_available()
"""


def get_fetcher_files():
    """获取所有 Fetcher 文件"""
    data_provider_dir = Path(__file__).parent.parent / "open_stock_data" / "data_provider"

    fetchers = {
        "tickflow": data_provider_dir / "tickflow_fetcher.py",
        "tushare": data_provider_dir / "tushare_fetcher.py",
        "efinance": data_provider_dir / "efinance_fetcher.py",
        "akshare": data_provider_dir / "akshare_fetcher.py",
        "baostock": data_provider_dir / "baostock_fetcher.py",
        "pytdx": data_provider_dir / "pytdx_fetcher.py",
        "yfinance": data_provider_dir / "yfinance_fetcher.py",
        "alphavantage": data_provider_dir / "alphavantage_fetcher.py",
    }

    return fetchers


def migrate_fetcher(fetcher_name: str, dry_run: bool = False):
    """
    为指定 Fetcher 添加能力声明

    Args:
        fetcher_name: Fetcher 名称（如 "tickflow"）
        dry_run: 如果为 True，只显示差异，不修改文件
    """
    fetchers = get_fetcher_files()

    if fetcher_name not in fetchers:
        print(f"❌ 未知的 Fetcher: {fetcher_name}")
        print(f"可用的 Fetcher: {', '.join(fetchers.keys())}")
        return False

    file_path = fetchers[fetcher_name]

    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return False

    print(f"\n{'='*60}")
    print(f"迁移 {fetcher_name.capitalize()}Fetcher")
    print(f"{'='*60}\n")

    # 读取原文件
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查是否已经迁移
    if 'capability_definitions' in content:
        print(f"⚠️  {fetcher_name}Fetcher 已经包含能力声明，跳过")
        return True

    # 步骤1: 添加导入
    print("📝 步骤1: 添加导入")
    import_line = f"from .capability_definitions import create_{fetcher_name}_capability\n"

    # 查找最后一个 from .xxx import 语句
    last_import_pos = 0
    for match in re.finditer(r'^from \.[a-z_]+ import.*$', content, re.MULTILINE):
        last_import_pos = match.end()

    if last_import_pos > 0:
        new_content = content[:last_import_pos] + '\n' + import_line + content[last_import_pos:]
        print(f"  ✓ 在第 {content[:last_import_pos].count(chr(10)) + 1} 行后添加导入")
    else:
        print(f"  ⚠️  未找到导入位置，跳过")
        new_content = content

    # 步骤2: 在 __init__ 方法末尾添加能力声明
    print("\n📝 步骤2: 在 __init__ 方法中添加能力声明")

    # 查找 __init__ 方法
    init_pattern = r'(def __init__\(self.*?\):.*?)(\n\s{4}def\s|\nclass\s|\Z)'
    match = re.search(init_pattern, new_content, re.DOTALL)

    if match:
        init_body = match.group(1)
        after_init = match.group(2)

        # 在 __init__ 末尾添加能力声明
        capability_code = CAPABILITY_INIT.format(fetcher_lower=fetcher_name)
        new_init_body = init_body.rstrip() + '\n' + capability_code

        new_content = new_content[:match.start()] + new_init_body + after_init + new_content[match.end():]

        print(f"  ✓ 在 __init__ 方法末尾添加能力声明")
    else:
        print(f"  ⚠️  未找到 __init__ 方法，跳过")

    # 显示差异
    if dry_run:
        print(f"\n📋 差异预览 (--dry-run 模式):\n")
        print("=" * 60)
        print("添加的导入:")
        print("-" * 60)
        print(import_line)
        print("=" * 60)
        print("添加到 __init__ 的代码:")
        print("-" * 60)
        print(CAPABILITY_INIT.format(fetcher_lower=fetcher_name))
        print("=" * 60)
        return True

    # 写入文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    print(f"\n✅ 迁移完成: {file_path}")
    return True


def verify_capability_definitions():
    """验证 capability_definitions.py 是否存在所有必要的函数"""
    capability_file = Path(__file__).parent.parent / "open_stock_data" / "data_provider" / "capability_definitions.py"

    if not capability_file.exists():
        print(f"❌ 未找到 capability_definitions.py")
        return False

    with open(capability_file, 'r') as f:
        content = f.read()

    fetchers = get_fetcher_files().keys()
    missing = []

    for fetcher in fetchers:
        func_name = f"create_{fetcher}_capability"
        if func_name not in content:
            missing.append(func_name)

    if missing:
        print(f"❌ capability_definitions.py 缺少以下函数:")
        for func in missing:
            print(f"  - {func}()")
        return False

    print("✅ capability_definitions.py 验证通过")
    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Fetcher 能力声明迁移工具")
    parser.add_argument("fetcher", nargs="?", help="Fetcher 名称（如 tickflow, tushare）")
    parser.add_argument("--all", action="store_true", help="迁移所有 Fetcher")
    parser.add_argument("--dry-run", action="store_true", help="仅显示差异，不修改文件")
    parser.add_argument("--verify", action="store_true", help="验证 capability_definitions.py")

    args = parser.parse_args()

    # 验证模式
    if args.verify:
        return 0 if verify_capability_definitions() else 1

    # 检查必要文件
    if not verify_capability_definitions():
        print("\n请先修复 capability_definitions.py 中的问题")
        return 1

    # 迁移所有
    if args.all:
        fetchers = get_fetcher_files().keys()
        success = 0
        failed = 0

        for fetcher in fetchers:
            if migrate_fetcher(fetcher, args.dry_run):
                success += 1
            else:
                failed += 1

        print(f"\n{'='*60}")
        print(f"迁移完成: 成功 {success}, 失败 {failed}")
        print(f"{'='*60}\n")

        return 0 if failed == 0 else 1

    # 迁移单个
    if not args.fetcher:
        parser.print_help()
        return 1

    success = migrate_fetcher(args.fetcher, args.dry_run)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
