class DataClassification:
    """Data sensitivity tiers governing model routing and storage encryption."""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"

    RANK = {
        "PUBLIC": 0,
        "INTERNAL": 1,
        "CONFIDENTIAL": 2,
        "RESTRICTED": 3,
    }


@dataclass
class PlatformSpec:
    """
    Defines the locked first usable product and its non-negotiable operational
    constraints.
    """
    product_name: str = "ULTRON-CORE // Sovereign Operator & Coding Agent Platform"
    version: str = "3.0.0-production"
    primary_use_case: str = (
        "Autonomous Infrastructure Operator, Codebase Architect, and DevSecOps Agent"
    )
    offline_first: bool = True
    enforce_dlp_privacy: bool = True
    allow_cloud_for_confidential: bool = False
    p95_latency_slo_ms: float = 850.0
    max_daily_budget_usd: float = 5.00
    max_tokens_per_run: int = 8192
    default_memory_ttl_days: int = 30


class KillSwitchException(RuntimeError):
    """Raised immediately when the global platform KillSwitch is engaged."""


class KillSwitch:
    """
    Non-negotiable hardware/software emergency circuit breaker.
    When engaged, immediately halts all active agent planning loops, blocks tool
    invocations, and rejects external model calls until explicitly reset by an
    authorized admin principal.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engaged: bool = False
        self._reason: str = "Nominal"
        self._triggered_by: str = "system"
        self._timestamp: Optional[float] = None

    def engage(self, reason: str, triggered_by: str = "system") -> Dict[str, Any]:
        with self._lock:
            self._engaged = True
            self._reason = reason
            self._triggered_by = triggered_by
            self._timestamp = time.time()
            return self.status()

    def reset(self, authorized_by: str) -> Dict[str, Any]:
        with self._lock:
            self._engaged = False
            self._reason = f"Reset by {authorized_by}"
            self._triggered_by = authorized_by
            self._timestamp = time.time()
            return self.status()

    def assert_clear(self) -> None:
        with self._lock:
            if self._engaged:
                raise KillSwitchException(
                    f"KILL SWITCH ENGAGED by '{self._triggered_by}': {self._reason}"
                )

    def status(self) -> Dict[str, Any]:
        return {
            "engaged": self._engaged,
            "reason": self._reason,
            "triggered_by": self._triggered_by,
            "timestamp": self._timestamp,
        }


class CostBudgetManager:
    """
    Tracks real-time token consumption and USD spend across local and cloud
    models, enforcing hard ceilings per task and per 24-hour window.
    """

    def __init__(self, spec: PlatformSpec) -> None:
        self.spec = spec
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_spend_usd: float = 0.0
        self.calls_by_tier: Dict[str, int] = {
            "local_ollama": 0,
            "cloud_api": 0,
            "deterministic_fallback": 0,
        }

    def check_can_spend(self, estimated_tokens: int, estimated_usd: float) -> Tuple[bool, str]:
        if estimated_tokens > self.spec.max_tokens_per_run:
            return False, (
                f"Token estimate ({estimated_tokens}) exceeds per-run ceiling "
                f"({self.spec.max_tokens_per_run})."
            )
        if (self.total_spend_usd + estimated_usd) > self.spec.max_daily_budget_usd:
            return False, (
                f"Projected spend (${self.total_spend_usd + estimated_usd:.4f}) "
                f"exceeds daily cap (${self.spec.max_daily_budget_usd:.2f})."
            )
        return True, "BUDGET_OK"

    def record_usage(
        self, tier: str, prompt_tokens: int, completion_tokens: int, cost_usd: float
    ) -> None:
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_spend_usd = round(self.total_spend_usd + cost_usd, 6)
        self.calls_by_tier[tier] = self.calls_by_tier.get(tier, 0) + 1

    def summary(self) -> Dict[str, Any]:
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_spend_usd": round(self.total_spend_usd, 6),
            "daily_limit_usd": self.spec.max_daily_budget_usd,
            "budget_remaining_usd": round(
                max(0.0, self.spec.max_daily_budget_usd - self.total_spend_usd), 6
            ),
            "calls_by_tier": dict(self.calls_by_tier),
        }
