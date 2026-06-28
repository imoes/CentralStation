import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ProjectResponse {
  id: string;
  name: string;
  description: string | null;
  status: string;
  owner_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface StepNode {
  id: string;
  parent_step_id: string | null;
  title: string;
  description: string | null;
  status: string;
  jira_issue_type: string;
  priority: string;
  duration_days: number;
  story_points: number | null;
  sort_order: number;
  assignee: string | null;
  labels: string | null;        // JSON array string, e.g. '["label1","label2"]'
  due_date: string | null;
  acceptance_criteria: string | null;
  est_start: number | null;
  est_end: number | null;
  lst_start: number | null;
  lst_end: number | null;
  slack: number | null;
  critical: boolean;
  pos_x: number | null;
  pos_y: number | null;
  jira_connector_type: string | null;
  jira_key: string | null;
  jira_status: string | null;
  jira_status_category: string | null;
  implementation_notes: string | null;
}

export interface DepEdge {
  id: string;
  step_id: string;
  depends_on_step_id: string;
}

export interface PlanGraph {
  project: ProjectResponse;
  steps: StepNode[];
  deps: DepEdge[];
}

export interface ProposedStep {
  temp_id: string;
  title: string;
  description: string;
  jira_issue_type: string;
  duration_days: number;
  depends_on: string[];
  parent_temp_id: string | null;
  code_blocks: CodeBlock[];
  bash_commands: BashCommand[];
}

export interface ToolActivity {
  tool: string;     // web_search | web_fetch
  detail: string;   // query or URL
  ok: boolean;
}

export interface CodeBlock {
  lang: string;
  filename?: string;
  content: string;
}

export interface BashCommand {
  command: string;
  purpose: string;
}

export interface PlanQuestion {
  text: string;
  options: string[];
}

export interface PlanResponse {
  reply: string;
  steps: ProposedStep[];
  open_points: string[];
  sources: string[];
  tool_activity: ToolActivity[];
  question?: PlanQuestion;
  code_blocks: CodeBlock[];
  bash_commands: BashCommand[];
}

@Injectable({ providedIn: 'root' })
export class ProjectsService {
  private http = inject(HttpClient);
  private base = '/api/projects';

  list(search = ''): Observable<ProjectResponse[]> {
    return this.http.get<ProjectResponse[]>(this.base, { params: search ? { search } : {} });
  }

  create(name: string, description?: string): Observable<ProjectResponse> {
    return this.http.post<ProjectResponse>(this.base, { name, description });
  }

  get(id: string): Observable<ProjectResponse> {
    return this.http.get<ProjectResponse>(`${this.base}/${id}`);
  }

  update(id: string, patch: Partial<ProjectResponse>): Observable<ProjectResponse> {
    return this.http.patch<ProjectResponse>(`${this.base}/${id}`, patch);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  getGraph(id: string): Observable<PlanGraph> {
    return this.http.get<PlanGraph>(`${this.base}/${id}/graph`);
  }

  addStep(projectId: string, step: {
    title: string; description?: string; jira_issue_type?: string;
    duration_days?: number; sort_order?: number; parent_step_id?: string;
    depends_on?: string[]; pos_x?: number; pos_y?: number;
  }): Observable<StepNode> {
    return this.http.post<StepNode>(`${this.base}/${projectId}/steps`, step);
  }

  updateStep(projectId: string, stepId: string, patch: Partial<StepNode>): Observable<StepNode> {
    return this.http.patch<StepNode>(`${this.base}/steps/${stepId}`, patch, { params: { project_id: projectId } });
  }

  deleteStep(projectId: string, stepId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/steps/${stepId}`, { params: { project_id: projectId } });
  }

  addDep(projectId: string, stepId: string, dependsOnStepId: string): Observable<DepEdge> {
    return this.http.post<DepEdge>(`${this.base}/${projectId}/deps`, {
      step_id: stepId,
      depends_on_step_id: dependsOnStepId,
    });
  }

  removeDep(projectId: string, depId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/deps/${depId}`, { params: { project_id: projectId } });
  }

  attachTicket(projectId: string, stepId: string, connectorType: string, jiraKey: string): Observable<StepNode> {
    return this.http.post<StepNode>(`${this.base}/${projectId}/steps/${stepId}/attach-ticket`, {
      connector_type: connectorType, jira_key: jiraKey,
    });
  }

  createTicket(projectId: string, stepId: string, body: {
    connector_type: string; summary?: string; description?: string; issue_type?: string; epic_key?: string;
  }): Observable<StepNode> {
    return this.http.post<StepNode>(`${this.base}/${projectId}/steps/${stepId}/create-ticket`, body);
  }

  sync(projectId: string): Observable<{ updated: number }> {
    return this.http.post<{ updated: number }>(`${this.base}/${projectId}/sync`, {});
  }

  runPlanner(messages: { role: string; content: string }[], existingGraph?: PlanGraph): Observable<PlanResponse> {
    return this.http.post<PlanResponse>(`${this.base}/plan`, { messages, existing_graph: existingGraph ?? null });
  }

  savePlan(name: string, description: string | null, steps: ProposedStep[]): Observable<ProjectResponse> {
    return this.http.post<ProjectResponse>(`${this.base}/from-plan`, { name, description, steps });
  }

  openInWorkbench(projectId: string): Observable<{ ide_url: string; project_id: string }> {
    return this.http.post<{ ide_url: string; project_id: string }>('/api/ide/open-project', { project_id: projectId });
  }

  pullStepFromJira(projectId: string, stepId: string): Observable<StepNode> {
    return this.http.post<StepNode>(`${this.base}/steps/${stepId}/jira-pull`, {}, { params: { project_id: projectId } });
  }

  chatWithProject(projectId: string, message: string): Observable<ProjectChatResponse> {
    return this.http.post<ProjectChatResponse>(`${this.base}/${projectId}/chat`, { message });
  }

  getReadySteps(): Observable<ReadyStep[]> {
    return this.http.get<ReadyStep[]>(`${this.base}/ready-steps`);
  }
}

export interface ChatAction {
  type: string;
  step_id?: string;
  status?: string;
  title?: string;
  description?: string;
  jira_issue_type?: string;
  duration_days?: number;
}

export interface ProjectChatResponse {
  reply: string;
  actions: ChatAction[];
}

export interface ReadyStep {
  step_id: string;
  project_id: string;
  project_name: string;
  title: string;
  jira_issue_type: string;
  priority: string;
  status: string;
  jira_key?: string;
  assignee?: string;
}
