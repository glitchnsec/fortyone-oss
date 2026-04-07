/**
 * StatCard -- displays a single platform metric with icon, value, and label.
 *
 * Used on the Admin Overview page for Total Users, Active Today,
 * Messages Today, and Pending Tasks.
 *
 * Loading state renders a Skeleton matching card dimensions.
 */
import { type ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface StatCardProps {
  label: string;
  value: number | string;
  icon: ReactNode;
  loading?: boolean;
}

export function StatCard({ label, value, icon, loading }: StatCardProps) {
  if (loading) {
    return (
      <Card className="bg-neutral-50 border border-neutral-200 rounded-lg p-4">
        <Skeleton className="h-6 w-6 rounded" />
        <Skeleton className="mt-3 h-8 w-20" />
        <Skeleton className="mt-2 h-4 w-24" />
      </Card>
    );
  }

  return (
    <Card className="bg-neutral-50 border border-neutral-200 rounded-lg p-4">
      <div className="text-neutral-500">{icon}</div>
      <div className="mt-3 text-[28px] font-semibold leading-tight text-neutral-900">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      <div className="mt-1 text-sm text-neutral-500">{label}</div>
    </Card>
  );
}
