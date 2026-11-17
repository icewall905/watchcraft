"""Jellyfin: browse and play a household Jellyfin server inside The Hub.

Jellyfin keeps the library, metadata, watched state and transcoding (including hardware
transcoding, configured in Jellyfin). The Hub signs each profile in as its own Jellyfin user,
keeps the token server-side and proxies images and streams, so devices never see the token.
"""
