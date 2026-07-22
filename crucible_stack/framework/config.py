import yaml
from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field, ValidationError

# ==========================================
# PYDANTIC SCHEMAS (Strict Type Enforcement)
# ==========================================

class MetaConfig(BaseModel):
    strategy_name: str
    strategy_type: Literal["ml", "rules"]
    description: Optional[str] = ""
    author: Optional[str] = "Unknown"
    version: str

class ExecutionConfig(BaseModel):
    engine: Literal["native", "realtest"] 
    initial_capital: float
    commission_per_trade: float = 0.0
    slippage_ticks: int = 0
    execution_price: Literal["Open", "Close"] = "Open"

class AssetConfig(BaseModel):
    symbol: str

class DataConfig(BaseModel):
    start_date: str
    end_date: str
    resolution: str
    assets: List[AssetConfig] = Field(default_factory=list)
    # Holdout-tier: name whole asset classes; expanded to member symbols and
    # pooled per class. Either assets, asset_classes, or both.
    asset_classes: Optional[List[str]] = None

class BarrierDefinitions(BaseModel):
    upper_barrier_atr: float
    lower_barrier_atr: float
    timeout_bars: int

class MLConfig(BaseModel):
    enabled: bool = False
    model_type: str = "xgboost"
    prediction_target: str = "triple_barrier"
    barrier_definitions: BarrierDefinitions

class EntryLogic(BaseModel):
    min_tp_probability: List[float]
    max_sl_probability: List[float]
    armed_window_bars: List[int]
    reversal_patterns: List[str]

class ExitLogic(BaseModel):
    take_profit_atr: float
    macro_neutral_line: int
    max_stop_loss_atr: float

class StrategySpaceConfig(BaseModel):
    entry_logic: EntryLogic
    exit_logic: ExitLogic

class AlphaValidatorConfig(BaseModel):
    enabled: bool = True
    run_alphalens: bool = True
    min_information_coefficient: float = 0.05
    quantiles: int = 5

class PardoWFMConfig(BaseModel):
    objective_function: str = "SQN"
    wfa_steps: int = 10
    out_of_sample_length_days: int = 180
    in_sample_matrix: List[int] = Field(default=[730])

class WFCGateConfig(BaseModel):
    enabled: bool = True
    correlation_method: Literal["spearman", "pearson", "kendall"] = "spearman"
    min_correlation_threshold: float = 0.30
    require_positive_oos: bool = True

class StrategyConfig(BaseModel):
    """Rules-strategy selector: names a class in strategies.STRATEGY_REGISTRY and
    carries its free-form params. `mode='fixed'` evaluates fixed params across the
    walk-forward windows; `mode='wfo'` grid-tunes `param_grid` on each IS window."""
    name: str
    mode: Literal["fixed", "wfo"] = "fixed"
    params: Dict[str, Any] = Field(default_factory=dict)
    param_grid: Optional[Dict[str, list]] = None
    objective: str = "expectancy"

class HoldoutConfig(BaseModel):
    """Tier-1 holdout screen: a single early/late temporal split."""
    split: str = "2019-01-01"
    embargo_weeks: int = 8

class SizingConfig(BaseModel):
    """Account-level position sizing for the portfolio Monte Carlo / deploy book.
    `flat` = constant risk_frac per trade. `count_cap` = concurrency-aware: cap
    concurrent gross at ~concurrency_cap positions, calibrated to the same average
    risk (redistributes risk off crowded moments; lower account DD, matched CAGR)."""
    policy: Literal["flat", "count_cap"] = "flat"
    risk_frac: float = 0.005                  # target AVERAGE per-trade risk fraction
    concurrency_cap: Optional[int] = None     # K; None -> median concurrency (structural)

# The Master Object that holds everything
class MasterConfig(BaseModel):
    meta: MetaConfig
    execution: ExecutionConfig
    data: DataConfig
    # ML-only blocks are optional so a rules config validates without them.
    ml_module: Optional[MLConfig] = None
    strategy_space: Optional[StrategySpaceConfig] = None
    alpha_validator: Optional[AlphaValidatorConfig] = None
    # Rules-strategy selector (required for strategy_type == 'rules').
    strategy: Optional[StrategyConfig] = None
    holdout: Optional[HoldoutConfig] = None
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    asset_metadata: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # WFA-tier blocks — defaulted so a holdout-only config can omit them.
    pardo_wfm: PardoWFMConfig = Field(default_factory=PardoWFMConfig)
    wfc_gate: WFCGateConfig = Field(default_factory=WFCGateConfig)

# ==========================================
# PARSER LOGIC
# ==========================================

def load_config(file_path: str) -> MasterConfig:
    """
    Reads a YAML file and validates it against the MasterConfig Pydantic schema.
    """
    with open(file_path, 'r') as file:
        try:
            yaml_dict = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise ValueError(f"Error reading YAML file: {exc}")

    try:
        # This unpacks the dictionary and validates types instantly
        config = MasterConfig(**yaml_dict)
        return config
    except ValidationError as e:
        print("\n[!] CRITICAL: Configuration Validation Failed!")
        print("Please check your YAML file for the following errors:")
        print(e)
        raise e