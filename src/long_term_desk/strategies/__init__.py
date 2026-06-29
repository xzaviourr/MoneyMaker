from .base_strategy import BaseStrategy
from .trend_following import TrendFollowingAgent
from .fundamentals import FundamentalsAgent
from .earnings_alpha import EarningsAlphaAgent
from .chart_pattern import ChartPatternAgent
from .momentum_surf import MomentumSurfAgent
from .sentiment import SentimentAgent
from .macro_regime import MacroRegimeAgent
from .insider_flow import InsiderFlowAgent
from .stat_arb import StatArbAgent
from .breakout import BreakoutAgent
from .mean_reversion import MeanReversionLTAgent
from .volume_profile import VolumeProfileAgent
from .order_flow import OrderFlowAgent
from .short_interest import ShortInterestAgent
from .catalyst_hunter import CatalystHunterAgent

ALL_STRATEGIES: list[type[BaseStrategy]] = [
    TrendFollowingAgent, FundamentalsAgent, EarningsAlphaAgent,
    ChartPatternAgent, MomentumSurfAgent, SentimentAgent,
    MacroRegimeAgent, InsiderFlowAgent, StatArbAgent,
    BreakoutAgent, MeanReversionLTAgent, VolumeProfileAgent,
    OrderFlowAgent, ShortInterestAgent, CatalystHunterAgent,
]
