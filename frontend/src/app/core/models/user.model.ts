export type Role = 'admin' | 'sysadmin' | 'network_technician' | 'viewer';

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: Role;
  is_active: boolean;
  created_at: string;
  computer_console_enabled: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}
