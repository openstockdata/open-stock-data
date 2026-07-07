"""测试辅助函数"""

import pytest


def assert_not_error(result: str):
    """断言结果不是错误消息"""
    error_keywords = ["Not Found", "失败", "错误", "Error", "Exception"]
    for kw in error_keywords:
        if kw in str(result):
            if "熔断" not in str(result):
                pytest.fail(f"结果包含错误关键词 '{kw}': {str(result)[:200]}")


def assert_has_data(result: str):
    """断言结果包含有效数据"""
    assert result, "结果为空"
    assert len(str(result)) > 10, f"结果太短: {result}"
    assert_not_error(result)


def assert_csv_format(result: str, min_rows: int = 1):
    """断言结果是 CSV 格式（跳过标题头部）"""
    lines = str(result).strip().split("\n")

    # 跳过标题头部（以 # 或 数据来源: 或 市场: 开头的行）
    csv_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("数据来源:") or stripped.startswith("市场:"):
            continue
        if stripped:  # 跳过空行
            csv_lines.append(stripped)

    assert len(csv_lines) >= min_rows + 1, f"CSV 行数不足 {min_rows + 1}: {len(csv_lines)}"
    assert "," in csv_lines[0], f"CSV 格式错误: 没有逗号分隔，第一行: {csv_lines[0]}"


def assert_has_source(result: str):
    """断言结果包含数据来源标识"""
    assert "数据来源:" in result, f"结果缺少数据来源标识: {result[:200]}"
