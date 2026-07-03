from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    app_env: str = "development"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "fraud_db"
    postgres_user: str = "postgres"
    postgres_password: str = "password"

    redis_host: str = "localhost"
    redis_port: int = 6379

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = BackendSettings()
