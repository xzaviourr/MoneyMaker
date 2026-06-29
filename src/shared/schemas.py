"""
Central data-model registry.  Every module imports from here — never define
domain types elsewhere to avoid circular imports.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


# ═══════════════════════════════════════════════════════════════════════════════
#  Enums
# ═══════════════════════════════════════════════════════════════════════════════

class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"   # Futures & Options
    MCX = "MCX"   # Commodities


class MarketRegimeTrend(str, Enum):
    TRENDING      = "trending"
    MEAN_REVERTING = "mean_reverting"
    CHOPPY        = "choppy"


class RiskPosture(str, Enum):
    RISK_ON  = "risk_on"
    RISK_OFF = "risk_off"


class VolatilityLevel(str, Enum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"
    CRISIS = "crisis"


class MarketBias(str, Enum):
    BULL    = "bull"
    BEAR    = "bear"
    NEUTRAL = "neutral"


class OrderType(str, Enum):
    MARKET    = "market"
    LIMIT     = "limit"
    STOP_LOSS = "stop_loss"
    SL_MARKET = "sl_market"


class OrderSide(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING          = "pending"
    OPEN             = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED           = "filled"
    CANCELLED        = "cancelled"
    REJECTED         = "rejected"
    EXPIRED          = "expired"


class PodState(str, Enum):
    SANDBOX    = "sandbox"
    PROBATION  = "probation"
    LIVE       = "live"
    REVIEW     = "review"
    KILLED     = "killed"


class GuardianResponseMode(str, Enum):
    ALERT     = "alert"
    HEDGE     = "hedge"
    LIQUIDATE = "liquidate"


class LLMTier(str, Enum):
    FAST      = "fast"
    STANDARD  = "standard"
    REASONING = "reasoning"
    DEEP      = "deep"
    EMBEDDING = "embedding"


class CircuitBreakerState(str, Enum):
    NORMAL    = "normal"
    WARNING   = "warning"
    TRIPPED   = "tripped"
    EMERGENCY = "emergency"


class SignalDirection(str, Enum):
    LONG    = "long"
    SHORT   = "short"
    NEUTRAL = "neutral"


class SignalStrength(str, Enum):
    WEAK       = "weak"
    MODERATE   = "moderate"
    STRONG     = "strong"
    VERY_STRONG = "very_strong"


class MessageType(str, Enum):
    # Market data
    QUOTE_UPDATE        = "quote_update"
    DATA_FETCHED        = "data_fetched"  # a real Yahoo Finance fetch just happened (not a cache hit)
    REGIME_CHANGE       = "regime_change"
    BAD_DATA_QUARANTINE = "bad_data_quarantine"
    # Pod lifecycle
    POD_SIGNAL          = "pod_signal"
    POD_STATE_CHANGE    = "pod_state_change"
    POD_PAUSED          = "pod_paused"
    POD_RESUMED         = "pod_resumed"
    # Orders
    ORDER_PLACED        = "order_placed"
    ORDER_FILLED        = "order_filled"
    ORDER_CANCELLED     = "order_cancelled"
    ORDER_REJECTED      = "order_rejected"
    # Capital
    CAPITAL_ALLOCATED   = "capital_allocated"
    CAPITAL_RETURNED    = "capital_returned"
    # Circuit breaker
    CIRCUIT_BREAKER_TRIGGERED = "circuit_breaker_triggered"
    CIRCUIT_BREAKER_RESET     = "circuit_breaker_reset"
    # Guardian
    GUARDIAN_ALERT      = "guardian_alert"
    GUARDIAN_HEDGE      = "guardian_hedge"
    GUARDIAN_LIQUIDATE  = "guardian_liquidate"
    # Long-term desk
    IDEA_APPROVED         = "idea_approved"
    IDEA_REJECTED         = "idea_rejected"
    ALLOCATION_PLAN_READY = "allocation_plan_ready"
    EXECUTION_COMPLETE    = "execution_complete"
    LT_EXECUTION_COMPLETE = "lt_execution_complete"  # alias for room3
    # Feedback
    TRADE_ATTRIBUTED      = "trade_attributed"
    TRADE_ATTRIBUTION     = "trade_attribution"      # alias
    PARAMETERS_UPDATED  = "parameters_updated"
    AGENT_WEIGHTS_UPDATED = "agent_weights_updated"
    # System
    SYSTEM_HEALTH_REPORT = "system_health_report"
    HUMAN_COMMAND        = "human_command"


# ═══════════════════════════════════════════════════════════════════════════════
#  Market Data
# ═══════════════════════════════════════════════════════════════════════════════

class Quote(BaseModel):
    symbol:    str
    exchange:  Exchange
    timestamp: datetime
    ltp:       Decimal
    open:      Decimal
    high:      Decimal
    low:       Decimal
    close:     Decimal
    volume:    int
    bid:       Optional[Decimal] = None
    ask:       Optional[Decimal] = None
    bid_qty:   Optional[int] = None
    ask_qty:   Optional[int] = None
    oi:        Optional[int] = None   # open interest (F&O)

    @property
    def spread(self) -> Optional[Decimal]:
        if self.bid and self.ask:
            return self.ask - self.bid
        return None


class OrderBookLevel(BaseModel):
    price:    Decimal
    quantity: int
    orders:   int = 1


class OrderBook(BaseModel):
    symbol:    str
    exchange:  Exchange
    timestamp: datetime
    bids: list[OrderBookLevel] = Field(default_factory=list)
    asks: list[OrderBookLevel] = Field(default_factory=list)

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def imbalance(self) -> float:
        bid_vol = sum(l.quantity for l in self.bids[:5])
        ask_vol = sum(l.quantity for l in self.asks[:5])
        total = bid_vol + ask_vol
        return (bid_vol - ask_vol) / total if total else 0.0


class RegimeSnapshot(BaseModel):
    timestamp:    datetime        = Field(default_factory=datetime.utcnow)
    trend:        MarketRegimeTrend = MarketRegimeTrend.CHOPPY
    risk_posture: RiskPosture     = RiskPosture.RISK_OFF
    volatility:   VolatilityLevel = VolatilityLevel.NORMAL
    bias:         MarketBias      = MarketBias.NEUTRAL
    vix:          Optional[float] = None
    nifty_adv_dec: Optional[float] = None  # advance/decline ratio
    confidence:   float           = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def is_crisis(self) -> bool:
        return self.volatility == VolatilityLevel.CRISIS

    @property
    def is_risk_on(self) -> bool:
        return self.risk_posture == RiskPosture.RISK_ON

    @property
    def regime_key(self) -> str:
        return f"{self.trend.value}_{self.risk_posture.value}_{self.volatility.value}"


class DataQualityAlert(BaseModel):
    id:            str      = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:     datetime = Field(default_factory=datetime.utcnow)
    symbol:        str
    exchange:      Exchange
    reason:        str
    severity:      str      # "warning" | "error" | "quarantine"
    is_quarantined: bool    = False


# ═══════════════════════════════════════════════════════════════════════════════
#  Broker / Orders
# ═══════════════════════════════════════════════════════════════════════════════

class AccountBalance(BaseModel):
    total:          Decimal
    available:      Decimal
    used_margin:    Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl:   Decimal = Decimal("0")


class Position(BaseModel):
    id:            str      = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol:        str
    exchange:      Exchange
    quantity:      int
    average_price: Decimal
    current_price: Decimal  = Decimal("0")
    side:          OrderSide
    realized_pnl:   Decimal = Decimal("0")
    stop_loss:      Optional[Decimal] = None
    take_profit:    Optional[Decimal] = None
    max_hold_until: Optional[datetime] = None
    trailing_stop_pct: Optional[float] = None
    source_pod:     Optional[str] = None
    source_desk:    Optional[str] = None
    strategy:       Optional[str] = None
    opened_at:      datetime = Field(default_factory=datetime.utcnow)
    updated_at:     datetime = Field(default_factory=datetime.utcnow)

    @property
    def market_value(self) -> Decimal:
        return self.current_price * self.quantity

    @property
    def cost_basis(self) -> Decimal:
        return self.average_price * self.quantity

    # computed, not stored — derives live from current_price every time it's
    # read, instead of a stored field that nothing ever updated after entry
    # (which is why unrealized P&L always showed ₹0 regardless of price moves)
    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl(self) -> Decimal:
        mult = 1 if self.side == OrderSide.BUY else -1
        return (self.current_price - self.average_price) * self.quantity * mult

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> float:
        if self.average_price == 0:
            return 0.0
        mult = 1 if self.side == OrderSide.BUY else -1
        return float((self.current_price - self.average_price) / self.average_price * 100) * mult


class Order(BaseModel):
    id:                str          = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol:            str
    exchange:          Exchange
    side:              OrderSide
    order_type:        OrderType
    quantity:          int
    price:             Optional[Decimal] = None
    trigger_price:     Optional[Decimal] = None
    stop_loss:         Optional[Decimal] = None
    take_profit:       Optional[Decimal] = None
    max_hold_until:    Optional[datetime] = None
    status:            OrderStatus   = OrderStatus.PENDING
    filled_quantity:   int           = 0
    average_fill_price: Optional[Decimal] = None
    source_pod:        Optional[str] = None
    source_desk:       Optional[str] = None
    strategy:          Optional[str] = None
    created_at:        datetime      = Field(default_factory=datetime.utcnow)
    updated_at:        datetime      = Field(default_factory=datetime.utcnow)
    broker_order_id:   Optional[str] = None
    tag:               Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED,
                               OrderStatus.REJECTED, OrderStatus.EXPIRED)

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity


class OrderResult(BaseModel):
    order_id:          str
    broker_order_id:   Optional[str]     = None
    status:            OrderStatus
    filled_quantity:   int                = 0
    average_fill_price: Optional[Decimal] = None
    rejection_reason:  Optional[str]     = None
    timestamp:         datetime           = Field(default_factory=datetime.utcnow)


class Trade(BaseModel):
    id:           str      = Field(default_factory=lambda: str(uuid.uuid4()))
    trade_id:     str      = ""     # broker trade ID (may differ from internal id)
    order_id:     str      = ""
    symbol:       str
    exchange:     Exchange
    side:         OrderSide
    direction:    Optional["SignalDirection"] = None
    quantity:     int
    price:        Decimal
    entry_price:  Decimal  = Decimal("0")
    exit_price:   Optional[Decimal] = None
    realized_pnl: Optional[float]   = None
    slippage_cost: float   = 0.0
    entry_time:   Optional[datetime] = None
    exit_time:    Optional[datetime] = None
    timestamp:    datetime = Field(default_factory=datetime.utcnow)
    source_pod:   Optional[str] = None
    source_desk:  Optional[str] = None
    strategy:     Optional[str] = None
    regime_at_entry: Optional[RegimeSnapshot] = None

    def model_post_init(self, __context: Any) -> None:
        if self.entry_price == 0 and self.price:
            object.__setattr__(self, "entry_price", self.price)
        if not self.trade_id:
            object.__setattr__(self, "trade_id", self.id)


class TradeCostEstimate(BaseModel):
    symbol:               str
    exchange:             Exchange
    quantity:             int
    order_type:           OrderType
    commission:           Decimal
    spread_cost:          Decimal
    market_impact:        Decimal
    slippage:             Decimal
    borrow_cost:          Decimal = Decimal("0")
    overnight_financing:  Decimal = Decimal("0")
    total_cost:           Decimal
    breakeven_move_pct:   float


# ═══════════════════════════════════════════════════════════════════════════════
#  Trading Signals
# ═══════════════════════════════════════════════════════════════════════════════

class TradeSignal(BaseModel):
    id:                 str           = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol:             str
    exchange:           Exchange
    direction:          SignalDirection
    strength:           SignalStrength
    strategy:           str
    conviction:         float          = Field(ge=0.0, le=1.0)
    entry_price:        Optional[Decimal] = None
    stop_loss:          Optional[Decimal] = None
    take_profit:        Optional[Decimal] = None
    timeframe:          str
    regime_compatible:  list[MarketRegimeTrend] = Field(default_factory=list)
    rationale:          str            = ""
    created_at:         datetime       = Field(default_factory=datetime.utcnow)
    expires_at:         Optional[datetime] = None
    metadata:           dict[str, Any] = Field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at


class StrategySignal(BaseModel):
    id:                   str      = Field(default_factory=lambda: str(uuid.uuid4()))
    strategy_name:        str
    symbol:               str
    exchange:             Exchange
    direction:            SignalDirection
    conviction:           float    = Field(ge=0.0, le=1.0)
    timeframe:            str
    rationale:            str
    supporting_indicators: dict[str, Any] = Field(default_factory=dict)
    created_at:           datetime = Field(default_factory=datetime.utcnow)
    expires_at:           Optional[datetime] = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at


class IdeaQueueItem(BaseModel):
    id:                    str      = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol:                str
    exchange:              Exchange
    direction:             SignalDirection
    conviction_score:      float    = Field(ge=0.0, le=1.0)
    supporting_strategies: list[str] = Field(default_factory=list)
    contradicting_strategies: list[str] = Field(default_factory=list)
    signals:               list[StrategySignal] = Field(default_factory=list)
    created_at:            datetime = Field(default_factory=datetime.utcnow)
    expires_at:            Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Long-Term Desk
# ═══════════════════════════════════════════════════════════════════════════════

class AgentVote(BaseModel):
    agent_id:   str
    verdict:    str   # "approve" | "reject" | "abstain"
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning:  str
    weight:     float = Field(default=1.0, ge=0.0)
    key_points: list[str] = Field(default_factory=list)


class IdeaVerdict(BaseModel):
    idea_id:            str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Core identity
    symbol:             str = ""
    exchange:           Optional["Exchange"] = None
    direction:          Optional["SignalDirection"] = None
    # Decision
    approved:           bool = False
    trade_approved:     bool = False          # alias kept for backwards compat
    final_conviction:   float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_score:   float = Field(default=0.0, ge=0.0, le=1.0)  # alias
    position_tier:      str   = "starter"     # "full" | "half" | "starter"
    # Debate artefacts
    votes:              list[AgentVote] = Field(default_factory=list)
    dissenting_agents:  list[str]       = Field(default_factory=list)
    key_risks_raised:   list[str]       = Field(default_factory=list)
    conditions:         list[str]       = Field(default_factory=list)
    reasoning:          str             = ""
    reasoning_summary:  str             = ""  # alias
    timestamp:          datetime        = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        # Keep aliases in sync
        if self.approved and not self.trade_approved:
            object.__setattr__(self, "trade_approved", self.approved)
        if self.trade_approved and not self.approved:
            object.__setattr__(self, "approved", self.trade_approved)
        if self.final_conviction > 0 and self.confidence_score == 0:
            object.__setattr__(self, "confidence_score", self.final_conviction)
        if self.reasoning and not self.reasoning_summary:
            object.__setattr__(self, "reasoning_summary", self.reasoning)

    @property
    def weighted_approval_pct(self) -> float:
        total_weight = sum(v.weight for v in self.votes)
        if total_weight == 0:
            return 0.0
        approve_weight = sum(v.weight for v in self.votes if v.verdict == "approve")
        return approve_weight / total_weight


class SellCandidate(BaseModel):
    position_id:       str = ""
    symbol:            str
    action:            str = "full_exit"   # "full_exit"|"trim_50pct"|"trim_25pct"
    quantity_to_sell:  int = 0
    rationale:         str
    urgency:           str = "gradual"     # "immediate"|"gradual"
    opportunity_cost:  Optional[float]   = None
    pnl_impact:        Optional[Decimal] = None
    tax_impact:        Optional[Decimal] = None


class AllocationPlan(BaseModel):
    id:                  str      = Field(default_factory=lambda: str(uuid.uuid4()))
    idea_id:             str      = ""
    # New unified fields
    symbol:              str      = ""
    exchange:            Optional["Exchange"]        = None
    direction:           Optional["SignalDirection"] = None
    quantity:            int      = 0
    allocated_capital:   float    = 0.0
    position_tier:       str      = "starter"
    trim_candidates:     list[dict[str, Any]] = Field(default_factory=list)
    cost_estimate_inr:   float    = 0.0
    conviction:          float    = 0.0
    reasoning:           str      = ""
    # Exit plan — carried from Room 1's bull/bear debate so the position
    # actually gets closed instead of held forever once it's opened.
    target_pct_upside:      float = 10.0
    stop_loss_pct_downside: float = 5.0
    time_horizon_weeks:     int   = 8
    # Legacy fields kept for compatibility
    sell_candidates:     list[SellCandidate] = Field(default_factory=list)
    buy_symbol:          str      = ""
    buy_exchange:        Optional["Exchange"] = None
    buy_quantity:        int      = 0
    buy_limit_price:     Optional[Decimal] = None
    net_capital_freed:   Decimal  = Decimal("0")
    portfolio_delta:     dict[str, Any] = Field(default_factory=dict)
    position_size_rationale: str = ""
    risk_metrics:        dict[str, Any] = Field(default_factory=dict)
    created_at:          datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        if self.symbol and not self.buy_symbol:
            object.__setattr__(self, "buy_symbol", self.symbol)
        if self.exchange and not self.buy_exchange:
            object.__setattr__(self, "buy_exchange", self.exchange)
        if self.quantity and not self.buy_quantity:
            object.__setattr__(self, "buy_quantity", self.quantity)


class ExecutionPlan(BaseModel):
    id:                   str      = Field(default_factory=lambda: str(uuid.uuid4()))
    allocation_plan_id:   str      = ""
    allocation_plan:      Optional[AllocationPlan] = None
    # Decision
    status:               str      = "pending"  # "executed"|"partial"|"blocked"|"deferred"
    reason:               str      = ""
    defer_to:             Optional[str] = None
    orders_placed:        list[dict[str, Any]] = Field(default_factory=list)
    # Risk gate results
    risk_approved:        bool     = False
    risk_veto_reason:     Optional[str] = None
    execution_window:     str      = "open"
    execution_algorithm:  str      = "market"
    var_delta:            Optional[float] = None
    tail_risk_flags:      list[str] = Field(default_factory=list)
    created_at:           datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  Pods
# ═══════════════════════════════════════════════════════════════════════════════

class PodConfig(BaseModel):
    pod_id:                str
    pod_name:              str
    strategy:              str
    timeframe:             str
    compatible_regimes:    list[MarketRegimeTrend]
    capital_budget:        Decimal = Decimal("0")
    max_daily_drawdown_pct: float  = 2.0
    max_position_size_pct: float   = 20.0
    stop_loss_pct:         float   = 1.5
    take_profit_pct:       float   = 3.0
    max_holding_minutes:   int     = 375  # one trading day — square off if still open
    max_open_positions:    int     = 5
    state:                 PodState = PodState.SANDBOX
    params:                dict[str, Any] = Field(default_factory=dict)


class PodMetrics(BaseModel):
    pod_id:          str
    total_trades:    int     = 0
    winning_trades:  int     = 0
    losing_trades:   int     = 0
    win_rate:        float   = 0.0
    total_pnl:       Decimal = Decimal("0")
    daily_pnl:       Decimal = Decimal("0")
    sharpe_ratio:    float   = 0.0
    max_drawdown:    float   = 0.0
    current_drawdown: float  = 0.0
    regime_accuracy: dict[str, float] = Field(default_factory=dict)
    updated_at:      datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_underperforming(self) -> bool:
        return self.sharpe_ratio < 0.5 or self.current_drawdown > 5.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Guardian
# ═══════════════════════════════════════════════════════════════════════════════

class GuardianAlert(BaseModel):
    id:                  str      = Field(default_factory=lambda: str(uuid.uuid4()))
    mode:                GuardianResponseMode
    symbol:              Optional[str] = None
    position_id:         Optional[str] = None
    severity:            str           = "info"
    reason:              str
    recommended_action:  Optional[str] = None
    auto_executed:       bool          = False
    hedge_instrument:    Optional[str] = None
    timestamp:           datetime      = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════════════

class CircuitBreakerEvent(BaseModel):
    id:              str                 = Field(default_factory=lambda: str(uuid.uuid4()))
    trigger:         str
    state:           CircuitBreakerState
    daily_pnl_pct:   float               = 0.0
    action_taken:    str
    affected_pods:   list[str]           = Field(default_factory=list)
    timestamp:       datetime            = Field(default_factory=datetime.utcnow)
    reset_at:        Optional[datetime]  = None


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM
# ═══════════════════════════════════════════════════════════════════════════════

class LLMRequest(BaseModel):
    agent_id:        str
    tier:            LLMTier
    system_prompt:   str
    user_prompt:     str
    max_tokens:      int   = 2048
    temperature:     float = 0.1
    json_mode:       bool  = False


class LLMResponse(BaseModel):
    agent_id:          str
    tier:              LLMTier
    content:           str
    prompt_tokens:     int
    completion_tokens: int
    latency_ms:        float
    cost_usd:          float
    model_id:          str
    timestamp:         datetime = Field(default_factory=datetime.utcnow)


class LLMUsageRecord(BaseModel):
    id:                str      = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id:          str
    tier:              LLMTier
    model_id:          str
    prompt_tokens:     int
    completion_tokens: int
    total_tokens:      int
    cost_usd:          float
    latency_ms:        float
    timestamp:         datetime = Field(default_factory=datetime.utcnow)
    trade_id:          Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Feedback & Attribution
# ═══════════════════════════════════════════════════════════════════════════════

class TradeAttribution(BaseModel):
    trade_id:              str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Execution audit fields (from PostTradeAuditor)
    symbol:                str = ""
    exchange:              Optional["Exchange"]        = None
    direction:             Optional["SignalDirection"] = None
    planned_price:         float   = 0.0
    executed_price:        float   = 0.0
    planned_quantity:      int     = 0
    executed_quantity:     int     = 0
    slippage_bps:          float   = 0.0
    execution_quality:     str     = "good"  # "good"|"acceptable"|"poor"
    failed_slices:         int     = 0
    source_agent:          str     = ""
    strategy:              str     = "unknown"  # which strategy/pod made the call — used to bucket performance stats
    timestamp:             datetime = Field(default_factory=datetime.utcnow)
    # P&L attribution fields
    total_pnl:             Decimal = Decimal("0")
    signal_contribution:   float   = 0.0
    execution_contribution: float  = 0.0
    timing_contribution:   float   = 0.0
    luck_contribution:     float   = 0.0
    regime_at_entry:       Optional[RegimeSnapshot] = None
    regime_at_exit:        Optional[RegimeSnapshot] = None
    holding_period_hours:  float   = 0.0
    slippage_cost:         Decimal = Decimal("0")
    calculated_at:         datetime = Field(default_factory=datetime.utcnow)


class ParameterUpdate(BaseModel):
    pod_id:         str
    strategy:       str
    parameter_name: str
    old_value:      Any
    new_value:      Any
    reason:         str
    confidence:     float
    regime_context: Optional[str] = None
    timestamp:      datetime      = Field(default_factory=datetime.utcnow)


class AgentWeight(BaseModel):
    agent_id:    str
    role:        str
    current_weight: float = 1.0
    accuracy:    float    = 0.0
    precision_by_conviction: dict[str, float] = Field(default_factory=dict)
    total_votes: int      = 0
    correct_votes: int    = 0
    updated_at:  datetime = Field(default_factory=datetime.utcnow)

    @property
    def accuracy_rate(self) -> float:
        if self.total_votes == 0:
            return 0.0
        return self.correct_votes / self.total_votes


# ═══════════════════════════════════════════════════════════════════════════════
#  Decision Trace / Explainability
# ═══════════════════════════════════════════════════════════════════════════════

class AgentDecision(BaseModel):
    agent_id:      str
    agent_role:    str
    input_summary: str
    reasoning:     str
    output:        Any
    llm_tier:      Optional[LLMTier] = None
    llm_cost_usd:  float             = 0.0
    timestamp:     datetime          = Field(default_factory=datetime.utcnow)


class DecisionTrace(BaseModel):
    id:              str      = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_type:   str      # "intraday_signal" | "lt_idea" | "guardian_action"
    symbol:          Optional[str] = None
    final_action:    str
    agent_decisions: list[AgentDecision] = Field(default_factory=list)
    regime_at_time:  Optional[RegimeSnapshot] = None
    outcome_pnl:     Optional[Decimal] = None
    outcome_recorded_at: Optional[datetime] = None
    total_llm_cost_usd: float = 0.0
    created_at:      datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  Capital
# ═══════════════════════════════════════════════════════════════════════════════

class PillarAllocation(BaseModel):
    pillar:    str
    allocated: Decimal
    deployed:  Decimal
    available: Decimal
    pnl:       Decimal = Decimal("0")

    @property
    def utilization_pct(self) -> float:
        if self.allocated == 0:
            return 0.0
        return float(self.deployed / self.allocated * 100)


class CapitalSnapshot(BaseModel):
    total_capital:     Decimal
    available_capital: Decimal
    deployed_capital:  Decimal
    reserved_capital:  Decimal
    total_pnl:         Decimal
    daily_pnl:         Decimal = Decimal("0")
    pillar_allocations: dict[str, PillarAllocation] = Field(default_factory=dict)
    timestamp:         datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  Message Bus
# ═══════════════════════════════════════════════════════════════════════════════

class Message(BaseModel):
    id:        str         = Field(default_factory=lambda: str(uuid.uuid4()))
    type:      MessageType
    source:    str
    payload:   Any
    timestamp: datetime    = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  Human Commands
# ═══════════════════════════════════════════════════════════════════════════════

class HumanCommand(BaseModel):
    id:         str      = Field(default_factory=lambda: str(uuid.uuid4()))
    command:    str      # pause_pod | resume_pod | force_exit | block_asset | set_limit
    target:     Optional[str]      = None
    parameters: dict[str, Any]     = Field(default_factory=dict)
    issued_by:  str                = "human"
    timestamp:  datetime           = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
#  System Health
# ═══════════════════════════════════════════════════════════════════════════════

class SystemHealthReport(BaseModel):
    id:                   str                  = Field(default_factory=lambda: str(uuid.uuid4()))
    total_capital:        Decimal              = Decimal("0")
    active_pods:          int                  = 0
    active_positions:     int                  = 0
    daily_pnl:            Decimal              = Decimal("0")
    monthly_pnl:          Decimal              = Decimal("0")
    sharpe_ytd:           float                = 0.0
    circuit_breaker_state: Optional[CircuitBreakerState] = None
    regime:               Optional[RegimeSnapshot]       = None
    top_performing_pods:  list[str]            = Field(default_factory=list)
    underperforming_pods: list[str]            = Field(default_factory=list)
    alerts:               list[GuardianAlert]  = Field(default_factory=list)
    llm_costs_today_usd:  float                = 0.0
    # Weekly review fields
    overall_health_score: float                = 0.0
    summary:              str                  = ""
    working_well:         list[str]            = Field(default_factory=list)
    underperforming:      list[str]            = Field(default_factory=list)
    pod_actions:          list[dict[str, Any]] = Field(default_factory=list)
    regime_notes:         str                  = ""
    generated_at:         datetime             = Field(default_factory=datetime.utcnow)
