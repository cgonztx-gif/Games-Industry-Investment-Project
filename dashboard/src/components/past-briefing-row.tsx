"use client"

import * as React from "react"
import { ChevronDownIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { BriefingDetail, formatWeekOf } from "@/components/briefing-detail"
import type { WeeklyBriefing } from "@/lib/data/briefings"

export function PastBriefingRow({
  briefing,
  gameTitles,
  studioNames,
}: {
  briefing: WeeklyBriefing
  gameTitles: Map<string, string>
  studioNames: Map<string, { name: string; ticker: string | null }>
}) {
  const [open, setOpen] = React.useState(false)

  return (
    <Card>
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="w-full text-left">
          <CardHeader className="flex flex-row items-center justify-between gap-4">
            <div>
              <CardTitle>{formatWeekOf(briefing.week_of)}</CardTitle>
              <CardDescription>{briefing.briefing_text}</CardDescription>
            </div>
            <ChevronDownIcon
              className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
            />
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent>
            <BriefingDetail briefing={briefing} gameTitles={gameTitles} studioNames={studioNames} />
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}
