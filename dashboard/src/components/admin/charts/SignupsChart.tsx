/**
 * SignupsChart -- area chart showing new user signups over time.
 *
 * Uses Recharts AreaChart with blue-600 stroke and 10% opacity fill.
 * Wrapped in a Card with "Signups" heading.
 */
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface SignupsChartProps {
  data: Array<{ date: string; count: number }>;
  loading?: boolean;
}

export function SignupsChart({ data, loading }: SignupsChartProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Signups</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[300px] w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={data} aria-label="Signups over time">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="count"
                stroke="#2563eb"
                fill="#2563eb"
                fillOpacity={0.1}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
