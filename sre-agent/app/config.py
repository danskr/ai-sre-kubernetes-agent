from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_namespace: str = "bulletin-board"
    app_label_selector: str = "app=bulletin-board"
    app_deployment_name: str = "bulletin-board"
    app_container_name: str = "api"
    app_probe_url: str = "http://bulletin-board.bulletin-board.svc.cluster.local/health/ready"
    observe_interval_seconds: int = 5

    # One Agent Server hosts the single exported sre_agent graph. The background
    # observer submits operational incidents here so Studio sees the exact same
    # workflow runs and can resume Scenario 3 interrupts.
    agent_server_url: str = "http://127.0.0.1:2024"
    agent_server_assistant_id: str = "sre_agent"
    agent_server_run_timeout_seconds: int = 120
    agent_server_connect_attempts: int = 5
    agent_server_connect_retry_seconds: float = 1.0

    self_heal_verify_timeout_seconds: int = 45
    self_heal_verify_successes: int = 2

    database_url: str
    openai_api_key: str
    openai_model: str

    auto_remediation_enabled: bool = True
    auto_remediation_failure_threshold: int = 2
    auto_remediation_recent_deployment_seconds: int = 180
    auto_remediation_confidence_threshold: float = 0.85
    auto_remediation_cooldown_seconds: int = 90
    rollback_verify_timeout_seconds: int = 60
    rollback_verify_successes: int = 3

    # Scenario 3: resource-pressure incidents are never auto-remediated. These
    # settings control detection and the bounded action available *after* a human
    # explicitly approves it at the interrupt node in the unified graph.
    resource_oom_restart_threshold: int = 2
    human_memory_limit_target: str = "512Mi"
    human_memory_limit_max: str = "512Mi"
    resource_verify_timeout_seconds: int = 45
    resource_verify_successes: int = 5


settings = Settings()
