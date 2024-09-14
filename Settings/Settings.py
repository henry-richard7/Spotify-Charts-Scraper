from pydantic_settings import BaseSettings


class SpotifySettings(BaseSettings):
    client_id: str
    client_secret: str
    redirect_url: str
    redirect_port: int

    class Config:
        env_file = ".env"
