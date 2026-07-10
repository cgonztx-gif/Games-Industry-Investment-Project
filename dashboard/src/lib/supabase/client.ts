import { createClient } from "@supabase/supabase-js"

// Browser/Server-Component READ client. Uses the anon key, which is safe to
// expose to the browser (NEXT_PUBLIC_*). RLS on each table must allow SELECT
// for the anon role, or this returns empty results -- see dashboard/README
// (or CLAUDE.md's Phase 7 notes) for the per-table RLS policy this depends on.
export function createAnonClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    { auth: { persistSession: false } }
  )
}
