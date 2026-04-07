/**
 * ActiveUsersChart -- line chart showing daily active users over time.
 *
 * Shows DAU as the primary series (blue-600). WAU and MAU are computed
 * client-side as rolling 7-day and 30-day sums when data length permits.
 *
 * Uses Recharts LineChart with legend below for multi-series.
 */
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface ActiveUsersChartProps {
  data: Array<{ date: string; dau: number }>;
  loading?: boolean;
}

/**
 * Compute rolling WAU (7-day sum) and MAU (30-day sum) from daily DAU data.
 */
function withRolling(data: Array<{ date: string; dau: number }>) {
  return data.map((point, idx) => {
    const wauSlice = data.slice(Math.max(0, idx - 6), idx + 1);
    const mauSlice = data.slice(Math.max(0, idx - 29), idx + 1);
    return {
      ...point,
      wau: wauSlice.reduce((sum, d) => sum + d.dau, 0),
      mau: mauSlice.reduce((sum, d) => sum + d.dau, 0),
    };
  });
}

export function ActiveUsersChart({ data, loading }: ActiveUsersChartProps) {
  const enriched = data.length > 0 ? withRolling(data) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Active Users</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-[300px] w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={enriched} aria-label="Active users over time">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey="dau"
                name="DAU"
                stroke="#2563eb"
                dot={false}
                strokeWidth={2}
              />
              <Line
                type="monotone"
                dataKey="wau"
                name="WAU"
                stroke="#8b5cf6"
                dot={false}
                strokeWidth={1.5}
              />
              <Line
                type="monotone"
                dataKey="mau"
                name="MAU"
                stroke="#0d9488"
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
