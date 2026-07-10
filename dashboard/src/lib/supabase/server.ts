import "server-only"
import { createClient } from "@supabase/supabase-js"

// WRITE client -- service_role key, full RLS bypass. Import only from Server
// Actions / Route Handlers, never from a Client Component. `server-only`
// makes any accidental client-side import a build error instead of a leak.
export function createServiceRoleClient() {
  return createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_ROLE_KEY!,
    { auth: { persistSession: false } }
  )
}
