-- =====================================================
-- THE GAUNTLET — RPG Database Schema
-- Tables for the standalone Gauntlet RPG app.
-- All tables prefixed with gauntlet_ to avoid collision
-- with Sparkwright core tables in the same Supabase project.
-- Run in Supabase SQL Editor.
-- =====================================================

-- Game sessions
CREATE TABLE IF NOT EXISTS public.gauntlet_games (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    setting TEXT NOT NULL,
    tone TEXT NOT NULL,
    genre TEXT NOT NULL,
    world_summary TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'abandoned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.gauntlet_games IS 'Gauntlet RPG — game sessions (standalone app, not part of Sparkwright core)';

-- Characters (up to 3 per game)
CREATE TABLE IF NOT EXISTS public.gauntlet_characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES public.gauntlet_games(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    personality TEXT,
    voice_type TEXT,
    voice_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.gauntlet_characters IS 'Gauntlet RPG — characters per game (standalone app)';

-- Turn history with full state snapshots
CREATE TABLE IF NOT EXISTS public.gauntlet_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id UUID NOT NULL REFERENCES public.gauntlet_games(id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL DEFAULT 0,
    game_state JSONB NOT NULL DEFAULT '{}',
    narration_text TEXT,
    narration_audio_url TEXT,
    dialogue JSONB DEFAULT '[]',
    image_url TEXT,
    image_prompt TEXT,
    choices JSONB DEFAULT '[]',
    player_choice INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.gauntlet_turns IS 'Gauntlet RPG — turn history with state snapshots (standalone app)';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_gauntlet_games_user ON public.gauntlet_games(user_id);
CREATE INDEX IF NOT EXISTS idx_gauntlet_characters_game ON public.gauntlet_characters(game_id);
CREATE INDEX IF NOT EXISTS idx_gauntlet_turns_game ON public.gauntlet_turns(game_id, turn_number);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION public.gauntlet_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gauntlet_games_updated ON public.gauntlet_games;
CREATE TRIGGER gauntlet_games_updated
    BEFORE UPDATE ON public.gauntlet_games
    FOR EACH ROW EXECUTE FUNCTION public.gauntlet_update_timestamp();

-- =====================================================
-- RLS — users can only see/edit their own games
-- =====================================================

ALTER TABLE public.gauntlet_games ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gauntlet_characters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gauntlet_turns ENABLE ROW LEVEL SECURITY;

-- Games
CREATE POLICY "gauntlet_games_select" ON public.gauntlet_games
    FOR SELECT USING (public.is_admin() OR user_id = auth.uid());
CREATE POLICY "gauntlet_games_insert" ON public.gauntlet_games
    FOR INSERT WITH CHECK (public.is_admin() OR user_id = auth.uid());
CREATE POLICY "gauntlet_games_update" ON public.gauntlet_games
    FOR UPDATE USING (public.is_admin() OR user_id = auth.uid());
CREATE POLICY "gauntlet_games_delete" ON public.gauntlet_games
    FOR DELETE USING (public.is_admin() OR user_id = auth.uid());

-- Characters (access via game ownership)
CREATE POLICY "gauntlet_characters_select" ON public.gauntlet_characters
    FOR SELECT USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_characters_insert" ON public.gauntlet_characters
    FOR INSERT WITH CHECK (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_characters_update" ON public.gauntlet_characters
    FOR UPDATE USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_characters_delete" ON public.gauntlet_characters
    FOR DELETE USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );

-- Turns (access via game ownership)
CREATE POLICY "gauntlet_turns_select" ON public.gauntlet_turns
    FOR SELECT USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_turns_insert" ON public.gauntlet_turns
    FOR INSERT WITH CHECK (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_turns_update" ON public.gauntlet_turns
    FOR UPDATE USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );
CREATE POLICY "gauntlet_turns_delete" ON public.gauntlet_turns
    FOR DELETE USING (
        public.is_admin() OR game_id IN (SELECT id FROM public.gauntlet_games WHERE user_id = auth.uid())
    );

-- =====================================================
-- Storage bucket (run separately if needed):
-- INSERT INTO storage.buckets (id, name, public)
-- VALUES ('gauntlet-media', 'gauntlet-media', true);
-- =====================================================
