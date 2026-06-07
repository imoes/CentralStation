export type ConnectorType =
  | 'checkmk' | 'graylog' | 'wazuh'
  | 'jira' | 'jira_sd'
  | 'o365' | 'teams' | 'prometheus' | 'netbox'
  | 'id_generator';

export interface Connector {
  id: string;
  name: string;
  type: ConnectorType;
  base_url: string | null;
  enabled: boolean;
  owner_user_id?: string | null;
  updated_at: string;
}

export interface ConnectorCreate {
  name: string;
  type: ConnectorType;
  base_url: string | null;
  credentials: Record<string, string | string[]>;
  enabled: boolean;
}

export interface ConnectorUpdate {
  name?: string;
  base_url?: string;
  credentials?: Record<string, string | string[]>;
  enabled?: boolean;
}

export interface ConnectorTestResult {
  success: boolean;
  message: string;
}

export interface SettingItem {
  key: string;
  value: string | null;
  is_secret: boolean;
}

export interface SettingsResponse {
  settings: SettingItem[];
}
