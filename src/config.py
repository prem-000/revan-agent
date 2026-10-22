from __future__ import annotations

import logging
from functools import lru_cache
import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings loaded from environment variables."""

    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3", alias="OLLAMA_MODEL")
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openai/gpt-3.5-turbo", alias="OPENROUTER_MODEL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    google_model: str = Field(default="gemini-2.0-flash", alias="GOOGLE_MODEL")
    perplexity_api_key: str | None = Field(default=None, alias="PERPLEXITY_API_KEY")
    perplexity_model: str = Field(default="sonar-pro", alias="PERPLEXITY_MODEL")
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_api_key_1: str | None = Field(default=None, alias="GROQ_API_KEY_1")
    groq_api_key_2: str | None = Field(default=None, alias="GROQ_API_KEY_2")
    groq_api_key_3: str | None = Field(default=None, alias="GROQ_API_KEY_3")
    groq_api_key_4: str | None = Field(default=None, alias="GROQ_API_KEY_4")
    groq_api_key_5: str | None = Field(default=None, alias="GROQ_API_KEY_5")
    max_llm_calls_per_run: int = Field(default=30, alias="MAX_LLM_CALLS_PER_RUN")
    max_run_time_seconds: int = Field(default=300, alias="MAX_RUN_TIME_SECONDS")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    together_api_key: str | None = Field(default=None, alias="TOGETHER_API_KEY")
    together_model: str = Field(default="mistralai/Mixtral-8x22B-Instruct-v0.1", alias="TOGETHER_MODEL")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    mistral_api_key: str | None = Field(default=None, alias="MISTRAL_API_KEY")
    mistral_model: str = Field(default="mistral-large-latest", alias="MISTRAL_MODEL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_search_results: int = Field(default=10, alias="MAX_SEARCH_RESULTS")
    # Increased context limit to allow for richer PDF extraction
    max_context_chars: int = Field(default=12000, alias="MAX_CONTEXT_CHARS")
    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    tavily_topic: str = Field(default="general", alias="TAVILY_TOPIC")
    
    # Provider routing & fallback settings
    llm_primary_provider: str = Field(default="groq", alias="LLM_PRIMARY_PROVIDER")
    llm_fallback_provider: str = Field(default="opencode", alias="LLM_FALLBACK_PROVIDER")
    llm_fallback_enabled: bool = Field(default=True, alias="LLM_FALLBACK_ENABLED")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")

    # OpenCode Zen (Big Pickle) settings
    opencode_api_key: str | None = Field(default=None, alias="OPENCODE_API_KEY")
    opencode_base_url: str = Field(default="https://opencode.ai/zen/v1", alias="OPENCODE_BASE_URL")
    opencode_model: str = Field(default="big-pickle", alias="OPENCODE_MODEL")

    # Deterministic mode settings
    default_seed: int = Field(default=42, alias="DEFAULT_SEED")
    default_temperature: float = Field(default=0.2, alias="DEFAULT_TEMPERATURE")
    default_top_p: float = Field(default=0.9, alias="DEFAULT_TOP_P")

    # Feature flags for upgrade modes
    relevance_filter: bool = Field(default=True, alias="RELEVANCE_FILTER")
    answer_first: bool = Field(default=True, alias="ANSWER_FIRST")
    self_check: bool = Field(default=True, alias="SELF_CHECK")
    graph_event: bool = Field(default=True, alias="GRAPH_EVENT")
    max_self_check_rounds: int = Field(default=2, alias="MAX_SELF_CHECK_ROUNDS")
    generate_full_report_upfront: bool = Field(default=True, alias="GENERATE_FULL_REPORT_UPFRONT")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def get_opencode_key(self) -> str | None:
        """Return the configured OpenCode API key, scanning .env safely if needed."""
        if self.opencode_api_key and self.opencode_api_key.strip():
            return self.opencode_api_key.strip()
        # Fallback check if user pasted key into .env with or without prefix
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("OPENCODE_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
                    elif line.startswith("oc_sk_"):
                        val = line.split("=", 1)[1].strip() if "=" in line else line
                        if val:
                            return val
        except Exception:
            pass
        return None

    def get_groq_keys(self) -> list[tuple[str, str]]:
        """Return configured Groq API keys in priority order GROQ_API_KEY_1..5 or GROQ_API_KEY."""
        discovered: list[tuple[str, str]] = []
        for i in range(1, 6):
            slot = f"GROQ_API_KEY_{i}"
            val = getattr(self, f"groq_api_key_{i}", None)
            if val and val.strip():
                discovered.append((slot, val.strip()))
        if not discovered and self.groq_api_key and self.groq_api_key.strip():
            discovered.append(("GROQ_API_KEY", self.groq_api_key.strip()))
        return discovered


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""

    return Settings()


class LogConfig:
    """Helper responsible for configuring structlog and stdlib logging."""

    def __init__(self, level: str = "INFO") -> None:
        self.level = level.upper()

    def configure(self) -> None:
        numeric_level = logging.getLevelName(self.level)
        if isinstance(numeric_level, str):
            numeric_level = logging.INFO

        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )


def configure_logging(settings: Settings | None = None) -> None:
    """Initialize logging using the provided settings."""

    config = LogConfig(level=(settings or get_settings()).log_level)
    config.configure()
