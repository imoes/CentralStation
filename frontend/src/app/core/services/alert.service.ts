import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Alert, AlertSummary } from '../models/alert.model';

@Injectable({ providedIn: 'root' })
export class AlertService {
  private api = `${environment.apiUrl}/alerts`;

  constructor(private http: HttpClient) {}

  list(params?: { source?: string; severity?: string; status?: string; limit?: number }) {
    let p = new HttpParams();
    if (params?.source) p = p.set('source', params.source);
    if (params?.severity) p = p.set('severity', params.severity);
    if (params?.status) p = p.set('status', params.status);
    if (params?.limit) p = p.set('limit', params.limit);
    return this.http.get<Alert[]>(`${this.api}/`, { params: p });
  }

  summary() {
    return this.http.get<AlertSummary>(`${this.api}/summary`);
  }

  acknowledge(id: string) {
    return this.http.post<{ message: string }>(`${this.api}/${id}/acknowledge`, {});
  }

  aggregate() {
    return this.http.post<{ new_alerts: number }>(`${this.api}/aggregate`, {});
  }
}
