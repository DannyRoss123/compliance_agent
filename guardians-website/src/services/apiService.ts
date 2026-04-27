// All HTTP calls from the frontend to the backend live here.
// Components never call fetch directly — they import functions from this file.
// Falls back to mock data if VITE_GUARDIANS_API_URL is not configured.
import type { PullRequest, Task, TaskSetMetadata, Violation } from '../types';
import { MOCK_PRS } from './mockData';

const API_BASE_URL = import.meta.env.VITE_GUARDIANS_API_URL;
const API_TOKEN = import.meta.env.VITE_GUARDIANS_API_TOKEN;
const hasApi = Boolean(API_BASE_URL);

// Fetches all scanned PRs from the backend, falls back to mock data on error
export async function fetchPullRequests(): Promise<PullRequest[]> {
  if (!hasApi) {
    return new Promise((resolve) =>
      setTimeout(() => resolve(MOCK_PRS), 450),
    );
  }

  try {
    const response = await fetch(`${API_BASE_URL}/pull-requests`);
    if (!response.ok) {
      throw new Error('Failed to fetch pull requests');
    }
    const data = await response.json();
    const items = data.items ?? [];
    return items.map(normalizePullRequest);
  } catch (error) {
    console.warn('[apiService] Falling back to mock PRs:', error);
    return MOCK_PRS;
  }
}

export async function refreshPullRequest(
  prId: string,
): Promise<PullRequest | undefined> {
  const prs = await fetchPullRequests();
  return prs.find((pr) => pr.id === prId);
}

export function exportTasksAsJson(tasks: Task[]): string {
  const blob = new Blob([JSON.stringify(tasks, null, 2)], {
    type: 'application/json',
  });
  return URL.createObjectURL(blob);
}

// Syncs the extracted task set to the backend so the runner knows what rules to enforce
export async function saveTaskSet(tasks: Task[]): Promise<TaskSetMetadata | null> {
  if (!hasApi) {
    console.info('[apiService] No API base URL configured; skipping task sync.');
    return null;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/tasks`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
      },
      body: JSON.stringify({ tasks }),
    });

    if (!response.ok) {
      throw new Error(`Failed to save task set (${response.status})`);
    }

    const payload = await response.json();
    return normalizeTaskMetadata(payload);
  } catch (error) {
    console.warn('[apiService] Unable to persist task set:', error);
    return null;
  }
}

function normalizeTaskMetadata(payload: any): TaskSetMetadata {
  return {
    taskSetId: payload.task_set_id,
    createdAt: payload.created_at,
    taskCount: payload.task_count,
    path: payload.path,
  };
}

export async function fetchLatestTaskSet(): Promise<{ tasks: Task[]; metadata: TaskSetMetadata } | null> {
  if (!hasApi) return null;

  try {
    const response = await fetch(`${API_BASE_URL}/tasks/current`);
    if (!response.ok) {
      throw new Error(`Failed to fetch current tasks (${response.status})`);
    }
    const payload = await response.json();
    return {
      tasks: payload.tasks,
      metadata: normalizeTaskMetadata(payload.metadata),
    };
  } catch (error) {
    console.warn('[apiService] Unable to fetch latest task set:', error);
    return null;
  }
}

export async function rerunPullRequest(prId: string): Promise<void> {
  if (!hasApi) return;
  const encodedId = encodeURIComponent(prId);
  const response = await fetch(`${API_BASE_URL}/pull-requests/${encodedId}/rerun`, {
    method: 'POST',
    headers: {
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to rerun PR (${response.status})`);
  }
}

// Uploads policy documents to the backend, triggering RAG ingestion (chunking, embedding, task extraction)
export async function uploadDocumentsToBackend(files: File[]): Promise<void> {
  if (!hasApi || !files.length) return;
  const formData = new FormData();
  files.forEach((f) => formData.append('files', f));
  const response = await fetch(`${API_BASE_URL}/documents`, {
    method: 'POST',
    headers: {
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
    },
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Document upload failed (${response.status})`);
  }
}

export async function rerunAllPullRequests(): Promise<void> {
  if (!hasApi) return;
  const response = await fetch(`${API_BASE_URL}/pull-requests/rerun-all`, {
    method: 'POST',
    headers: {
      ...(API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {}),
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to rerun all PRs (${response.status})`);
  }
}

// Normalizes raw backend PR response into the PullRequest shape the frontend expects
function normalizePullRequest(item: any): PullRequest {
  const violationDetails = normalizeViolations(item.result ?? item.violationDetails);
  const repo = item.repository ?? 'unknown/unknown';
  return {
    id: item.id ?? `${repo}#PR-${item.number ?? 0}`,
    number: Number(item.number ?? 0),
    title: item.title ?? 'Untitled pull request',
    repository: repo,
    author: normalizeAuthor(item.author),
    status: normalizeStatus(item.status),
    filesChanged: item.files_changed ?? item.filesChanged ?? 0,
    violations: item.violations ?? violationDetails.length,
    linesAdded: item.lines_added ?? item.linesAdded ?? 0,
    linesRemoved: item.lines_removed ?? item.linesRemoved ?? 0,
    violationDetails,
    summary: item.summary ?? item.notes ?? (violationDetails.length ? `Detected ${violationDetails.length} violation(s).` : 'All policy checks passed.'),
    lastRun: item.last_run ?? item.lastRun ?? null,
  };
}

function normalizeAuthor(author: any): PullRequest['author'] {
  if (author && typeof author === 'object' && 'name' in author && 'avatar' in author) {
    return author as PullRequest['author'];
  }
  const name = typeof author === 'string' && author.length ? author : 'Guardians Agent';
  const seed = encodeURIComponent(name);
  return {
    name,
    avatar: `https://api.dicebear.com/7.x/initials/svg?seed=${seed}`,
  };
}

function normalizeStatus(status?: string): PullRequest['status'] {
  if (status === 'ready' || status === 'violations' || status === 'critical') {
    return status;
  }
  if (status === 'warnings') {
    return 'violations';
  }
  return 'pending';
}

// Maps raw violation objects from the backend into typed Violation objects, handling snake_case/camelCase variants
function normalizeViolations(entries?: any[]): Violation[] {
  if (!Array.isArray(entries)) return [];
  return entries
    .filter(Boolean)
    .map((entry) => ({
      severity: (entry.severity ?? 'warning') as Violation['severity'],
      rule: entry.taskId ?? entry.task_id ?? entry.rule ?? entry.name ?? 'Policy check',
      message: entry.message ?? 'Task violation detected.',
      file: entry.file ?? 'unknown',
      line: typeof entry.line === 'number' ? entry.line : 1,
      suggestedFix: entry.suggestedFix ?? entry.suggested_fix ?? entry.fix,
    }));
}
