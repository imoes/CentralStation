import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  JiraComment, JiraDetail, KanbanCard, KanbanCardCreate, KanbanCardMove, KanbanCardUpdate,
} from '../models/kanban.model';

@Injectable({ providedIn: 'root' })
export class KanbanService {
  private api = `${environment.apiUrl}/kanban`;

  constructor(private http: HttpClient) {}

  list() {
    return this.http.get<KanbanCard[]>(`${this.api}/`);
  }

  create(data: KanbanCardCreate) {
    return this.http.post<KanbanCard>(`${this.api}/`, data);
  }

  update(id: string, data: KanbanCardUpdate) {
    return this.http.patch<KanbanCard>(`${this.api}/${id}`, data);
  }

  move(id: string, data: KanbanCardMove) {
    return this.http.post<KanbanCard>(`${this.api}/${id}/move`, data);
  }

  delete(id: string) {
    return this.http.delete<void>(`${this.api}/${id}`);
  }

  syncJira(id: string) {
    return this.http.post<{ jira_key: string }>(`${this.api}/${id}/jira-sync`, {});
  }

  getJiraDetail(id: string) {
    return this.http.get<JiraDetail>(`${this.api}/${id}/jira-detail`);
  }

  addJiraComment(id: string, body: string) {
    return this.http.post<JiraComment>(`${this.api}/${id}/jira-comment`, { body });
  }
}
