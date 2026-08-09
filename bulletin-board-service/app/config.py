from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'bulletin-board-service'
    environment: str = 'kubernetes'
    log_level: str = 'INFO'

    database_url: str = Field(alias='BULLETIN_DATABASE_URL')
    database_startup_retries: int = 20
    database_startup_retry_seconds: float = 1.0

    db_pool_size: int = Field(default=5, alias='DB_POOL_SIZE')
    db_max_overflow: int = Field(default=10, alias='DB_MAX_OVERFLOW')
    db_pool_timeout_seconds: float = Field(default=2.0, alias='DB_POOL_TIMEOUT_SECONDS')

    demo_faults_enabled: bool = Field(default=False, alias='DEMO_FAULTS_ENABLED')
    memory_growth_chunk_mib: int = Field(default=12, alias='MEMORY_GROWTH_CHUNK_MIB')
    memory_growth_interval_seconds: float = Field(default=1.5, alias='MEMORY_GROWTH_INTERVAL_SECONDS')
    memory_growth_start_delay_seconds: float = Field(default=3.0, alias='MEMORY_GROWTH_START_DELAY_SECONDS')

    @field_validator('database_url')
    @classmethod
    def require_postgres(cls, value: str) -> str:
        if not value.startswith(('postgresql://', 'postgresql+psycopg://')):
            raise ValueError('BULLETIN_DATABASE_URL must reference PostgreSQL')
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
