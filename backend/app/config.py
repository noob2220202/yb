from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./yb.db"

    use_demo_provider: bool = True

    pinnacle_base_url: str = "https://api.pinnacle.com"
    pinnacle_username: str = ""
    pinnacle_password: str = ""

    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com"
    odds_api_sport_keys: str = "soccer_epl"

    poll_sports: str = "soccer"
    poll_interval_seconds: int = 60

    api_keys: str = "dev-local-key"

    @property
    def poll_sports_list(self) -> list[str]:
        return [s.strip() for s in self.poll_sports.split(",") if s.strip()]

    @property
    def odds_api_sport_keys_list(self) -> list[str]:
        return [s.strip() for s in self.odds_api_sport_keys.split(",") if s.strip()]

    @property
    def api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def pinnacle_configured(self) -> bool:
        return bool(self.pinnacle_username and self.pinnacle_password)

    @property
    def odds_api_configured(self) -> bool:
        return bool(self.odds_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
