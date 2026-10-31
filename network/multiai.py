@dataclass
class AIProviderConfig:
    """Configuration metadata for an integrated AI model provider."""
    provider_id: str
    display_name: str
    protocol: str  # "gemini" | "openai_compat" | "anthropic" | "ollama" | "local_synth"
    base_url: str
    env_key_name: str
    default_model: str
    available_models: List[str]
    cost_per_1k_input_usd: float
    cost_per_1k_output_usd: float
    is_local: bool = False
    strengths: List[str] = field(default_factory=list)


DEFAULT_AI_PROVIDERS: Dict[str, AIProviderConfig] = {
    "gemini": AIProviderConfig(
        provider_id="gemini",
        display_name="Google Gemini",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/models",
        env_key_name="GEMINI_API_KEY",
        default_model="gemini-2.5-flash",
        available_models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        cost_per_1k_input_usd=0.00015,
        cost_per_1k_output_usd=0.00060,
        is_local=False,
        strengths=["multimodal", "fast", "reasoning", "long_context"],
    ),
    "openai": AIProviderConfig(
        provider_id="openai",
        display_name="OpenAI ChatGPT",
        protocol="openai_compat",
        base_url="https://api.openai.com/v1/chat/completions",
        env_key_name="OPENAI_API_KEY",
        default_model="gpt-4o",
        available_models=["gpt-4o", "gpt-4o-mini", "o3-mini", "gpt-4.1"],
        cost_per_1k_input_usd=0.00250,
        cost_per_1k_output_usd=0.01000,
        is_local=False,
        strengths=["coding", "reasoning", "general", "tool_use"],
    ),
    "anthropic": AIProviderConfig(
        provider_id="anthropic",
        display_name="Anthropic Claude",
        protocol="anthropic",
        base_url="https://api.anthropic.com/v1/messages",
        env_key_name="ANTHROPIC_API_KEY",
        default_model="claude-3-7-sonnet-latest",
        available_models=["claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
        cost_per_1k_input_usd=0.00300,
        cost_per_1k_output_usd=0.01500,
        is_local=False,
        strengths=["coding", "architecture", "security_audit"],
    ),
    "deepseek": AIProviderConfig(
        provider_id="deepseek",
        display_name="DeepSeek AI (V3 / R1)",
        protocol="openai_compat",
        base_url="https://api.deepseek.com/v1/chat/completions",
        env_key_name="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        available_models=["deepseek-chat", "deepseek-reasoner"],
        cost_per_1k_input_usd=0.00027,
        cost_per_1k_output_usd=0.00110,
        is_local=False,
        strengths=["math", "reasoning", "coding", "cost_efficient"],
    ),
    "openrouter": AIProviderConfig(
        provider_id="openrouter",
        display_name="OpenRouter (Grok / Llama / Qwen)",
        protocol="openai_compat",
        base_url="https://openrouter.ai/api/v1/chat/completions",
        env_key_name="OPENROUTER_API_KEY",
        default_model="x-ai/grok-2-1212",
        available_models=[
            "x-ai/grok-2-1212",
            "meta-llama/llama-3.3-70b-instruct",
            "qwen/qwen-2.5-coder-32b-instruct",
            "mistralai/mistral-large-2411",
        ],
        cost_per_1k_input_usd=0.00100,
        cost_per_1k_output_usd=0.00300,
        is_local=False,
        strengths=["multi_model", "aggregator", "fallback"],
    ),
    "local_ollama": AIProviderConfig(
        provider_id="local_ollama",
        display_name="Local Ollama / vLLM Engine",
        protocol="ollama",
        base_url="http://127.0.0.1:11434/api/chat",
        env_key_name="ULTRON_LOCAL_LLM_URL",
        default_model="qwen2.5-coder:14b",
        available_models=["qwen2.5-coder:14b", "llama3.3:70b", "deepseek-r1:14b", "mistral:7b"],
        cost_per_1k_input_usd=0.0,
        cost_per_1k_output_usd=0.0,
        is_local=True,
        strengths=["privacy", "air_gapped", "zero_cost"],
    ),
    "deterministic_fallback": AIProviderConfig(
        provider_id="deterministic_fallback",
        display_name="Ultron Sovereign Offline Synthesis",
        protocol="local_synth",
        base_url="local://sovereign-deterministic-core",
        env_key_name="",
        default_model="ultron-synth-v3",
        available_models=["ultron-synth-v3"],
        cost_per_1k_input_usd=0.0,
        cost_per_1k_output_usd=0.0,
        is_local=True,
        strengths=["guaranteed_uptime", "sub_millisecond", "air_gapped"],
    ),
}

