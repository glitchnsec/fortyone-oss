/**
 * IntentsChart -- horizontal bar chart showing top intents by count.
 *
 * Uses Recharts BarChart with layout="vertical" for horizontal bars.
 * Data is sorted descending and capped at 10 items.
 * Wrapped in a Card with "Top Intents" heading.
 */
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface IntentsChartProps {
  data: Array<{ intent: string; count: number }>;
  loading?: boolean;
}

export function IntentsChart({ data, loading }: IntentsChartProps) {
  const sorted = [...data]
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  const chartHeight = Math.max(240, sorted.length * 36);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Top Intents</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[300px] w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart
              data={sorted}
              layout="vertical"
              aria-label="Top intents by count"
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
              <YAxis
                type="category"
                dataKey="intent"
                tick={{ fontSize: 12 }}
                width={120}
              />
              <Tooltip />
              <Bar dataKey="count" fill="#2563eb" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
