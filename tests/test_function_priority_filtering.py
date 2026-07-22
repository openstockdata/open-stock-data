"""已迁移：函数级数据源优先级由 default_routes.py 的静态 RouteSpec 决定。

原先测试 DataFetcherManager._get_fetchers_for / _function_priorities 的用例已废弃
（该机制随旧 failover 引擎一并移除）。路由级故障转移与顺序由
tests/test_route_executor.py 与 tests/test_spot_priority.py 覆盖。
"""
