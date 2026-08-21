import { createClient } from '@supabase/supabase-js';

let cached = null;

export function getSupabase() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    return null;
  }
  if (cached) return cached;
  cached = createClient(url, key, {
    auth: { persistSession: false },
  });
  return cached;
}
