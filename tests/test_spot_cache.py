"""已迁移：全市场快照缓存/完整性由 RouteExecutor 处理。

原先测试 DataFetcherManager.get_a_stock_spot + _get_dynamic_cached 的用例，
等价行为现由 tests/test_route_executor.py 覆盖：
  - test_partial_snapshot_is_rejected_and_only_complete_result_cached
  - test_snapshot_all_partial_fails_and_nothing_cached
  - test_fresh_cache_bypasses_providers
以及 tests/test_data_client.py::test_snapshot_returns_english_dataframe。
"""
