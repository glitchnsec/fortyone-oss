/**
 * MessagesChart -- bar chart showing messages per day.
 *
 * Uses Recharts BarChart with blue-600 fill.
 * Wrapped in a Card with "Messages per Day" heading.
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

interface MessagesChartProps {
  data: Array<{ date: string; count: number }>;
  loading?: boolean;
}

export function MessagesChart({ data, loading }: MessagesChartProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Messages per Day</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[300px] w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={data} aria-label="Messages per day">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#2563eb" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
