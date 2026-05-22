export type KanbanStatus = 'backlog' | 'todo' | 'in_progress' | 'review' | 'done';
export type KanbanPriority = 'low' | 'medium' | 'high' | 'critical';

export interface KanbanCard {
  id: string;
  title: string;
  description: string | null;
  status: KanbanStatus;
  priority: KanbanPriority;
  jira_key: string | null;
  assigned_to: string | null;
  alert_id: string | null;
  ai_generated: boolean;
  position: number;
  created_at: string;
  updated_at: string;
}

export interface KanbanColumn {
  id: KanbanStatus;
  label: string;
  cards: KanbanCard[];
}

export interface KanbanCardCreate {
  title: string;
  description?: string;
  status?: KanbanStatus;
  priority?: KanbanPriority;
}

export interface KanbanCardUpdate {
  title?: string;
  description?: string;
  priority?: KanbanPriority;
  position?: number;
}

export interface KanbanCardMove {
  status: KanbanStatus;
  position: number;
}
