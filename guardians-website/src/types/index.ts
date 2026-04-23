export type TaskCategory =
  | 'Code Quality'
  | 'Security'
  | 'Performance'
  | 'Documentation';

export type TaskSeverity = 'critical' | 'warning' | 'info';

export type PageKey = 'onboarding' | 'tasks' | 'monitor';

export interface Task {
  id: string;
  title: string;
  description: string;
  category: TaskCategory;
  severity: TaskSeverity;
  checkType: string;
  fileTypes: string[];
  exampleViolation: string;
  suggestedFix: string;
  docReference: string;
}

export interface UploadedFile {
  id: string;
  name: string;
  type: string;
  size: number;
  status: 'uploading' | 'complete' | 'error';
  progress?: number;
}

export interface Violation {
  severity: TaskSeverity;
  rule: string;
  message: string;
  file: string;
  line: number;
  suggestedFix?: string;
  codeSnippet?: string;
}

export interface PullRequest {
  id: string;
  number: number;
  title: string;
  repository: string;
  author: { name: string; avatar: string };
  status: 'pending' | 'ready' | 'violations' | 'critical';
  filesChanged: number;
  violations: number;
  linesAdded: number;
  linesRemoved: number;
  violationDetails: Violation[];
  summary?: string;
  lastRun?: string | null;
}

export interface AppState {
  uploadedFiles: UploadedFile[];
  tasks: Task[];
  pullRequests: PullRequest[];
  currentPage: PageKey;
}

export interface GenerationState {
  isRunning: boolean;
  currentStep: string;
  processedFiles: number;
  totalFiles: number;
}

export interface GithubStat {
  label: string;
  value: string;
  trend?: string;
  color?: string;
}

export interface AgentRunSummary {
  totalChecks: number;
  violations: number;
  severity: PullRequest['status'];
  notes: string;
}

export interface AgentRunState {
  status: 'idle' | 'running' | 'passed' | 'warnings' | 'critical';
  progress: number;
  currentStep: string;
  startedAt?: string;
  completedAt?: string;
  summary?: AgentRunSummary;
}

export interface TaskSetMetadata {
  taskSetId: string;
  createdAt: string;
  taskCount: number;
  path: string;
}
