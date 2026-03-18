"""Supabase client — connects to the shared Sparkwright Supabase project.
Gauntlet tables are namespaced with gauntlet_ prefix."""

import os
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

_client: Client = None


def get_client() -> Client:
    """Get or create Supabase client (singleton)."""
    global _client
    if _client is None:
        if not SUPABASE_URL:
            raise ValueError("SUPABASE_URL not set")
        if not SUPABASE_KEY:
            raise ValueError("SUPABASE_SECRET_KEY not set")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client
