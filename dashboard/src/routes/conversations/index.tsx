/**
 * Conversations page — paginated message history table.
 *
 * Fetches GET /api/v1/conversations?page=1&limit=20
 * Table columns: Time | Direction (in/out badge) | Message (truncated) | Intent badge
 * Empty state: "No conversations yet" / "Send a message to your assistant via SMS to get started."
 * Pagination: prev/next buttons when total > limit
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2, MessageSquare } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchWithAuth } from "@/lib/api";

export const Route = createFileRoute("/conversations/")({
  component: ConversationsPage,
});

// ─── Types ────────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  direction: "inbound" | "outbound";
  body: string;
  intent: string | null;
  channel: string;
  created_at: string;
}

interface ConversationsResponse {
  conversations: Message[];
  total: number;
  page: number;
  limit: number;
}

const LIMIT = 20;

// ─── Page ────────────────────────────────────────────────────────────────────

function ConversationsPage() {
  const [page, setPage] = useState(1);
  const [channel, setChannel] = useState<string>("all");

  const { data, isLoading, isError } = useQuery<ConversationsResponse>({
    queryKey: ["conversations", page, channel],
    queryFn: () => {
      const params = new URLSearchParams({ page: String(page), limit: String(LIMIT) });
      if (channel !== "all") params.set("channel", channel);
      return fetchWithAuth(`/api/v1/conversations?${params}`).then(
        (r) => r.json()
      ) as Promise<ConversationsResponse>;
    },
  });

  const messages = data?.conversations ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / LIMIT);

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-neutral-400" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-6 sm:py-8">
      <h1 className="mb-6 text-xl font-semibold text-neutral-900">Conversation History</h1>

      {/* Channel filter tabs */}
      <div className="mb-4 flex -space-x-px">
        {(["all", "sms", "slack"] as const).map((ch, i) => {
          const isActive = channel === ch;
          const label = ch === "all" ? "All" : ch === "sms" ? "SMS" : "Slack";
          const rounded = i === 0 ? "rounded-l-md" : i === 2 ? "rounded-r-md" : "";
          return (
            <button
              key={ch}
              onClick={() => { setChannel(ch); setPage(1); }}
              className={`px-4 py-1.5 text-sm font-medium border ${rounded} ${
                isActive
                  ? "bg-neutral-900 text-white border-neutral-900 z-10"
                  : "bg-neutral-100 text-neutral-600 border-neutral-200 hover:bg-neutral-200"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {isError && (
        <p className="mb-4 text-sm text-red-600">
          Failed to load conversations. Please refresh the page.
        </p>
      )}

      {!isLoading && messages.length === 0 ? (
        <EmptyState channel={channel} />
      ) : (
        <>
          <div className="overflow-x-auto rounded-md border border-neutral-200">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">Time</TableHead>
                  <TableHead className="hidden sm:table-cell w-[100px]">Direction</TableHead>
                  <TableHead className="hidden sm:table-cell w-[80px]">Channel</TableHead>
                  <TableHead>Message</TableHead>
                  <TableHead className="hidden sm:table-cell w-[120px]">Intent</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {messages.map((msg) => (
                  <TableRow key={msg.id}>
                    <TableCell className="text-xs text-neutral-500">
                      {formatDate(msg.created_at)}
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <DirectionBadge direction={msg.direction} />
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      <ChannelBadge channel={msg.channel} />
                    </TableCell>
                    <TableCell className="max-w-xs">
                      <span className="block truncate text-sm text-neutral-800" title={msg.body}>
                        {msg.body}
                      </span>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell">
                      {msg.intent ? (
                        <IntentBadge intent={msg.intent} />
                      ) : (
                        <span className="text-xs text-neutral-400">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                Previous
              </Button>
              <span className="text-sm text-neutral-500">
                Page {page} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState({ channel }: { channel: string }) {
  const channelLabel = channel === "all" ? "" : channel === "sms" ? " SMS" : " Slack";
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <MessageSquare className="h-10 w-10 text-neutral-300" />
      <h2 className="text-2xl font-semibold text-neutral-900">
        No{channelLabel} conversations yet
      </h2>
      <p className="max-w-xs text-sm text-neutral-500">
        {channel === "all"
          ? "Send a message to your assistant via SMS to get started."
          : `No messages found for the ${channel === "sms" ? "SMS" : "Slack"} channel.`}
      </p>
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function ChannelBadge({ channel }: { channel: string }) {
  if (channel === "slack") {
    return (
      <Badge className="bg-purple-100 text-purple-700 hover:bg-purple-100 text-xs">Slack</Badge>
    );
  }
  return (
    <Badge className="bg-green-100 text-green-700 hover:bg-green-100 text-xs">SMS</Badge>
  );
}

function DirectionBadge({ direction }: { direction: "inbound" | "outbound" }) {
  if (direction === "inbound") {
    return (
      <Badge className="bg-blue-100 text-blue-700 hover:bg-blue-100 text-xs">In</Badge>
    );
  }
  return (
    <Badge className="bg-neutral-100 text-neutral-600 hover:bg-neutral-100 text-xs">Out</Badge>
  );
}

function IntentBadge({ intent }: { intent: string }) {
  const label = intent.replace(/_/g, " ");
  return (
    <Badge variant="outline" className="text-xs capitalize text-neutral-600">
      {label}
    </Badge>
  );
}

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}
