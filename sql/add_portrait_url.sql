-- Add portrait_url column to gauntlet_characters for IP-Adapter face references
ALTER TABLE public.gauntlet_characters ADD COLUMN IF NOT EXISTS portrait_url TEXT;
