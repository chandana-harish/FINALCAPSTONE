import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # GitHub Configuration
    github_pat: str = Field(..., validation_alias="GITHUB_PAT")

    # OpenAI Configuration
    openai_api_key: str = Field(..., validation_alias="OPENAI_API_KEY")
    openai_model_name: str = Field("gpt-4o", validation_alias="OPENAI_MODEL_NAME")

    # Azure Cosmos DB Configuration
    cosmos_uri: str = Field(..., validation_alias="COSMOS_URI")
    cosmos_key: str = Field(..., validation_alias="COSMOS_KEY")
    cosmos_database: str = Field("FailureAnalyzerDB", validation_alias="COSMOS_DATABASE")
    cosmos_container: str = Field("analysis_results", validation_alias="COSMOS_CONTAINER")

    # App Settings
    api_title: str = "AI CI/CD Failure Analyzer API"
    debug: bool = False

    # Allow reading from .env file (first looks for it in project root, then current dir)
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings
settings = Settings(_env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
