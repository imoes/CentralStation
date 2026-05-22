export type KanbanStatus = 'backlog' | 'todo' | 'in_progress' | 'review' | 'done';

export interface KanbanCard {
  id: string;
  title: string;
  description: string | null;
  status: KanbanStatus;
  priority: string;
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
