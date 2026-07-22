"""已迁移：批量实时行情由 RouteExecutor.execute_batch 处理。

原先测试 DataFetcherManager.prefetch_realtime_quotes（小批量逐票 / 大批量原生批量）
的用例已废弃。等价的"优先原生批量 + remaining 逐票回退"行为由
tests/test_route_executor.py 覆盖：
  - test_native_batch_covers_all_codes_in_single_call
  - test_native_batch_remaining_covered_by_next_provider
  - test_native_batch_uncovered_code_falls_back_to_single_quote
以及 tests/test_data_client.py 的 batch_realtime_quotes 用例。
"""
