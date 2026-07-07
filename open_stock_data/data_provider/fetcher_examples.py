"""
Fetcher 基类改进示例

演示如何在现有 Fetcher 中集成能力声明和装饰器。
"""

# 这是一个参考示例，展示如何修改现有 Fetcher
# 实际修改时应该逐个 Fetcher 文件进行

from typing import Optional
import os
import logging

from .base import BaseFetcher
from .capability import FetcherCapability
from .capability_definitions import (
    create_tickflow_capability,
    create_tushare_capability,
    create_efinance_capability,
)

_LOGGER = logging.getLogger(__name__)


# ==================== 示例 1: TickflowFetcher 改造 ====================

class TickflowFetcherImproved(BaseFetcher):
    """
    改进的 TickflowFetcher，集成能力声明
    """

    name = "TickflowFetcher"
    priority = -1
    backend_group = "tickflow"

    def __init__(self):
        super().__init__()
        self._api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
        configured_url = os.getenv("TICKFLOW_API_URL", "").strip()

        # 设置基础 URL
        if self._api_key:
            default_url = "https://api.tickflow.org"
        else:
            default_url = "https://free-api.tickflow.org"
        self._base_url = (configured_url or default_url).rstrip("/")

        # 声明能力
        self.capability = create_tickflow_capability()

        # 设置可用性（继承基类的 _available 属性）
        self._available = self.capability.is_available()

        if self._api_key:
            _LOGGER.info(f"TickFlow 初始化成功: {self._base_url} (认证)")
        else:
            _LOGGER.info(f"TickFlow 初始化成功: {self._base_url} (免费服务)")

    def get_realtime_quote(self, stock_code: str):
        """
        获取实时行情

        现在自动检查能力声明，无需手动判断 API key
        """
        from .capability import DataType

        # 能力检查（可选，装饰器也会检查）
        if not self.capability.supports_data_type(DataType.REALTIME_QUOTE):
            _LOGGER.debug(f"[{self.name}] 未配置 API Key，跳过实时行情")
            return None

        # 原有实现...
        pass


# ==================== 示例 2: TushareFetcher 改造 ====================

class TushareFetcherImproved(BaseFetcher):
    """
    改进的 TushareFetcher，集成能力声明
    """

    name = "TushareFetcher"
    priority = 0
    backend_group = "tushare"

    def __init__(self):
        super().__init__()
        self._api = None

        # 声明能力（在初始化 API 之前）
        self.capability = create_tushare_capability()

        # 根据能力声明决定是否初始化
        if not self.capability.is_available():
            _LOGGER.info("未配置 TUSHARE_TOKEN，TushareFetcher 不可用")
            self._available = False
            return

        # 初始化 Tushare API
        token = os.getenv("TUSHARE_TOKEN")
        try:
            import tushare as ts
            ts.set_token(token)
            self._api = ts.pro_api()
            self._available = True
            _LOGGER.info("Tushare API 初始化成功")
        except Exception as e:
            _LOGGER.warning(f"Tushare API 初始化失败: {e}")
            self._available = False


# ==================== 示例 3: EfinanceFetcher 改造 ====================

class EfinanceFetcherImproved(BaseFetcher):
    """
    改进的 EfinanceFetcher，集成能力声明
    """

    name = "EfinanceFetcher"
    priority = 1
    backend_group = "eastmoney"

    # 后端失败作用域映射（保持原有逻辑）
    _BACKEND_FAILURE_SCOPE_MAP = {
        "get_realtime_quote": "eastmoney:push2:realtime_quotes",
        "get_batch_realtime_quotes": "eastmoney:push2:realtime_quotes",
        "get_a_stock_spot": "eastmoney:push2:realtime_quotes",
        "get_belong_board": "eastmoney:push2:slist_get",
        "get_fund_flow": "eastmoney:http:push2his:fund_flow",
        "get_billboard": "eastmoney:datacenter:daily_billboard",
    }

    def __init__(self):
        super().__init__()

        # 声明能力
        self.capability = create_efinance_capability()

        # 延迟导入
        try:
            import efinance as ef
            self._ef = ef
            self._available = True
        except ImportError:
            _LOGGER.warning("efinance 库未安装")
            self._available = False


# ==================== 示例 4: DataFetcherManager 使用装饰器 ====================

class DataFetcherManagerImproved:
    """
    展示如何在 DataFetcherManager 中使用新的装饰器
    """

    def __init__(self):
        self._fetchers = []
        # ... 其他初始化

    # ==================== 原有方法改造 ====================

    # 方式1: 使用装饰器（推荐）
    from .failover import with_failover
    from .capability import DataType

    @with_failover(
        circuit_breaker_name="realtime",
        validate_result=lambda q: q is not None and hasattr(q, 'has_basic_data') and q.has_basic_data(),
        cache_result=True,
        data_type=DataType.REALTIME_QUOTE,
    )
    def get_realtime_quote_v2(self, stock_code: str, stock_type=None):
        """
        使用装饰器的版本：代码简洁，逻辑统一

        装饰器自动处理：
        - 熔断器检查
        - 后端分组故障
        - 能力声明检查
        - 异常处理
        - 日志记录
        """
        # 装饰器会自动遍历 fetchers 并调用这个方法
        # 这里只需要声明方法签名，实际调用由装饰器完成
        pass

    # 方式2: 手动使用协调器（兼容现有代码）
    def get_realtime_quote_manual(self, stock_code: str, stock_type=None):
        """
        手动使用协调器的版本：保持对现有代码的兼容
        """
        from .failover import FailoverCoordinator, FetchContext
        from .circuit_breaker import get_circuit_breaker
        from .capability import DataType, Market

        # 构建上下文
        context = FetchContext(
            method_name="get_realtime_quote",
            args=(stock_code,),
            kwargs={'stock_type': stock_type},
            data_type=DataType.REALTIME_QUOTE,
            market=self._infer_market(stock_code, stock_type),
        )

        circuit_breaker = get_circuit_breaker("realtime")
        coordinator = FailoverCoordinator()

        for fetcher in self._get_fetchers_for("get_realtime_quote", stock_type):
            # 统一跳过检查
            should_skip, reason = coordinator.should_skip_fetcher(
                fetcher, circuit_breaker, context, self
            )
            if should_skip:
                _LOGGER.debug(f"[{fetcher.name}] 跳过: {reason}")
                continue

            try:
                quote = fetcher.get_realtime_quote(stock_code)
                if quote is not None and quote.has_basic_data():
                    circuit_breaker.record_success(fetcher.name)
                    return quote
            except Exception as e:
                # 统一错误处理
                coordinator.handle_fetch_error(
                    fetcher, e, context, circuit_breaker, self
                )
                continue

        return None

    def _infer_market(self, stock_code: str, stock_type) -> Optional[Market]:
        """推断市场类型"""
        from .stock_code import detect_stock_type
        from .capability import Market

        if not stock_type:
            stock_type = detect_stock_type(stock_code)

        market_map = {
            'a_stock': Market.A_STOCK,
            'hk_stock': Market.HK_STOCK,
            'us_stock': Market.US_STOCK,
            'etf': Market.ETF,
        }

        if stock_type and hasattr(stock_type, 'value'):
            return market_map.get(stock_type.value)
        return None


# ==================== 向后兼容的渐进式迁移策略 ====================

"""
迁移策略：

1. 阶段1（向后兼容）：
   - 新增 capability 属性到所有 Fetcher
   - 保持现有方法签名和逻辑不变
   - 在 __init__ 中添加能力声明

   示例：
   class TushareFetcher(BaseFetcher):
       def __init__(self):
           super().__init__()
           self.capability = create_tushare_capability()  # 新增
           # ... 现有初始化逻辑

2. 阶段2（逐步替换）：
   - 选择1-2个简单方法使用装饰器
   - 验证功能正确性
   - 逐步推广到其他方法

   示例：
   # 原有方法保持不变
   def get_chip_distribution(self, stock_code: str):
       # ... 现有逻辑

   # 新方法使用装饰器
   @with_failover(...)
   def get_realtime_quote(self, stock_code: str):
       pass

3. 阶段3（完全迁移）：
   - 所有方法使用装饰器
   - 删除重复的故障转移代码
   - 简化 DataFetcherManager

迁移收益：
- 阶段1: 增强可观测性，明确能力边界
- 阶段2: 减少重复代码，统一错误处理
- 阶段3: 大幅简化代码，降低维护成本
"""
