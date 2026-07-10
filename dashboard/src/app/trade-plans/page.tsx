import { TradePlanCard } from "@/components/trade-plan-card"
import { getPendingTradePlans } from "@/lib/data/trade-plans"

// New trade plans land after every Portfolio Manager run, and status changes
// on every approve/reject -- must never serve a build-time snapshot.
export const dynamic = "force-dynamic"

export default async function TradePlansPage() {
  const plans = await getPendingTradePlans()

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Trade Plans</h1>
        <p className="text-sm text-muted-foreground">
          Weekly trade plans the Portfolio Manager proposed. Approving a plan approves every
          pending order under it; individual orders can also be overridden below.
        </p>
      </div>

      {plans.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No pending trade plans — the Portfolio Manager writes one after each weekly run.
        </p>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {plans.map((plan) => (
            <TradePlanCard key={plan.plan_id} plan={plan} />
          ))}
        </div>
      )}
    </div>
  )
}
