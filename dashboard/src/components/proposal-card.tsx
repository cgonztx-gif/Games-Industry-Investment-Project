"use client"

import * as React from "react"
import { useTransition } from "react"
import { ExternalLinkIcon, CheckIcon, XIcon } from "lucide-react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type { PendingProposal } from "@/lib/data/watchlist-proposals"
import { approveProposal, rejectProposal } from "@/app/watchlist-proposals/actions"

function quickLink(proposal: PendingProposal): { label: string; href: string } | null {
  const steamId = proposal.games?.steam_app_id
  if (steamId) {
    return { label: "View on Steam", href: `https://store.steampowered.com/app/${steamId}` }
  }
  const rawgSlug = proposal.games?.rawg_slug
  if (rawgSlug) {
    return { label: "View on RAWG", href: `https://rawg.io/games/${rawgSlug}` }
  }
  return null
}

export function ProposalCard({ proposal }: { proposal: PendingProposal }) {
  const [isPending, startTransition] = useTransition()
  const [resolved, setResolved] = React.useState(false)

  const title = proposal.games?.title ?? proposal.studios?.name ?? "Unknown"
  const isCompanyLevel = !proposal.game_id
  const link = quickLink(proposal)

  function handleDecision(decision: "approve" | "reject") {
    startTransition(async () => {
      const action = decision === "approve" ? approveProposal : rejectProposal
      const result = await action(proposal.proposal_id)
      if (result.success) {
        toast.success(`${title} ${decision === "approve" ? "approved" : "rejected"}`)
        setResolved(true)
      } else {
        toast.error(result.error ?? "Something went wrong")
      }
    })
  }

  if (resolved) return null

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle>{title}</CardTitle>
            <CardDescription>
              {isCompanyLevel ? "Company-level proposal" : "Game-level proposal"}
              {proposal.studios?.name && !isCompanyLevel ? ` · ${proposal.studios.name}` : ""}
              {proposal.studios?.ticker ? ` (${proposal.studios.ticker})` : ""}
            </CardDescription>
          </div>
          <div className="flex flex-col items-end gap-1">
            {proposal.score !== null && (
              <Badge variant="secondary" className="tabular-nums">
                Score {proposal.score}
              </Badge>
            )}
            {proposal.recommended_sentiment_tier && (
              <Badge variant="outline">{proposal.recommended_sentiment_tier}</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {proposal.trigger_signal && (
          <div>
            <div className="text-xs font-medium text-muted-foreground">Trigger signal</div>
            <p className="text-sm">{proposal.trigger_signal}</p>
          </div>
        )}
        {proposal.claude_rationale && (
          <div>
            <div className="text-xs font-medium text-muted-foreground">Rationale</div>
            <p className="text-sm text-muted-foreground">{proposal.claude_rationale}</p>
          </div>
        )}
        {link && (
          <a
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            {link.label}
            <ExternalLinkIcon className="size-3.5" />
          </a>
        )}
      </CardContent>
      <CardFooter className="gap-2">
        <Button
          size="sm"
          disabled={isPending}
          onClick={() => handleDecision("approve")}
        >
          <CheckIcon />
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={isPending}
          onClick={() => handleDecision("reject")}
        >
          <XIcon />
          Reject
        </Button>
      </CardFooter>
    </Card>
  )
}
