from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from common.proxy_env import normalize_socks_proxy_env

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    # Python 3.10 fallback
    import tomli as tomllib  # type: ignore

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# -----------------------------
# OpenManus-style AppConfig models
# -----------------------------


class LLMSettingsModel(BaseModel):
    model: str
    base_url: str
    api_key: str
    max_tokens: int = 4096
    max_input_tokens: Optional[int] = None
    temperature: float = 1.0
    api_type: str = ""
    api_version: str = ""


class ProxySettings(BaseModel):
    server: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class SearchSettings(BaseModel):
    engine: str = "tavily"
    fallback_engines: list[str] = Field(default_factory=list)
    retry_delay: int = 60
    max_retries: int = 3
    lang: str = "en"
    country: str = "us"


class BrowserSettings(BaseModel):
    headless: bool = False
    disable_security: bool = True
    extra_chromium_args: list[str] = Field(default_factory=list)
    chrome_instance_path: Optional[str] = None
    wss_url: Optional[str] = None
    cdp_url: Optional[str] = None
    proxy: Optional[ProxySettings] = None
    max_content_length: int = 2000


class SandboxSettings(BaseModel):
    use_sandbox: bool = False
    image: str = "python:3.12-slim"
    work_dir: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout: int = 300
    network_enabled: bool = False


class DaytonaSettings(BaseModel):
    daytona_api_key: str = ""
    daytona_server_url: str = "https://app.daytona.io/api"
    daytona_target: str = "us"
    sandbox_image_name: str = "whitezxj/sandbox:0.1.0"
    sandbox_entrypoint: str = (
        "/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf"
    )
    VNC_password: str = ""  # Must be set via environment variable


class MCPServerConfig(BaseModel):
    type: str
    url: Optional[str] = None
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)


class MCPSettings(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class RunflowSettings(BaseModel):
    use_data_analysis_agent: bool = False


class AppConfig(BaseModel):
    llm: dict[str, LLMSettingsModel]
    sandbox: Optional[SandboxSettings] = None
    browser_config: Optional[BrowserSettings] = None
    search_config: Optional[SearchSettings] = None
    mcp_config: Optional[MCPSettings] = None
    run_flow_config: Optional[RunflowSettings] = None
    daytona_config: Optional[DaytonaSettings] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Settings(BaseSettings):
    # Local `.env` files often include variables for the frontend (e.g. `SOULSEARCHER_BASE_URL`)
    # or optional providers that aren't always modeled here. Rejecting unknown keys makes
    # local setup brittle, so we ignore extras.
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
        extra="ignore",
    )
    """Application settings."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """
        In local dev, prefer `.env` over process environment variables.

        Many users run multiple projects in the same shell/conda env; a stale
        `OPENAI_API_KEY` in the process env can silently override `.env` and
        lead to confusing auth errors. In prod, keep the standard precedence
        (env vars override dotenv).
        """

        app_env = (os.getenv("APP_ENV") or "").strip().lower()
        debug_raw = (os.getenv("DEBUG") or "").strip().lower()
        debug = debug_raw in {"1", "true", "yes", "y", "on"}

        # If the process explicitly declares production, keep the standard precedence.
        if app_env in {"prod", "production"}:
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)

        # If the process declares dev/debug, prefer dotenv.
        if debug or app_env in {"dev", "debug", "local", "test"}:
            return (init_settings, dotenv_settings, env_settings, file_secret_settings)

        # Heuristic: if a `.env` file exists, assume a local/dev-style run.
        # This avoids confusing cases where a stale process env var overrides `.env`.
        if any(
            candidate.is_file()
            for candidate in (
                Path(".env"),
                Path(__file__).resolve().parent.parent / ".env",
            )
        ):
            return (init_settings, dotenv_settings, env_settings, file_secret_settings)

        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    # API Keys
    openai_api_key: str = ""
    openai_base_url: str = ""
    use_azure: bool = False
    azure_api_key: str = ""
    azure_endpoint: str = ""
    azure_api_version: str = "2025-03-01-preview"
    openai_timeout: int = 60
    openai_extra_body: str = ""  # JSON string for extra OpenAI-compatible params
    llm_reliability_enabled: bool = Field(
        default=True,
        validation_alias="LLM_RELIABILITY_ENABLED",
        description="Enable retry and circuit-breaker wrapper for LLM calls.",
    )
    llm_retry_max_attempts: int = Field(
        default=3,
        ge=1,
        validation_alias="LLM_RETRY_MAX_ATTEMPTS",
        description="Total LLM call attempts including the first attempt.",
    )
    llm_retry_initial_delay_seconds: float = Field(
        default=0.5,
        ge=0.0,
        validation_alias="LLM_RETRY_INITIAL_DELAY_SECONDS",
    )
    llm_retry_max_delay_seconds: float = Field(
        default=8.0,
        ge=0.0,
        validation_alias="LLM_RETRY_MAX_DELAY_SECONDS",
    )
    llm_retry_jitter_seconds: float = Field(
        default=0.25,
        ge=0.0,
        validation_alias="LLM_RETRY_JITTER_SECONDS",
    )
    llm_circuit_breaker_failures: int = Field(
        default=5,
        ge=1,
        validation_alias="LLM_CIRCUIT_BREAKER_FAILURES",
    )
    llm_circuit_breaker_reset_seconds: float = Field(
        default=60.0,
        ge=1.0,
        validation_alias="LLM_CIRCUIT_BREAKER_RESET_SECONDS",
    )
    tavily_api_key: str = ""
    tavily_api_keys: str = (
        ""  # comma-separated Tavily keys for auto-rotation on quota exhaustion
    )
    bocha_api_key: str = ""
    # Web search providers (optional; used when SEARCH_ENGINES includes them)
    serper_api_key: str = ""
    serpapi_api_key: str = ""
    bing_api_key: str = ""
    brave_api_key: str = ""
    exa_api_key: str = ""
    firecrawl_api_key: str = ""
    google_search_api_key: str = ""  # Google Custom Search API key
    google_search_engine_id: str = ""  # Google Custom Search Engine ID (cx)
    e2b_api_key: str = ""
    anthropic_api_key: str = ""
    memory_user_id: str = "default_user"
    enable_mcp: bool = False
    mcp_servers: str = ""  # JSON mapping for MultiServerMCPClient
    mcp_strategy: str = (
        "on_demand"  # disabled | fast_once | per_research_unit | on_demand
    )
    mcp_tool_whitelist: str = ""  # comma-separated allowed MCP tool names
    mcp_max_tools: int = 0  # 0 = no explicit cap
    mcp_deferred_tools_enabled: bool = Field(
        default=False, validation_alias="MCP_DEFERRED_TOOLS_ENABLED"
    )
    human_review: bool = False  # require manual approval before final report
    tool_approval: bool = False  # require approval before executing tools
    max_revisions: int = 2

    # DeerFlow-style Agent runtime
    agent_runtime_enabled: bool = True
    agent_runtime_default_subagent_enabled: bool = True
    agent_runtime_max_concurrent_subagents: int = 3
    host_bash_enabled: bool = False

    # Quality gates (evaluation)
    citation_gate_min_coverage: float = Field(default=0.6, ge=0.0, le=1.0)
    claim_verifier_gate_max_contradicted: int = Field(default=0, ge=0)
    claim_verifier_gate_max_unsupported: int = Field(default=0, ge=0)
    evaluation_pass_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    evaluation_revise_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_claim_alignment_min_rate: float = Field(default=0.75, ge=0.0, le=1.0)
    evaluation_require_l2_pass: bool = True
    evaluation_require_citation_evidence: bool = True
    evaluation_html_quality_check: bool = True
    evaluation_calibration_min_agreement: float = Field(default=0.7, ge=0.0, le=1.0)
    evaluation_calibration_min_samples: int = Field(default=5, ge=1)
    deep_research_strict_citations: bool = Field(
        default=True, validation_alias="DEEP_RESEARCH_STRICT_CITATIONS"
    )
    tool_policy_strict: bool = Field(
        default=True, validation_alias="TOOL_POLICY_STRICT"
    )
    background_runs_enabled: bool = Field(
        default=False, validation_alias="BACKGROUND_RUNS_ENABLED"
    )
    background_execution_backend: str = Field(
        default="local",
        validation_alias="SOULSEARCHER_BACKGROUND_EXECUTION_BACKEND",
        description="Background research backend: local or temporal.",
    )
    temporal_address: str = Field(
        default="localhost:7233",
        validation_alias="TEMPORAL_ADDRESS",
    )
    temporal_namespace: str = Field(
        default="default",
        validation_alias="TEMPORAL_NAMESPACE",
    )
    temporal_task_queue: str = Field(
        default="soulsearcher-deep-research",
        validation_alias="TEMPORAL_TASK_QUEUE",
    )
    temporal_workflow_timeout_seconds: int = Field(
        default=21600,
        ge=60,
        validation_alias="TEMPORAL_WORKFLOW_TIMEOUT_SECONDS",
    )
    temporal_workflow_version: str = Field(
        default="v2",
        validation_alias="SOULSEARCHER_TEMPORAL_WORKFLOW_VERSION",
        description="Temporal workflow implementation version for background research.",
    )
    legacy_citation_mode: bool = Field(
        default=False, validation_alias="LEGACY_CITATION_MODE"
    )

    # Evidence/source cache
    source_cache_max_chars: int = Field(
        default=120000, validation_alias="SOURCE_CACHE_MAX_CHARS", ge=0
    )
    deep_read_max_chars: int = Field(
        default=30000, validation_alias="DEEP_READ_MAX_CHARS", ge=1000
    )
    slash_skill_activation_enabled: bool = Field(
        default=True, validation_alias="SLASH_SKILL_ACTIVATION_ENABLED"
    )

    # Environment
    app_env: str = "dev"  # dev | test | prod
    enable_prometheus: bool = False  # expose /metrics in Prometheus format
    port: int = Field(
        default=8001,
        ge=1,
        le=65535,
        validation_alias="PORT",
        description="Backend listen port (used by `python main.py`).",
    )
    soulsearcher_reload: bool = Field(
        default=False,
        validation_alias="SOULSEARCHER_RELOAD",
        description="Enable uvicorn hot reload when running `python main.py` (only in DEBUG).",
    )
    internal_api_key: str = Field(
        default="", validation_alias="SOULSEARCHER_INTERNAL_API_KEY"
    )
    public_base_url: str = Field(
        default="",
        validation_alias="SOULSEARCHER_PUBLIC_BASE_URL",
        description="Public base URL used in A2A Agent Cards and external callbacks.",
    )
    auth_user_header: str = Field(
        default="X-SoulSearcher-User",
        validation_alias="SOULSEARCHER_AUTH_USER_HEADER",
        description="Trusted user identity header (only meaningful behind an authenticated proxy).",
    )
    a2a_stalled_timeout_seconds: int = Field(
        default=900,
        ge=0,
        validation_alias="SOULSEARCHER_A2A_STALLED_TIMEOUT_SECONDS",
        description="Mark A2A working tasks stalled after this many seconds without persisted progress; 0 disables.",
    )
    a2a_idempotency_ttl_seconds: int = Field(
        default=86400,
        ge=60,
        validation_alias="SOULSEARCHER_A2A_IDEMPOTENCY_TTL_SECONDS",
        description="Retention hint for A2A idempotency metadata stored with run records.",
    )
    a2a_callback_retry_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        validation_alias="SOULSEARCHER_A2A_CALLBACK_RETRY_ATTEMPTS",
        description="Best-effort retry attempts for SoulClaw A2A callback delivery.",
    )
    a2a_callback_token: str = Field(
        default="",
        validation_alias="SOULSEARCHER_A2A_CALLBACK_TOKEN",
        description="Fallback callback token when task metadata does not provide one.",
    )
    a2a_callback_outbox_enabled: bool = Field(
        default=True,
        validation_alias="SOULSEARCHER_A2A_CALLBACK_OUTBOX_ENABLED",
        description="Persist A2A callbacks to an outbox before delivery when a database is configured.",
    )

    # HTTP Rate Limiting (optional; primarily useful for public deployments)
    # RATE_LIMIT_ENABLED:
    # - unset (default): enabled only when APP_ENV=prod|production
    # - true/false: force on/off regardless of APP_ENV
    rate_limit_enabled: Optional[bool] = Field(
        default=None, validation_alias="RATE_LIMIT_ENABLED"
    )
    rate_limit_general_per_minute: int = Field(
        default=60, ge=1, validation_alias="RATE_LIMIT_GENERAL_PER_MINUTE"
    )
    rate_limit_research_per_minute: int = Field(
        default=20,
        ge=1,
        validation_alias="RATE_LIMIT_RESEARCH_PER_MINUTE",
    )
    rate_limit_window_seconds: int = Field(
        default=60, ge=1, validation_alias="RATE_LIMIT_WINDOW_SECONDS"
    )
    rate_limit_max_buckets: int = Field(
        default=10_000,
        ge=1,
        validation_alias="RATE_LIMIT_MAX_BUCKETS",
        description="Cap in-memory token buckets to avoid unbounded growth under many unique clients.",
    )
    rate_limit_backend: str = Field(
        default="memory",
        validation_alias="RATE_LIMIT_BACKEND",
        description="HTTP rate limiter backend: memory, redis, or auto.",
    )
    rate_limit_redis_fail_open: bool = Field(
        default=True,
        validation_alias="RATE_LIMIT_REDIS_FAIL_OPEN",
        description="When Redis rate limiting is unavailable, fall back to local memory buckets.",
    )
    redis_url: str = Field(
        default="",
        validation_alias="REDIS_URL",
        description="Shared Redis URL for production runtime coordination.",
    )

    # Idempotency controls
    idempotency_enabled: bool = Field(
        default=True,
        validation_alias="IDEMPOTENCY_ENABLED",
        description="Enable idempotency-key handling on mutation endpoints that support it.",
    )
    idempotency_ttl_seconds: int = Field(
        default=86400,
        ge=60,
        validation_alias="IDEMPOTENCY_TTL_SECONDS",
        description="How long to keep completed idempotency records.",
    )
    background_run_lease_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        validation_alias="BACKGROUND_RUN_LEASE_TTL_SECONDS",
        description="Lease TTL for background run execution locks.",
    )

    # Database
    database_url: str = ""

    # App Config
    debug: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:3100"
    interrupt_before_nodes: str = (
        ""  # comma-separated node names for LangGraph interrupts
    )
    app_config_path: str = (
        "config/config.toml"  # Optional TOML config (OpenManus style)
    )
    yaml_config_path: str = "config/config.yaml"  # Optional YAML config
    mcp_config_path: str = "config/mcp.json"  # MCP servers definition (JSON)
    app_config_object: Optional[AppConfig] = (
        None  # populated at runtime if TOML is present
    )

    # Logging Config
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    log_file: str = "logs/soulsearcher.log"  # Log file path
    log_max_bytes: int = 10485760  # 10MB
    log_backup_count: int = 5  # Keep 5 backup files
    log_format: str = (
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    )
    enable_file_logging: bool = True  # Enable logging to file
    enable_json_logging: bool = False  # Enable structured JSON logging

    # Tracing Config
    enable_tracing: bool = False  # Enable LLM call tracing
    trace_buffer_size: int = 1000  # Max traces to keep in memory
    otlp_endpoint: str = ""  # Optional OTLP exporter endpoint

    # Model Config
    primary_model: str = "qwen3.6-plus"
    reasoning_model: str = "qwen3.7-max"  # For planning

    # Three-Tier Model Routing (fast / smart / strategic)
    # Empty means the runtime falls back to primary_model / reasoning_model.
    fast_llm_model: str = ""   # Cheap, fast model for high-volume tasks
    smart_llm_model: str = ""  # Balanced model for synthesis & writing
    strategic_llm_model: str = ""  # Most capable model for planning & deep reasoning

    # DashScope (Alibaba Cloud) API
    dashscope_api_key: str = ""  # Required when using DashScope models via compatible API

    # Multi-Model Research Config (Task-specific overrides)
    # Each overrides the three-tier default for a specific phase.
    # Empty = use the tier-appropriate model from above.
    fast_llm_max_tokens: int = 4096
    smart_llm_max_tokens: int = 8192
    strategic_llm_max_tokens: int = 8192
    planner_model: str = ""      # Model for research planning (defaults to strategic_llm)
    researcher_model: str = ""   # Model for research analysis (defaults to smart_llm)
    writer_model: str = ""       # Model for report writing (defaults to smart_llm)
    evaluator_model: str = ""    # Model for quality evaluation (defaults to fast_llm)
    critic_model: str = ""       # Model for URL selection/critique (defaults to reasoning_model)
    compression_model: str = ""  # Model for research compression (defaults to smart_llm)
    summarization_model: str = ""# Model for summarization (defaults to fast_llm)
    final_report_model: str = "" # Model for final report (defaults to smart_llm)
    research_model: str = ""     # Model for research phase (defaults to smart_llm)
    vision_model: str = ""       # Vision-capable model override

    # Report Visualization Config
    enable_report_charts: bool = True  # Generate charts from data in reports

    # Human-in-the-Loop (HITL) Config
    hitl_checkpoints: str = (
        ""  # Comma-separated interrupt points: plan,sources,draft,final
    )
    hitl_timeout_seconds: int = 3600  # Max wait time for human review (1 hour)

    # Prompt Config (选择提示词风格)
    prompt_style: str = "enhanced"  # simple | enhanced | custom
    custom_agent_prompt_path: str = ""  # 自定义 agent 提示词文件路径
    custom_writer_prompt_path: str = ""  # 自定义 writer 提示词文件路径
    prompt_pack: str = "deepsearch"  # default prompt pack
    prompt_variant: str = "full"  # full | lite

    # Message trimming (short-term memory)
    trim_messages: bool = False
    trim_messages_keep_first: int = 2
    trim_messages_keep_last: int = 8
    summary_messages: bool = False
    summary_messages_trigger: int = (
        12  # when messages count exceeds this, summarize middle
    )
    summary_messages_keep_last: int = 4
    summary_messages_model: str = ""  # Empty = use fast_llm_model
    summary_messages_word_limit: int = 200

    # Concurrency Control (并发控制)
    max_concurrency: int = 5  # 最大并发数
    search_batch_size: int = 3  # 搜索批次大小
    api_rate_limit: float = 0.5  # API 调用间隔（秒）

    # Deepsearch Settings (for supervisor_workers pipeline)
    deepsearch_mode: str = "supervisor_workers"
    deepsearch_max_epochs: int = 3
    deepsearch_max_seconds: float = 0.0  # 0 = disabled
    deepsearch_max_tokens: int = 0  # 0 = disabled
    deepsearch_supervisor_rounds: int = 2
    deepsearch_supervisor_max_workers: int = 4
    deepsearch_supervisor_queries_per_worker: int = 2
    deepsearch_supervisor_parallel_workers: int = 2
    deepsearch_supervisor_think_enabled: bool = True
    deepsearch_supervisor_max_depth: int = 1
    deepsearch_supervisor_depth_confidence_threshold: float = 0.5
    deepsearch_max_seconds_per_worker: float = 0.0
    deepsearch_max_skills: int = 3
    deepsearch_reflection_loops: int = 0  # 0 = derive from supervisor rounds
    deepsearch_claim_verifier_use_passages: bool = True
    deepsearch_claim_verifier_min_overlap_tokens: int = 2
    deepsearch_claim_verifier_max_evidence_per_claim: int = 3
    deepsearch_claim_verifier_max_claims: int = 10
    deepsearch_max_research_units: int = 0  # 0 = derive from rounds * workers
    deepsearch_max_tool_calls_per_unit: int = 0  # 0 = derive from queries per worker
    deepsearch_summary_trigger_tokens: int = 8000
    deepsearch_summary_trigger_messages: int = 10
    deepsearch_summary_keep_recent: int = 3
    deepsearch_guardrail_denied_tools: str = ""
    deepsearch_guardrail_allowed_domains: str = ""
    deepsearch_guardrail_denied_domains: str = ""
    retrieval_policy_strict: bool = Field(
        default=True, validation_alias="RETRIEVAL_POLICY_STRICT"
    )
    document_library_path: str = Field(
        default="data/document_library", validation_alias="DOCUMENT_LIBRARY_PATH"
    )
    document_library_database_url: str = Field(
        default="", validation_alias="DOCUMENT_LIBRARY_DATABASE_URL"
    )
    document_library_embedding_model: str = Field(
        default="", validation_alias="DOCUMENT_LIBRARY_EMBEDDING_MODEL"
    )
    document_library_embedding_dim: int = Field(
        default=0, ge=0, validation_alias="DOCUMENT_LIBRARY_EMBEDDING_DIM"
    )
    document_library_chunk_chars: int = Field(
        default=1500, ge=300, validation_alias="DOCUMENT_LIBRARY_CHUNK_CHARS"
    )
    document_library_chunk_overlap: int = Field(
        default=200, ge=0, validation_alias="DOCUMENT_LIBRARY_CHUNK_OVERLAP"
    )
    # Research Fetcher / Reader Settings
    reader_fallback_mode: str = "both"
    reader_public_base: str = "https://r.jina.ai"
    reader_self_hosted_base: str = ""
    research_fetch_timeout_s: float = 25.0
    research_fetch_max_bytes: int = 2_000_000
    research_fetch_concurrency: int = 6
    research_fetch_concurrency_per_domain: int = 2
    research_fetch_cache_ttl_s: float = 0.0  # 0 disables in-memory fetch cache
    research_fetch_cache_max_entries: int = 256
    research_fetch_cache_store_errors: bool = False
    research_fetch_render_mode: str = "off"  # off | auto | always
    research_fetch_render_min_chars: int = 200
    research_fetch_extract_markdown: bool = True

    # Multi-Search Engine Config
    search_strategy: str = "fallback"  # fallback | parallel | round_robin | best_first
    search_enable_freshness_ranking: bool = (
        True  # Apply freshness boost for time-sensitive queries
    )
    search_freshness_half_life_days: float = 30.0  # Decay half-life for recency score
    search_freshness_weight: float = (
        0.35  # Blend weight between relevance and freshness
    )
    search_cache_max_size: int = 200  # session-level search cache capacity
    search_cache_ttl_seconds: float = 1800.0  # search cache TTL in seconds
    search_cache_similarity_threshold: float = 0.9  # fuzzy query match threshold
    search_reliability_max_retries: int = (
        2  # retries per provider call (in addition to first attempt)
    )
    search_reliability_retry_backoff_seconds: float = (
        0.5  # exponential backoff base seconds
    )
    search_reliability_circuit_breaker_failures: int = (
        3  # open circuit after N consecutive failures
    )
    search_reliability_circuit_breaker_reset_seconds: float = (
        60.0  # reset circuit after N seconds
    )
    search_parallel_max_workers: int = 8  # cap threads for parallel provider fan-out
    search_parallel_timeout_seconds: float = (
        30.0  # best-effort timeout for parallel fan-out
    )
    brave_api_key: str = ""  # Brave Search API key
    serper_api_key: str = ""  # Serper.dev API key
    exa_api_key: str = ""  # Exa.ai API key

    # Real-time Feed Settings
    twitter_bearer_token: str = ""  # Twitter/X API v2 Bearer Token
    twitter_api_key: str = ""  # Twitter API Key (optional)
    twitter_api_secret: str = ""  # Twitter API Secret (optional)
    reddit_client_id: str = ""  # Reddit OAuth client ID
    reddit_client_secret: str = ""  # Reddit OAuth client secret
    reddit_user_agent: str = "SoulSearcher/1.0"  # Reddit API user agent
    hackernews_enabled: bool = True  # HackerNews search (no API key needed)

    # Academic Search Settings
    arxiv_enabled: bool = True  # arXiv search (no API key needed)
    scholar_enabled: bool = True  # Google Scholar (no API key, rate limited)
    semantic_scholar_api_key: str = (
        ""  # Semantic Scholar API key (optional, higher rate)
    )
    pubmed_email: str = ""  # NCBI Entrez email (required for PubMed)
    pubmed_api_key: str = ""  # NCBI API key (optional, higher rate)

    # Crawler
    crawler_headless: bool = True  # True=无头(默认不弹窗)，False=可视化调试
    use_optimized_crawler: bool = (
        False  # 是否启用Playwright优化爬虫，Windows建议默认False
    )

    # Daytona sandbox
    daytona_api_key: str = ""
    daytona_server_url: str = "https://app.daytona.io/api"
    daytona_target: str = "us"
    daytona_image_name: str = "whitezxj/sandbox:0.1.0"
    daytona_entrypoint: str = (
        "/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf"
    )
    daytona_vnc_password: str = ""  # Must be set via environment variable

    # Sandbox mode: e2b (remote E2B), daytona (remote Daytona), none (disabled).
    # The value "local" is accepted as an alias for E2B remote execution.
    sandbox_mode: str = "e2b"
    sandbox_template_browser: str = (
        ""  # e2b sandbox browser template ID (e.g., chrome-stable)
    )
    sandbox_allow_internet: bool = True  # allow internet access inside sandbox

    # IM channel integration
    channels_enabled: bool = False
    channels_base_url: str = ""
    channel_default_agent_name: str = ""
    channel_default_model: str = ""
    channel_default_subagent_enabled: bool = True
    channel_default_deepsearch_mode: str = "supervisor_workers"
    feishu_channel_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_domain: str = "https://open.feishu.cn"

    # ── DeerFlow-aligned: Skills ──
    skills_path: str = ""  # Override skills root path (defaults to skills/ under project root)
    skills_public_dir: str = "skills/public"
    skills_custom_dir: str = "skills/custom"
    skills_state_path: str = "data/skills_state.json"
    skills_container_path: str = "/mnt/skills"
    skill_evolution_enabled: bool = False  # Allow agents to create/modify skills
    skill_evolution_moderation_model_name: str = ""  # Model for security scan (empty = default)

    # ── Unified Memory ──
    memory_enabled: bool = Field(default=True, validation_alias="MEMORY_ENABLED")
    memory_backend: str = Field(default="postgres", validation_alias="MEMORY_BACKEND")
    memory_database_url: str = Field(default="", validation_alias="MEMORY_DATABASE_URL")
    memory_embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="MEMORY_EMBEDDING_MODEL",
    )
    memory_embedding_dim: int = Field(
        default=1536,
        ge=1,
        validation_alias="MEMORY_EMBEDDING_DIM",
    )
    memory_retrieval_max_tokens: int = Field(
        default=2500,
        ge=256,
        validation_alias="MEMORY_RETRIEVAL_MAX_TOKENS",
    )
    memory_retrieval_top_k: int = Field(
        default=12,
        ge=1,
        validation_alias="MEMORY_RETRIEVAL_TOP_K",
    )
    memory_write_min_confidence: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        validation_alias="MEMORY_WRITE_MIN_CONFIDENCE",
    )
    memory_write_require_evidence: bool = Field(
        default=True,
        validation_alias="MEMORY_WRITE_REQUIRE_EVIDENCE",
    )
    memory_sensitive_write_policy: str = Field(
        default="reject",
        validation_alias="MEMORY_SENSITIVE_WRITE_POLICY",
    )
    memory_auto_skill_evolution: bool = Field(
        default=True,
        validation_alias="MEMORY_AUTO_SKILL_EVOLUTION",
    )
    memory_auto_skill_min_support: int = Field(
        default=3,
        ge=1,
        validation_alias="MEMORY_AUTO_SKILL_MIN_SUPPORT",
    )
    # ── DeerFlow-aligned: Summarization ──
    summarization_enabled: bool = True
    summarization_max_input_tokens: int = 64000
    summarization_trigger_fraction: float = 0.75
    summarization_keep_last: int = 15

    # ── DeerFlow-aligned: Loop Detection ──
    loop_detection_enabled: bool = True
    loop_detection_max_repeats: int = 3
    loop_detection_window: int = 8

    # ── DeerFlow-aligned: Guardrails ──
    guardrails_enabled: bool = False
    guardrails_denylist: str = ""  # comma-separated denied tool names

    # ── DeerFlow-aligned: Tool Search (deferred MCP tools) ──
    tool_search_enabled: bool = False

    # ── DeerFlow-aligned: Title Generation ──
    title_generation_enabled: bool = True
    title_max_words: int = 8

    # ── DeerFlow-aligned: Todo/Plan Mode ──
    plan_mode_enabled: bool = True

    # ── Sandbox security ──
    sandbox_allow_host_bash: bool = False

    # Tool / middleware controls
    tool_retry: bool = True
    tool_retry_max_attempts: int = 3
    tool_retry_backoff: float = 1.5  # seconds exponential factor
    tool_retry_initial_delay: float = 1.0
    tool_retry_max_delay: float = 60.0
    tool_call_limit: int = 12
    strip_tool_messages: bool = False  # drop ToolMessage from history to save tokens
    observation_masking: bool = True  # mask old tool observations instead of dropping
    observation_masking_window: int = 5  # keep last N turns' observations in full
    context_edit_trigger_tokens: int = 1000
    context_edit_keep_tools: int = 3
    tool_selector: bool = (
        True  # provider-safe selector retries across structured-output methods
    )
    tool_selector_model: str = ""  # defaults to primary_model when unset
    tool_selector_max_tools: int = 3
    tool_selector_always_include: str = ""  # comma-separated tool names
    tool_selector_prompt: str = ""
    enable_todo_middleware: bool = (
        True  # provider-safe: preserves LangChain defaults when unset
    )
    todo_system_prompt: str = ""  # custom system prompt for todo middleware
    todo_tool_description: str = ""  # custom tool description for todo middleware
    enable_browser_use: bool = False  # enable browser_use tool (Playwright-based)
    enable_browser_context_helper: bool = (
        False  # inject browser context prompt if available
    )

    # Tool visibility / events
    emit_tool_events: bool = True  # wrap tools with event emitters for front-end
    tool_whitelist: str = ""  # comma-separated tool names to allow (empty = all)
    tool_blacklist: str = ""  # comma-separated tool names to block

    # Search fallback
    search_engines: str = "tavily"  # comma-separated engines in order

    # Context Offloading
    context_offloading: bool = False  # offload large tool results to filesystem
    context_offloading_threshold: int = 2000  # chars above which content is offloaded
    context_offloading_dir: str = ""  # empty = /tmp/soulsearcher_offload

    # Agent Reflexion
    agent_reflexion_enabled: bool = True  # self-reflection after tool-calling rounds
    agent_reflexion_max_rounds: int = 2  # max reflection iterations

    # Dynamic tool pruning
    dynamic_tool_pruning: bool = False  # prune tools by route to reduce token overhead

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins string into list."""
        origins = [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

        # Dev ergonomics: allow common local frontend ports by default.
        # This keeps the UI working even when CORS_ORIGINS in `.env` is outdated.
        env = (self.app_env or "").strip().lower()
        if (
            self.debug
            or env in {"dev", "debug", "local", "test"}
            or env not in {"prod", "production"}
        ):
            for extra in (
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:3100",
                "http://127.0.0.1:3100",
            ):
                if extra not in origins:
                    origins.append(extra)

        return origins

    @property
    def rate_limit_enabled_effective(self) -> bool:
        """
        Whether HTTP rate limiting is enabled for this process.

        Open-source/dev ergonomics:
        - If RATE_LIMIT_ENABLED is set, we respect it.
        - Otherwise we only enable rate limiting in production.
        """
        configured = getattr(self, "rate_limit_enabled", None)
        if configured is not None:
            return bool(configured)
        env = (self.app_env or "").strip().lower()
        return env in {"prod", "production"}

    @property
    def interrupt_nodes_list(self) -> list[str]:
        """Parse interrupt_before_nodes into list for LangGraph compile."""
        return [
            node.strip()
            for node in self.interrupt_before_nodes.split(",")
            if node.strip()
        ]

    @property
    def tool_selector_always_include_list(self) -> list[str]:
        """Comma separated tool names that must always be kept when selector is on."""
        return [
            t.strip() for t in self.tool_selector_always_include.split(",") if t.strip()
        ]

    @property
    def tool_whitelist_list(self) -> list[str]:
        """Comma separated tool whitelist."""
        return [t.strip() for t in self.tool_whitelist.split(",") if t.strip()]

    @property
    def tool_blacklist_list(self) -> list[str]:
        """Comma separated tool blacklist."""
        return [t.strip() for t in self.tool_blacklist.split(",") if t.strip()]

    @property
    def search_engines_list(self) -> list[str]:
        """Comma separated ordered search engines."""
        engines = [e.strip() for e in self.search_engines.split(",") if e.strip()]
        if not engines and getattr(self, "app_config_object", None):
            cfg = getattr(self.app_config_object, "search_config", None)
            if cfg:
                engines = [cfg.engine] + list(cfg.fallback_engines or [])
        return engines or ["tavily"]

    @property
    def use_fallback_search_tool(self) -> bool:
        """Use the multi-engine fallback search tool whenever non-Tavily routing is configured."""
        engines = [e.strip().lower() for e in self.search_engines_list if e.strip()]
        return len(engines) != 1 or (engines[0] if engines else "tavily") != "tavily"

    def llm_config_for_model(self, model_name: str) -> Optional[LLMSettingsModel]:
        """Return app-configured credentials/base URL for a specific model when available."""
        app_cfg = getattr(self, "app_config_object", None)
        llm_map = getattr(app_cfg, "llm", None)
        if not llm_map:
            return None

        requested = str(model_name or "").strip()
        if not requested:
            return llm_map.get("default")

        direct = llm_map.get(requested)
        if direct:
            return direct

        for cfg in llm_map.values():
            if (cfg.model or "").strip() == requested:
                return cfg

        return None

    @field_validator("deepsearch_mode", mode="before")
    @classmethod
    def normalize_deepsearch_mode(cls, value: str) -> str:
        mode = str(value or "").strip().lower().replace("-", "_")
        if mode in {
            "reflection",
            "reflection_loop",
            "auto",
        }:
            mode = "supervisor_workers"
        if mode in {"supervisor", "workers", "supervisor_worker"}:
            mode = "supervisor_workers"
        if mode in {"linear", "linear_light", "light"}:
            mode = "supervisor_workers"
        if mode in {"supervisor_workers"}:
            return mode
        return "supervisor_workers"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_mcp_servers(mcp_path: str) -> dict[str, MCPServerConfig]:
    """
    Load MCP server configs from JSON (mcp.json or mcp.json.example).
    """
    candidates = [
        _project_root() / mcp_path,
        _project_root() / "config" / "mcp.json.example",
        _project_root() / "docs2" / "config_mcp.json.example",
    ]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                servers = {}
                for sid, scfg in data.get(
                    "mcpServers", data.get("servers", {})
                ).items():
                    servers[sid] = MCPServerConfig(
                        type=scfg.get("type", ""),
                        url=scfg.get("url"),
                        command=scfg.get("command"),
                        args=scfg.get("args", []),
                    )
                return servers
            except Exception as e:
                logger.warning(f"Failed to load MCP config from {path}: {e}")
    return {}


@lru_cache
def load_app_config(config_path: str, mcp_path: str) -> Optional[AppConfig]:
    """
    Load AppConfig from TOML + MCP JSON (compatible with OpenManus config layout).
    """
    root = _project_root()
    primary_path = Path(config_path)
    primary = primary_path if primary_path.is_absolute() else root / config_path
    example = (
        primary.with_suffix(".example.toml")
        if primary.suffix != ".toml"
        else root / "config" / "config.example.toml"
    )
    candidates = [primary, example]
    cfg_file = next((p for p in candidates if p.exists()), None)
    if not cfg_file:
        return None

    data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
    base_llm = data.get("llm", {}) or {}
    overrides = {k: v for k, v in base_llm.items() if isinstance(v, dict)}

    default_settings = {
        "model": base_llm.get("model", ""),
        "base_url": base_llm.get("base_url", ""),
        "api_key": base_llm.get("api_key", ""),
        "max_tokens": base_llm.get("max_tokens", 4096),
        "max_input_tokens": base_llm.get("max_input_tokens"),
        "temperature": base_llm.get("temperature", 1.0),
        "api_type": base_llm.get("api_type", ""),
        "api_version": base_llm.get("api_version", ""),
    }

    llm_dict: dict[str, LLMSettingsModel] = {}
    if default_settings.get("model"):
        llm_dict["default"] = LLMSettingsModel(**default_settings)
    for name, override in overrides.items():
        merged = {**default_settings, **override}
        if merged.get("model"):
            llm_dict[name] = LLMSettingsModel(**merged)

    browser_cfg = data.get("browser", {}) or {}
    proxy_cfg = browser_cfg.get("proxy") or {}
    proxy = None
    if proxy_cfg.get("server"):
        proxy = ProxySettings(
            server=proxy_cfg.get("server"),
            username=proxy_cfg.get("username"),
            password=proxy_cfg.get("password"),
        )
    browser_settings = None
    if browser_cfg:
        bs_kwargs = {
            k: v
            for k, v in browser_cfg.items()
            if k in BrowserSettings.__annotations__ and v is not None
        }
        if proxy:
            bs_kwargs["proxy"] = proxy
        if bs_kwargs:
            browser_settings = BrowserSettings(**bs_kwargs)

    search_cfg = data.get("search") or {}
    search_settings = SearchSettings(**search_cfg) if search_cfg else None

    sandbox_cfg = data.get("sandbox") or {}
    sandbox_settings = SandboxSettings(**sandbox_cfg) if sandbox_cfg else None

    daytona_cfg = data.get("daytona") or {}
    daytona_settings = DaytonaSettings(**daytona_cfg) if daytona_cfg else None

    mcp_servers = _load_mcp_servers(mcp_path)
    mcp_cfg = data.get("mcp") or {}
    mcp_settings = (
        MCPSettings(servers=mcp_servers or mcp_cfg.get("servers", {}))
        if (mcp_servers or mcp_cfg)
        else None
    )

    runflow_cfg = data.get("runflow") or {}
    runflow_settings = RunflowSettings(**runflow_cfg) if runflow_cfg else None

    if not llm_dict:
        return None

    return AppConfig(
        llm=llm_dict,
        sandbox=sandbox_settings,
        browser_config=browser_settings,
        search_config=search_settings,
        mcp_config=mcp_settings,
        run_flow_config=runflow_settings,
        daytona_config=daytona_settings,
    )


def load_yaml_config(yaml_path: str) -> dict[str, Any]:
    """
    Load a YAML config file and return a flat dict of settings.
    Supports layered structure: llm, search, browser, sandbox, deepsearch, agent, eval.
    Returns empty dict if file not found or parsing fails.
    """
    root = _project_root()
    primary = Path(yaml_path) if Path(yaml_path).is_absolute() else root / yaml_path
    if not primary.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(primary.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to load YAML config from {primary}: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    flat: dict[str, Any] = {}
    for section_key, section_val in data.items():
        if isinstance(section_val, dict):
            for key, val in section_val.items():
                flat[key] = val
        else:
            flat[section_key] = section_val
    return flat


def _apply_yaml_overrides(settings_obj: Settings) -> None:
    """
    Apply YAML config as low-priority overrides (below env vars and TOML).
    Only sets values that are still at their defaults.
    """
    yaml_data = load_yaml_config(settings_obj.yaml_config_path)
    if not yaml_data:
        return
    for key, value in yaml_data.items():
        if not hasattr(settings_obj, key):
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        field_info = Settings.model_fields.get(key)
        if field_info is None:
            continue
        current = getattr(settings_obj, key, None)
        default = field_info.get_default(call_default_factory=True)
        if current == default:
            try:
                setattr(settings_obj, key, value)
            except Exception:
                pass


def _normalize_model_defaults(settings_obj: Settings) -> None:
    """Keep model tiers aligned when only PRIMARY_MODEL/REASONING_MODEL are set."""
    primary = (settings_obj.primary_model or "").strip()
    reasoning = (settings_obj.reasoning_model or "").strip() or primary

    if not (settings_obj.fast_llm_model or "").strip() and primary:
        settings_obj.fast_llm_model = primary
    if not (settings_obj.smart_llm_model or "").strip() and primary:
        settings_obj.smart_llm_model = primary
    if not (settings_obj.strategic_llm_model or "").strip() and reasoning:
        settings_obj.strategic_llm_model = reasoning


def apply_app_config_overrides(settings: Settings) -> None:
    """
    Merge TOML/JSON app config into BaseSettings values without overriding explicit env values.
    Then apply YAML config as lowest-priority overrides.
    """
    app_cfg = load_app_config(settings.app_config_path, settings.mcp_config_path)
    settings.app_config_object = app_cfg
    if not app_cfg:
        _apply_yaml_overrides(settings)
        _normalize_model_defaults(settings)
        return

    default_llm = app_cfg.llm.get("default")
    if default_llm:
        if not getattr(settings, "openai_api_key", ""):
            settings.openai_api_key = default_llm.api_key
        if not settings.primary_model:
            settings.primary_model = default_llm.model
        if not settings.openai_base_url:
            settings.openai_base_url = default_llm.base_url

    # Search engines
    if not settings.search_engines.strip() and app_cfg.search_config:
        engines = [app_cfg.search_config.engine] + list(
            app_cfg.search_config.fallback_engines or []
        )
        settings.search_engines = ",".join([e for e in engines if e])

    # Daytona
    if app_cfg.daytona_config:
        cfg = app_cfg.daytona_config
        if not settings.daytona_api_key and cfg.daytona_api_key:
            settings.daytona_api_key = cfg.daytona_api_key
        settings.daytona_server_url = (
            settings.daytona_server_url or cfg.daytona_server_url
        )
        settings.daytona_target = settings.daytona_target or cfg.daytona_target
        settings.daytona_image_name = (
            settings.daytona_image_name or cfg.sandbox_image_name
        )
        settings.daytona_entrypoint = (
            settings.daytona_entrypoint or cfg.sandbox_entrypoint
        )
        settings.daytona_vnc_password = (
            settings.daytona_vnc_password or cfg.VNC_password
        )

    # MCP servers
    if not settings.mcp_servers and app_cfg.mcp_config and app_cfg.mcp_config.servers:
        try:
            settings.mcp_servers = json.dumps(
                {k: v.model_dump() for k, v in app_cfg.mcp_config.servers.items()}
            )
        except Exception as e:
            logger.warning(f"Failed to inject MCP servers from app config: {e}")

    # Sandbox switch from TOML
    if (
        app_cfg.sandbox
        and app_cfg.sandbox.use_sandbox is False
        and settings.sandbox_mode == "local"
    ):
        # allow disabling sandbox via config
        settings.sandbox_mode = "none"

    _apply_yaml_overrides(settings)
    _normalize_model_defaults(settings)


def validate_critical_config(s: Settings) -> list[str]:
    """Run startup-time sanity checks on critical config fields.

    Returns a list of human-readable warning strings.  An empty list means all
    checks passed.  Callers (e.g. ``main.startup_event``) should log these at
    WARNING level so operators notice misconfigurations early.
    """
    warnings: list[str] = []

    # --- LLM credentials ---
    has_api_key = bool(s.openai_api_key) or bool(s.azure_api_key)
    if not has_api_key:
        warnings.append(
            "No LLM API key configured (OPENAI_API_KEY / AZURE_API_KEY). "
            "All LLM calls will fail."
        )

    if not s.primary_model.strip():
        warnings.append(
            "PRIMARY_MODEL is empty. "
            "Set it to a valid model name (e.g. deepseek-chat)."
        )

    # --- Search ---
    engines = [e.strip().lower() for e in s.search_engines_list if e.strip()]
    needs_tavily = "tavily" in engines
    needs_bocha = "bocha" in engines
    if needs_tavily and not s.tavily_api_key:
        warnings.append(
            "SEARCH_ENGINES includes 'tavily' but TAVILY_API_KEY is empty."
        )
    if needs_bocha and not s.bocha_api_key:
        warnings.append(
            "SEARCH_ENGINES includes 'bocha' but BOCHA_API_KEY is empty."
        )

    # --- Numeric sanity ---
    if s.deepsearch_max_epochs < 1:
        warnings.append(
            f"DEEPSEARCH_MAX_EPOCHS={s.deepsearch_max_epochs} is < 1; "
            "at least 1 epoch is required for any research output."
        )
    if s.deepsearch_supervisor_rounds < 1:
        warnings.append(
            f"DEEPSEARCH_SUPERVISOR_ROUNDS={s.deepsearch_supervisor_rounds} is < 1; "
            "supervisor mode requires at least 1 round."
        )

    return warnings


try:
    normalize_socks_proxy_env()
except Exception:
    # Best-effort only; never block settings initialization.
    pass

settings = Settings()
apply_app_config_overrides(settings)
