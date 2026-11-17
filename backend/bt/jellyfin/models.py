"""Jellyfin tables: one linked Jellyfin user per Hub profile."""
import time

from sqlmodel import Field, SQLModel


class JellyfinLink(SQLModel, table=True):
    profile_id: int = Field(primary_key=True)
    user_id: str = ''
    user_name: str = ''
    token: str = ''          # Jellyfin access token; never sent to the browser
    device_id: str = ''      # stable per link: Jellyfin keys sessions and transcodes by device
    server_id: str = ''
    is_admin: bool = False   # Jellyfin administrator (can read the server's encoding settings)
    created_at: float = Field(default_factory=time.time)
