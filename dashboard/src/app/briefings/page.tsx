import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { BriefingDetail, formatWeekOf } from "@/components/briefing-detail"
import { PastBriefingRow } from "@/components/past-briefing-row"
import { getBriefings, getEntityLookups } from "@/lib/data/briefings"

// New briefing lands every weekly pipeline run -- must never serve a
// build-time snapshot (same bug this dashboard already hit once, see
// tasks.md's Phase 7 notes).
export const dynamic = "force-dynamic"

export default async function BriefingsPage() {
  const briefings = await getBriefings()
  const { gameTitles, studioNames } = await getEntityLookups(briefings)

  const [latest, ...past] = briefings

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Weekly Briefings</h1>
        <p className="text-sm text-muted-foreground">
          The Synthesis agent&apos;s weekly account of divergence signals, risk flags, and its
          own reasoning for the week.
        </p>
      </div>

      {!latest ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No weekly briefings yet — the Synthesis agent writes one after each weekly pipeline run.
        </p>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>{formatWeekOf(latest.week_of)}</CardTitle>
              <CardDescription>{latest.briefing_text}</CardDescription>
            </CardHeader>
            <CardContent>
              <BriefingDetail briefing={latest} gameTitles={gameTitles} studioNames={studioNames} />
            </CardContent>
          </Card>

          {past.length > 0 && (
            <div>
              <h2 className="mb-2 text-lg font-semibold tracking-tight">Past Briefings</h2>
              <div className="space-y-3">
                {past.map((briefing) => (
                  <PastBriefingRow
                    key={briefing.id}
                    briefing={briefing}
                    gameTitles={gameTitles}
                    studioNames={studioNames}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
