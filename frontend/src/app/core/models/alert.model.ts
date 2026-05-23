export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type AlertStatus = 'new' | 'acknowledged' | 'resolved';

export interface Alert {
  id: string;
  source: string;
  severity: Severity;
  title: string;
  body: string | null;
  external_id: string | null;
  status: AlertStatus;
  metadata_: Record<string, string | number | null> | null;
  location_name: string | null;
  location_city: string | null;
  acknowledged_by: string | null;
  created_at: string;
}

export interface AlertSummary {
  critical?: number;
  high?: number;
  medium?: number;
  low?: number;
  info?: number;
}
