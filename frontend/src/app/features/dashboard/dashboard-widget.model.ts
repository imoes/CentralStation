export interface DashboardWidget {
  id: string;
  user_id: string;
  dashboard_id?: string | null;
  widget_type: 'stat' | 'list' | 'donut' | 'bar' | 'ai_summary' | 'top_hosts' | 'timeseries' | 'grafana_panel' | 'forecast' | 'war_room' | 'incidents';
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
  rationale?: string | null;
  generated_at?: string | null;
  created_at?: string | null;
}

/** Response from the generative dashboard endpoints. */
export interface GenerativePayload {
  dashboard: Dashboard;
  widgets: DashboardWidget[];
  rationale?: string | null;
  generated_at?: string | null;
  hosts?: string[];
}

export interface RationaleSegment {
  text: string;
  host: string | null;
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

export interface IncidentsData {
  incidents: Array<{
    id: string;
    title: string;
    primary_host: string;
    severity: string;
    status: string;
    member_count: number;
    updated_at: string;
  }>;
  total: number;
}

export type WidgetData = StatData | ListData | DonutData | BarData | AiSummaryData | TopHostsData | TimeseriesData | GrafanaPanelData | ForecastData | WarRoomData | IncidentsData;

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

export interface WarRoomFinding {
  title: string;
  severity: string;
  description: string;
  host: string;
  source: string;
}

export interface WarRoomBlastRadius {
  host: string;
  location?: string | null;
  co_hosted_vms: string[];
  co_located_hosts: string[];
  reason: string;
}

export interface WarRoomData {
  active: boolean;
  analysis_id?: string;
  severity: string;
  findings: WarRoomFinding[];
  recommendations: Array<{ action: string; priority: string; rationale: string; jira_title?: string }>;
  blast_radius: WarRoomBlastRadius[];
  run_at?: string | null;
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
