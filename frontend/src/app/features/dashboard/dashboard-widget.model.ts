export interface DashboardWidget {
  id: string;
  user_id: string;
  widget_type: 'stat' | 'list' | 'donut' | 'timeseries' | 'grafana_panel';
  title: string;
  gs_x: number;
  gs_y: number;
  gs_w: number;
  gs_h: number;
  config: Record<string, unknown>;
}

export interface FeedItem {
  id: string;
  source: string;
  severity: string;
  title: string;
  body?: string | null;
  created_at: string;
  external_url?: string | null;
  metadata?: Record<string, unknown> | null;
}

export type WidgetData = StatData | ListData | DonutData | TimeseriesData | GrafanaPanelData;

export interface StatData {
  count: number;
}

export interface ListData {
  items: FeedItem[];
}

export interface DonutData {
  buckets: Array<{ key: string; count: number }>;
}

export interface TimeseriesData {
  series: Array<{ time: string; value: number }>;
  unit?: string;
  error?: string;
}

export interface GrafanaPanelData {
  panel_url: string;
  refresh_seconds?: number;
}

export interface DashboardWidgetCreate {
  widget_type: DashboardWidget['widget_type'];
  title: string;
  gs_x?: number;
  gs_y?: number;
  gs_w?: number;
  gs_h?: number;
  config: Record<string, unknown>;
}

export const SEVERITY_COLORS: Record<string, string> = {
  critical: '#b71c1c',
  high: '#e65100',
  medium: '#f9a825',
  low: '#1565c0',
  info: '#546e7a',
};
