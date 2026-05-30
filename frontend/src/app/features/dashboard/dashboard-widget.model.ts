export interface DashboardWidget {
  id: string;
  user_id: string;
  dashboard_id?: string | null;
  widget_type: 'stat' | 'list' | 'donut' | 'bar' | 'ai_summary' | 'top_hosts' | 'timeseries' | 'grafana_panel' | 'forecast';
  title: string;
  gs_x: number;
  gs_y: number;
  gs_w: number;
  gs_h: number;
  config: Record<string, unknown>;
  pinned: boolean;
  hidden: boolean;
}

export interface Dashboard {
  id: string;
  user_id: string;
  name: string;
  description?: string | null;
  is_default: boolean;
  position: number;
  mode: 'classic' | 'generative';
  created_at?: string | null;
}

export interface LayoutPlacement {
  widget_id: string;
  gs_x: number;
  gs_y: number;
  gs_w: number;
  gs_h: number;
  hidden: boolean;
  pinned: boolean;
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

export type WidgetData = StatData | ListData | DonutData | BarData | AiSummaryData | TopHostsData | TimeseriesData | GrafanaPanelData | ForecastData;

export interface StatData {
  count: number;
}

export interface ListData {
  items: FeedItem[];
}

export interface DonutData {
  buckets: Array<{ key: string; count: number }>;
}

export interface BarData {
  buckets: Array<{ key: string; count: number }>;
  agg_field: string;
}

export interface AiSummaryData {
  analysis_id?: string | null;
  summary: string;
  findings: Array<{ title: string; severity?: string; description?: string; host?: string | null; source?: string }>;
  recommendations: Array<{ title: string; priority?: string; description?: string }>;
  run_at?: string | null;
}

export interface TopHostsData {
  hosts: Array<{ host: string; count: number; items: FeedItem[]; external_url?: string | null }>;
}

export interface TimeseriesData {
  series?: Array<{ time: string; value: number }>;
  series_list?: Array<{ label: string; series: Array<{ time: string; value: number }>; error?: string }>;
  unit?: string;
  error?: string;
}

export interface ForecastData {
  series_history: Array<{ time: string; value: number }>;
  series_forecast: Array<{ time: string; value: number }>;
  confidence_band: Array<{ time: string; lower: number; upper: number }>;
  title?: string;
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
  dashboard_id?: string | null;
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
