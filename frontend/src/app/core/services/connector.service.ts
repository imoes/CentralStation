import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  Connector, ConnectorCreate, ConnectorUpdate,
  ConnectorTestResult, SettingsResponse, SettingItem,
} from '../models/connector.model';

@Injectable({ providedIn: 'root' })
export class ConnectorService {
  private api = `${environment.apiUrl}/connectors`;
  private settingsApi = `${environment.apiUrl}/settings`;

  constructor(private http: HttpClient) {}

  list() {
    return this.http.get<Connector[]>(`${this.api}/`);
  }

  create(data: ConnectorCreate) {
    return this.http.post<Connector>(`${this.api}/`, data);
  }

  update(id: string, data: ConnectorUpdate) {
    return this.http.patch<Connector>(`${this.api}/${id}`, data);
  }

  delete(id: string) {
    return this.http.delete<void>(`${this.api}/${id}`);
  }

  test(id: string) {
    return this.http.post<ConnectorTestResult>(`${this.api}/${id}/test`, {});
  }

  getSettings() {
    return this.http.get<SettingsResponse>(`${this.settingsApi}/`);
  }

  updateSetting(key: string, value: string | null) {
    return this.http.patch<SettingItem>(`${this.settingsApi}/${key}`, { value });
  }
}
