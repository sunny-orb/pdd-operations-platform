"""集中读取公开配置与私密环境变量。"""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEEPSEEK_MODEL = "bailian:deepseek-v4.1-flash-1017"


class Settings(BaseSettings):
    """应用设置；环境变量为输入，字段校验后的设置供服务使用。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    assistant_production_enabled: bool = False
    assistant_max_concurrent: int = Field(default=1, ge=1, le=2)
    assistant_requests_per_minute: int = Field(default=20, ge=1, le=60)
    assistant_model: str = DEEPSEEK_MODEL
    assistant_business_data_enabled: bool = False
    assistant_business_providers: list[str] = Field(default_factory=list)
    bailian_api_key: SecretStr = SecretStr("")
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    database_url: SecretStr = SecretStr("")
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    db_pool_size: int = Field(default=8, ge=1, le=20)
    db_max_overflow: int = Field(default=4, ge=0, le=10)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    content_reuse_enabled: bool = False

    @property
    def assistant_models(self) -> list[str]:
        """只启用指定的 DeepSeek。留空或旧配置会关闭问答。"""
        return [DEEPSEEK_MODEL] if self.assistant_model == DEEPSEEK_MODEL else []

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """检查数据库类型；输入为私密 URL，输出原值，不在错误中输出凭据。"""
        url = value.get_secret_value()
        if url and not url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL 必须使用 postgresql+psycopg 协议")
        return value
