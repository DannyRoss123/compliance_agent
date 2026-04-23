import { useMemo, useState } from 'react';
import { ChevronRight, Code2, FileCode, GitPullRequest, Loader2, RefreshCw, Shield } from 'lucide-react';
import type { PullRequest, Violation } from '../types';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';

interface PRMonitorProps {
  pullRequests: PullRequest[];
  isLoading: boolean;
  onRunAll: () => void | Promise<void>;
  onRunSingle: (prId: string) => void | Promise<void>;
  onBack: () => void;
  onRefresh: () => void | Promise<void>;
}

const statusBadge = (status: PullRequest['status']) => {
  switch (status) {
    case 'critical':
      return <Badge variant="critical">Critical</Badge>;
    case 'violations':
      return <Badge variant="warning">Warnings</Badge>;
    case 'ready':
      return <Badge variant="outline">Passed</Badge>;
    default:
      return <Badge variant="outline">Pending</Badge>;
  }
};

const statusAccent = (status: PullRequest['status']) => {
  switch (status) {
    case 'critical':
      return 'border-l-4 border-l-critical';
    case 'violations':
      return 'border-l-4 border-l-warning';
    case 'ready':
      return 'border-l-4 border-l-success';
    default:
      return 'border-l-4 border-l-border';
  }
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return 'Not run yet';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

function groupViolationsByFile(violations: Violation[]): Record<string, Violation[]> {
  return violations.reduce<Record<string, Violation[]>>((acc, v) => {
    (acc[v.file] ??= []).push(v);
    return acc;
  }, {});
}

function CodeTabViewer({ violations }: { violations: Violation[] }) {
  const byFile = useMemo(() => groupViolationsByFile(violations), [violations]);
  const files = Object.keys(byFile);
  const [activeFile, setActiveFile] = useState(files[0] ?? '');

  if (!files.length) return null;

  const activeViolations = byFile[activeFile] ?? [];
  const snippet = activeViolations.find((v) => v.codeSnippet)?.codeSnippet;

  return (
    <article className="rounded-3xl border border-border/70 bg-panel overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border/60 px-4 pt-4 pb-0">
        <Code2 className="h-4 w-4 text-textMuted" />
        <span className="text-xs font-semibold uppercase tracking-[0.3em] text-textMuted">Code review</span>
      </div>
      {/* File tabs */}
      <div className="flex gap-1 overflow-x-auto border-b border-border/60 bg-panelMuted/30 px-3 pt-2">
        {files.map((file) => (
          <button
            key={file}
            type="button"
            onClick={() => setActiveFile(file)}
            className={`flex shrink-0 items-center gap-1.5 rounded-t-lg border border-b-0 px-3 py-1.5 text-xs transition ${
              activeFile === file
                ? 'border-border/70 bg-panel text-text'
                : 'border-transparent text-textMuted hover:text-text'
            }`}
          >
            <FileCode className="h-3 w-3" />
            <span className="max-w-[160px] truncate">{file.split('/').pop()}</span>
            <span className={`ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
              byFile[file].some(v => v.severity === 'critical')
                ? 'bg-critical/20 text-critical'
                : 'bg-warning/20 text-warning'
            }`}>
              {byFile[file].length}
            </span>
          </button>
        ))}
      </div>
      {/* Code snippet */}
      {snippet ? (
        <pre className="overflow-x-auto bg-[#0d1117] p-4 text-xs leading-relaxed text-[#c9d1d9] font-mono">
          {snippet.split('\n').map((line, i) => {
            const isViolation = activeViolations.some((v) => {
              const lineNum = parseInt(line.trim().split(' ')[0], 10);
              return !isNaN(lineNum) && lineNum === v.line;
            });
            return (
              <div
                key={i}
                className={isViolation ? 'rounded bg-[#ff000020] px-1 -mx-1' : ''}
              >
                {line}
              </div>
            );
          })}
        </pre>
      ) : (
        <div className="px-4 py-6 text-center text-xs text-textMuted">
          <p className="font-semibold">{activeFile}</p>
          <p className="mt-1 text-textMuted/60">Code preview available after first scan with policy documents uploaded.</p>
        </div>
      )}
      {/* Violations for active file */}
      <ul className="divide-y divide-border/40 px-4 pb-4">
        {activeViolations.map((v, i) => (
          <li key={i} className="py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-text">{v.rule}</p>
                <p className="text-xs text-textMuted">line {v.line}</p>
                <p className="mt-1 text-sm text-text/80">{v.message}</p>
                {v.suggestedFix && (
                  <p className="mt-1 text-xs text-textMuted">
                    <span className="font-semibold text-text">Fix: </span>{v.suggestedFix}
                  </p>
                )}
              </div>
              <Badge variant={v.severity === 'critical' ? 'critical' : v.severity === 'warning' ? 'warning' : 'outline'} className="shrink-0">
                {v.severity}
              </Badge>
            </div>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function PRMonitor({
  pullRequests,
  isLoading,
  onRunAll,
  onRunSingle,
  onBack,
  onRefresh,
}: PRMonitorProps) {
  const [active, setActive] = useState<'all' | string>('all');

  const aggregate = useMemo(() => {
    return pullRequests.reduce(
      (acc, pr) => {
        acc.total += 1;
        if (pr.status === 'pending') acc.pending += 1;
        if (pr.status === 'ready') acc.passed += 1;
        if (pr.status === 'violations') acc.warnings += 1;
        if (pr.status === 'critical') acc.critical += 1;
        return acc;
      },
      { total: 0, pending: 0, passed: 0, warnings: 0, critical: 0 },
    );
  }, [pullRequests]);

  const selectedPR = active === 'all' ? null : pullRequests.find((pr) => pr.id === active) ?? null;

  const renderEmpty = () => (
    <div className="mt-10 rounded-3xl border border-dashed border-border/60 bg-panel/40 px-6 py-10 text-center text-textMuted">
      <p className="text-lg font-semibold text-text">No pull requests to monitor yet</p>
      <p className="mt-2 text-sm">
        Once a webhook fires or you re-run a scan, the PR will appear here with its live policy status.
      </p>
    </div>
  );

  const renderOverviewCards = () => (
    <div className="mt-6 grid gap-4 md:grid-cols-2">
      {pullRequests.map((pr) => (
        <article
          key={pr.id}
          className={`rounded-3xl border border-border/70 bg-panel px-6 py-5 text-left transition hover:-translate-y-[2px] hover:bg-panelMuted/40 ${statusAccent(pr.status)}`}
        >
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.35em] text-textMuted">#{pr.number}</p>
              <p className="text-lg font-semibold text-text">{pr.title}</p>
              <p className="text-sm text-textMuted">{pr.repository}</p>
            </div>
            {statusBadge(pr.status)}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3 text-sm text-textMuted">
            <div>
              <p className="text-xs uppercase tracking-[0.2em]">Files</p>
              <p className="text-text font-semibold">{pr.filesChanged}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-[0.2em]">Violations</p>
              <p className="text-text font-semibold">{pr.violationDetails.length}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-[0.2em]">Last run</p>
              <p className="text-text font-semibold">{formatTimestamp(pr.lastRun)}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={() => void onRunSingle(pr.id)} disabled={isLoading}>
              <Shield className="mr-2 h-4 w-4" />
              Re-run policy check
            </Button>
            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs font-semibold text-textMuted hover:text-text"
              onClick={() => setActive(pr.id)}
            >
              View details
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </article>
      ))}
    </div>
  );

  const renderDetail = () => {
    if (!selectedPR) return renderOverviewCards();

    return (
      <div className="mt-6 space-y-6">
        <article className={`rounded-3xl border border-border/70 bg-panel p-6 ${statusAccent(selectedPR.status)}`}>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.35em] text-textMuted">#{selectedPR.number}</p>
              <h2 className="text-2xl font-semibold text-text">{selectedPR.title}</h2>
              <p className="text-sm text-textMuted">{selectedPR.repository}</p>
            </div>
            {statusBadge(selectedPR.status)}
          </div>
          <p className="mt-4 text-sm text-textMuted">
            {selectedPR.summary ?? 'Awaiting first scan for this pull request.'}
          </p>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 text-sm text-textMuted">
            <div>
              <dt className="uppercase tracking-[0.25em] text-xs">Last run</dt>
              <dd className="text-text font-semibold">{formatTimestamp(selectedPR.lastRun)}</dd>
            </div>
            <div>
              <dt className="uppercase tracking-[0.25em] text-xs">Files changed</dt>
              <dd className="text-text font-semibold">{selectedPR.filesChanged}</dd>
            </div>
          </dl>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button onClick={() => void onRunSingle(selectedPR.id)} disabled={isLoading}>
              <Shield className="mr-2 h-4 w-4" />
              Re-run check
            </Button>
            <Button variant="outline" onClick={() => setActive('all')}>
              Back to overview
            </Button>
          </div>
        </article>

        {selectedPR.violationDetails.length > 0 ? (
          <CodeTabViewer violations={selectedPR.violationDetails} />
        ) : (
          <article className="rounded-3xl border border-border/70 bg-panel p-6">
            <div className="rounded-2xl border border-dashed border-border/60 bg-panelMuted/20 px-4 py-8 text-center text-sm text-textMuted">
              No violations detected on the latest run.
            </div>
          </article>
        )}
      </div>
    );
  };

  return (
    <section className="mx-auto mt-10 flex max-w-6xl gap-6 px-6 pb-16 text-text">
      <aside className="w-72 border-r border-border/60 pr-4">
        <p className="text-xs font-semibold uppercase tracking-[0.35em] text-textMuted">PR Targets</p>
        <p className="mt-1 text-sm text-textMuted">{pullRequests.length} tracked</p>

        <button
          type="button"
          className={`mt-4 w-full rounded-2xl border border-border/60 px-4 py-3 text-left text-sm font-semibold transition ${
            active === 'all' ? 'bg-panel text-text' : 'text-textMuted hover:bg-panelMuted/40'
          }`}
          onClick={() => setActive('all')}
        >
          <div className="flex items-center gap-2">
            <GitPullRequest className="h-4 w-4" />
            <span>All pull requests</span>
          </div>
        </button>

        <div className="mt-6 space-y-2">
          {pullRequests.map((pr) => (
            <button
              key={pr.id}
              type="button"
              className={`w-full rounded-2xl border border-border/60 px-4 py-3 text-left text-sm transition ${
                active === pr.id ? 'bg-panel text-text' : 'text-textMuted hover:bg-panelMuted/40'
              }`}
              onClick={() => setActive(pr.id)}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-semibold text-text">{pr.title}</p>
                  <p className="text-xs text-textMuted">
                    {pr.repository} · #{pr.number}
                  </p>
                </div>
                {statusBadge(pr.status)}
              </div>
            </button>
          ))}
          {!pullRequests.length && <p className="text-xs text-textMuted">No pull requests yet.</p>}
        </div>
      </aside>

      <div className="flex-1">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <button
              type="button"
              className="inline-flex items-center gap-2 text-sm font-semibold text-text transition hover:text-textMuted"
              onClick={onBack}
            >
              ← Back to tasks
            </button>
            <p className="mt-2 text-xs uppercase tracking-[0.35em] text-textMuted">Guardians Monitor</p>
            <h1 className="text-3xl font-semibold text-text">Pull request QA status</h1>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void onRefresh()} disabled={isLoading}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh data
            </Button>
            <Button className="gap-2" onClick={() => void onRunAll()} disabled={isLoading || !pullRequests.length}>
              {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
              Run all checks
            </Button>
          </div>
        </header>

        <div className="mt-6 grid gap-4 md:grid-cols-4">
          {[
            { label: 'Tracked', value: aggregate.total },
            { label: 'Pending', value: aggregate.pending },
            { label: 'Passed', value: aggregate.passed },
            { label: 'Critical / Warn', value: aggregate.critical + aggregate.warnings },
          ].map((stat) => (
            <div key={stat.label} className="rounded-3xl border border-border/60 bg-panel px-4 py-3 text-sm text-textMuted">
              <p className="text-xs uppercase tracking-[0.35em]">{stat.label}</p>
              <p className="mt-1 text-2xl font-semibold text-text">{stat.value}</p>
            </div>
          ))}
        </div>

        {isLoading && pullRequests.length === 0 ? (
          <div className="mt-10 flex items-center gap-3 text-textMuted">
            <Loader2 className="h-5 w-5 animate-spin" />
            Loading pull requests...
          </div>
        ) : pullRequests.length === 0 ? (
          renderEmpty()
        ) : active === 'all' ? (
          renderOverviewCards()
        ) : (
          renderDetail()
        )}
      </div>
    </section>
  );
}
