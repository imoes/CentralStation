import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  Connector, ConnectorCreate, ConnectorUpdate,
  ConnectorTestResult, SettingsResponse, SettingItem, ConnectorType,
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

  listMine() {
    return this.http.get<Connector[]>(`${this.api}/my`);
  }

  upsertMine(type: ConnectorType, data: ConnectorCreate) {
    return this.http.put<Connector>(`${this.api}/my/${type}`, data);
  }

  testMine(type: ConnectorType) {
    return this.http.post<ConnectorTestResult>(`${this.api}/my/${type}/test`, {});
  }

  deleteMine(type: ConnectorType) {
    return this.http.delete<void>(`${this.api}/my/${type}`);
  }

  createMine(data: ConnectorCreate) {
    return this.http.post<Connector>(`${this.api}/my/`, data);
  }

  updateMineById(id: string, data: ConnectorUpdate) {
    return this.http.patch<Connector>(`${this.api}/my/id/${id}`, data);
  }

  deleteMineById(id: string) {
    return this.http.delete<void>(`${this.api}/my/id/${id}`);
  }

  getSettings() {
    return this.http.get<SettingsResponse>(`${this.settingsApi}/`);
  }

  updateSetting(key: string, value: string | null) {
    return this.http.patch<SettingItem>(`${this.settingsApi}/${key}`, { value });
  }

  testSettingGroup(group: string) {
    return this.http.post<{ success: boolean; message: string; detail: string | null }>(
      `${this.settingsApi}/test/${group}`, {}
    );
  }
}
