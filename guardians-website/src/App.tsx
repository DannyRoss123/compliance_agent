import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Navigation } from './components/Navigation';
import { OnboardingPage } from './components/OnboardingPage';
import { TasksDashboard } from './components/TasksDashboard';
import { PRMonitor } from './components/PRMonitor';
import type {
  AppState,
  GenerationState,
  PageKey,
  PullRequest,
  Task,
  UploadedFile,
} from './types';
import { generateTasksFromDocuments } from './services/taskGenerator';
import {
  fetchLatestTaskSet,
  fetchPullRequests,
  rerunAllPullRequests,
  rerunPullRequest,
  saveTaskSet,
  uploadDocumentsToBackend,
} from './services/apiService';
import { MOCK_PRS, MOCK_TASKS } from './services/mockData';

const initialState: AppState = {
  uploadedFiles: [],
  tasks: MOCK_TASKS,
  pullRequests: [],
  currentPage: 'onboarding',
};

const generateId = () =>
  (typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2));

const ENABLE_DEMO_VIOLATIONS = import.meta.env.VITE_FAKE_VIOLATIONS !== 'false';
const DEMO_FILES = ['src/api/userService.ts', 'src/payments/processor.ts', 'src/auth/jwt.ts', 'src/utils/logger.ts'];

function applyDemoViolations(prs: PullRequest[], tasks: Task[]): PullRequest[] {
  if (!ENABLE_DEMO_VIOLATIONS) {
    return prs;
  }
  const sourceTasks = tasks.length ? tasks : MOCK_TASKS;
  if (!sourceTasks.length) {
    return prs;
  }
  return prs.map((pr, index) => {
    const existingDetails = pr.violationDetails ?? [];
    if (existingDetails.length || pr.status === 'ready') {
      return {
        ...pr,
        violationDetails: existingDetails,
        summary: pr.summary ?? (existingDetails.length ? `Detected ${existingDetails.length} violation(s).` : 'All policy checks passed.'),
      };
    }
    const sampleTasks = sourceTasks.slice(0, Math.min(2, sourceTasks.length));
    const details = sampleTasks.map((task, offset) => ({
      severity: task.severity,
      rule: task.title,
      message: `Policy "${task.title}" detected an issue in ${DEMO_FILES[(index + offset) % DEMO_FILES.length]}.`,
      file: DEMO_FILES[(index + offset) % DEMO_FILES.length],
      line: (offset + 1) * 12,
      suggestedFix: task.suggestedFix ?? 'Review the internal standards document.',
    }));
    return {
      ...pr,
      status: pr.status === 'pending' ? 'violations' : pr.status,
      violations: details.length,
      violationDetails: details,
      summary: `Detected ${details.length} policy violation(s).`,
    };
  });
}

function App() {
  const [state, setState] = useState<AppState>(initialState);
  const [fileLibrary, setFileLibrary] = useState<Record<string, File>>({});
  const [generationState, setGenerationState] = useState<GenerationState>({
    isRunning: false,
    currentStep: 'Idle',
    processedFiles: 0,
    totalFiles: 0,
  });
  const [showSuccess, setShowSuccess] = useState(false);
  const [isLoadingPRs, setIsLoadingPRs] = useState(false);
  const uploadTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const loadPullRequests = useCallback(async () => {
    setIsLoadingPRs(true);
    try {
      const prs = await fetchPullRequests();
      setState((prev) => {
        const withDemo = ENABLE_DEMO_VIOLATIONS
          ? applyDemoViolations(prs, prev.tasks.length ? prev.tasks : MOCK_TASKS)
          : prs;
        return {
          ...prev,
          pullRequests: withDemo,
        };
      });
    } catch (error) {
      console.warn('Failed to fetch PRs, using mock data.', error);
      setState((prev) => ({
        ...prev,
        pullRequests: ENABLE_DEMO_VIOLATIONS
          ? applyDemoViolations(MOCK_PRS, prev.tasks.length ? prev.tasks : MOCK_TASKS)
          : MOCK_PRS,
      }));
    } finally {
      setIsLoadingPRs(false);
    }
  }, []);

  useEffect(() => {
    void hydrateTasks();
    void loadPullRequests();
  }, [loadPullRequests]);

  useEffect(
    () => () => {
      Object.values(uploadTimers.current).forEach((timer) => clearInterval(timer));
    },
    [],
  );

  const simulateUpload = (fileId: string) => {
    const timer = setInterval(() => {
      let shouldStop = false;
      setState((prev) => {
        const uploadedFiles = prev.uploadedFiles.map((file) => {
          if (file.id !== fileId) return file;
          const nextProgress = Math.min((file.progress ?? 0) + 15, 100);
          if (nextProgress >= 100) {
            shouldStop = true;
          }
          return {
            ...file,
            progress: nextProgress,
            status: nextProgress >= 100 ? 'complete' : file.status,
          };
        });
        return { ...prev, uploadedFiles };
      });
      if (shouldStop) {
        clearInterval(timer);
        delete uploadTimers.current[fileId];
      }
    }, 300);

    uploadTimers.current[fileId] = timer;
  };

  const addFiles = (files: File[]) => {
    if (!files.length) return;
    setShowSuccess(false);
    const payload: UploadedFile[] = files.map((file) => {
      const id = generateId();
      return {
        id,
        name: file.name,
        type: file.type || 'text/plain',
        size: file.size,
        status: 'uploading',
        progress: 5,
      };
    });

    setState((prev) => ({
      ...prev,
      uploadedFiles: [...prev.uploadedFiles, ...payload],
    }));
    setFileLibrary((prev) => {
      const next = { ...prev };
      payload.forEach((meta, index) => {
        next[meta.id] = files[index];
        simulateUpload(meta.id);
      });
      return next;
    });
  };

  const removeFile = (fileId: string) => {
    clearInterval(uploadTimers.current[fileId]);
    delete uploadTimers.current[fileId];
    setState((prev) => ({
      ...prev,
      uploadedFiles: prev.uploadedFiles.filter((file) => file.id !== fileId),
    }));
    setFileLibrary((prev) => {
      const next = { ...prev };
      delete next[fileId];
      return next;
    });
  };

  const clearFiles = () => {
    Object.keys(uploadTimers.current).forEach((key) => clearInterval(uploadTimers.current[key]));
    uploadTimers.current = {};
    setState((prev) => ({ ...prev, uploadedFiles: [] }));
    setFileLibrary({});
    setShowSuccess(false);
  };

  const clearTasks = () => {
    setState((prev) => ({
      ...prev,
      tasks: [],
      currentPage: 'onboarding',
    }));
    setShowSuccess(false);
  };

  const navigate = (page: PageKey) => {
    setState((prev) => ({ ...prev, currentPage: page }));
  };

  const syncTasksToBackend = async (tasks: Task[]) => {
    await saveTaskSet(tasks);
  };

  const hydrateTasks = async () => {
    const latest = await fetchLatestTaskSet();
    if (latest) {
      setState((prev) => ({
        ...prev,
        tasks: latest.tasks,
      }));
    }
  };

  const handleGenerateTasks = async () => {
    if (generationState.isRunning) return;
    const files = state.uploadedFiles
      .map((file) => fileLibrary[file.id])
      .filter((file): file is File => Boolean(file));
    if (!files.length) return;

    setGenerationState({
      isRunning: true,
      currentStep: 'Analyzing documents...',
      processedFiles: 0,
      totalFiles: files.length,
    });

    files.forEach((_, index) => {
      setTimeout(() => {
        setGenerationState((prev) => ({
          ...prev,
          processedFiles: Math.min(index + 1, files.length),
        }));
      }, (index + 1) * 500);
    });

    const tasks = await generateTasksFromDocuments(files);
    setState((prev) => ({
      ...prev,
      tasks,
      currentPage: 'tasks',
    }));
    setGenerationState({
      isRunning: false,
      currentStep: 'Complete',
      processedFiles: files.length,
      totalFiles: files.length,
    });
    setShowSuccess(true);

    void syncTasksToBackend(tasks);
    // Also upload raw docs to backend so RAG index is populated for validation
    const rawFiles = state.uploadedFiles
      .map((f) => fileLibrary[f.id])
      .filter((f): f is File => Boolean(f));
    void uploadDocumentsToBackend(rawFiles);
  };

  const handleRefreshTasks = () => {
    setState((prev) => ({
      ...prev,
      tasks: [...prev.tasks],
    }));
  };

  const handleStartMonitoring = useCallback(() => {
    navigate('monitor');
    void loadPullRequests();
  }, [loadPullRequests]);

  const handleRefreshPRs = useCallback(async () => {
    await loadPullRequests();
  }, [loadPullRequests]);

  const handleRerunAll = useCallback(async () => {
    try {
      setIsLoadingPRs(true);
      await rerunAllPullRequests();
    } catch (error) {
      console.error('Failed to rerun all PRs', error);
    } finally {
      await loadPullRequests();
    }
  }, [loadPullRequests]);

  const handleRerunSingle = useCallback(
    async (prId: string) => {
      try {
        setIsLoadingPRs(true);
        await rerunPullRequest(prId);
      } catch (error) {
        console.error(`Failed to rerun ${prId}`, error);
      } finally {
        await loadPullRequests();
      }
    },
    [loadPullRequests],
  );


  const currentPage = useMemo(() => {
    switch (state.currentPage) {
      case 'tasks':
        return (
          <TasksDashboard
            tasks={state.tasks}
            onBack={() => navigate('onboarding')}
            onRefresh={handleRefreshTasks}
            onClearTasks={clearTasks}
            onStartMonitoring={handleStartMonitoring}
          />
        );
      case 'monitor':
        return (
          <PRMonitor
            pullRequests={state.pullRequests}
            isLoading={isLoadingPRs}
            onRunAll={handleRerunAll}
            onRunSingle={handleRerunSingle}
            onBack={() => navigate('tasks')}
            onRefresh={handleRefreshPRs}
          />
        );
      default:
        return (
          <OnboardingPage
            files={state.uploadedFiles}
            onFilesAdded={addFiles}
            onRemoveFile={removeFile}
            onClearFiles={clearFiles}
            onGenerateTasks={handleGenerateTasks}
            onViewTasks={() => navigate('tasks')}
            isGenerating={generationState.isRunning}
            generationState={generationState}
            showSuccess={showSuccess}
          />
        );
    }
  }, [
    state,
    generationState,
    showSuccess,
    isLoadingPRs,
    handleRerunAll,
    handleRerunSingle,
    handleRefreshPRs,
    handleStartMonitoring,
  ]);

  return (
    <div className="min-h-screen bg-base font-inter text-text">
      <Navigation currentPage={state.currentPage} onNavigate={navigate} />
      <main className="bg-base">{currentPage}</main>
    </div>
  );
}

export default App;
