import type { PullRequest, Task } from '../types';

export const MOCK_TASKS: Task[] = [
  {
    id: 'task_001',
    title: 'Enforce structured error handling',
    description:
      'All critical code paths must capture exceptions and log contextual metadata before rethrowing.',
    category: 'Code Quality',
    severity: 'critical',
    checkType: 'Error Handling',
    fileTypes: ['*.ts', '*.tsx', '*.js'],
    exampleViolation: 'Catching errors without logging or rethrowing.',
    suggestedFix: 'Wrap handlers with logger.error including request/user ids.',
    docReference: 'error-handbook.md#structured-handling',
  },
  {
    id: 'task_002',
    title: 'Disallow plaintext secrets',
    description:
      'Ensure no API keys, tokens, or secrets are committed in configuration files.',
    category: 'Security',
    severity: 'critical',
    checkType: 'Secrets Scan',
    fileTypes: ['*.env', '*.yml', '*.json'],
    exampleViolation: 'AWS_SECRET=abcd1234 in .env file.',
    suggestedFix: 'Move secrets to Vault and reference via env placeholders.',
    docReference: 'security-controls.pdf#page=4',
  },
  {
    id: 'task_003',
    title: 'Optimize bundle size for UI builds',
    description:
      'Client bundles must stay under 250kb gzip and leverage dynamic imports.',
    category: 'Performance',
    severity: 'warning',
    checkType: 'Bundle Budget',
    fileTypes: ['*.tsx', '*.ts'],
    exampleViolation: 'Importing entire lodash library synchronously.',
    suggestedFix: 'Use lodash-es per-function imports with dynamic loading.',
    docReference: 'frontend-guidelines.md#performance',
  },
  {
    id: 'task_004',
    title: 'Validate API response schemas',
    description:
      'All outbound responses must conform to OpenAPI schema definitions.',
    category: 'Documentation',
    severity: 'info',
    checkType: 'Schema Validation',
    fileTypes: ['*.ts', '*.py'],
    exampleViolation: 'Missing optional field default in user profile response.',
    suggestedFix: 'Use zod schema to validate before returning payload.',
    docReference: 'api-standards.md#responses',
  },
  {
    id: 'task_005',
    title: 'Mandate secure headers',
    description:
      'Every service must inject CSP, HSTS, and frameguard headers.',
    category: 'Security',
    severity: 'warning',
    checkType: 'Header Enforcement',
    fileTypes: ['*.ts', '*.js'],
    exampleViolation: 'Express server missing helmet configuration.',
    suggestedFix: 'Add helmet middleware with enterprise CSP template.',
    docReference: 'security-controls.pdf#page=12',
  },
];

export const MOCK_PRS: PullRequest[] = [
  {
    id: 'pr-001',
    number: 128,
    title: 'Refactor logging pipeline',
    repository: 'acme/observability-service',
    author: {
      name: 'Ava Martinez',
      avatar: 'https://i.pravatar.cc/150?img=1',
    },
    status: 'violations',
    filesChanged: 12,
    violations: 3,
    linesAdded: 542,
    linesRemoved: 132,
    summary: 'Detected logging violations that require remediation.',
    lastRun: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
    violationDetails: [
      {
        severity: 'critical',
        rule: 'No PII in Logs',
        message: 'User email is logged in plaintext.',
        file: 'src/log/serializer.ts',
        line: 88,
        suggestedFix:
          'Mask user identifiers before logging or remove field entirely.',
        codeSnippet: `85  export function serializeEvent(event: AuditEvent): LogPayload {
86    return {
87      timestamp: event.timestamp,
88      user_email: event.user.email,   // ← PII logged in plaintext
89      action: event.action,
90      resource_id: event.resourceId,
91    };
92  }`,
      },
      {
        severity: 'warning',
        rule: 'Structured Logging',
        message: 'Log context missing requestId property.',
        file: 'src/log/logger.ts',
        line: 54,
        codeSnippet: `51  function logRequest(req: Request, level: LogLevel, msg: string) {
52    logger[level]({
53      service: 'observability',
54      message: msg,               // ← requestId absent from context
55      userId: req.user?.id,
56    });
57  }`,
      },
    ],
  },
  {
    id: 'pr-002',
    number: 87,
    title: 'Add authentication middleware',
    repository: 'acme/web-platform',
    author: {
      name: 'Liam Chen',
      avatar: 'https://i.pravatar.cc/150?img=2',
    },
    status: 'ready',
    filesChanged: 3,
    violations: 0,
    linesAdded: 245,
    linesRemoved: 12,
    summary: 'All policy checks passed on last run.',
    lastRun: new Date(Date.now() - 1000 * 60 * 45).toISOString(),
    violationDetails: [],
  },
  {
    id: 'pr-003',
    number: 206,
    title: 'Payment service retries',
    repository: 'acme/payments',
    author: {
      name: 'Priya Kapoor',
      avatar: 'https://i.pravatar.cc/150?img=3',
    },
    status: 'critical',
    filesChanged: 18,
    violations: 6,
    linesAdded: 812,
    linesRemoved: 96,
    summary: 'Critical payment retries issues detected.',
    lastRun: new Date(Date.now() - 1000 * 60 * 5).toISOString(),
    violationDetails: [
      {
        severity: 'critical',
        rule: 'Idempotent Writes',
        message: 'Retry handler lacks transaction idempotency check.',
        file: 'src/retry/workflow.ts',
        line: 132,
        suggestedFix: 'Persist retry tokens and short-circuit duplicates.',
        codeSnippet: `129  async function retryCharge(chargeId: string, amount: number) {
130    const charge = await stripe.charges.retrieve(chargeId);
131    if (charge.status === 'failed') {
132      return await stripe.charges.create({ amount, currency: 'usd' });
133      // ← No idempotency key — duplicate charges possible on network retry
134    }
135  }`,
      },
      {
        severity: 'warning',
        rule: 'Telemetry Coverage',
        message: 'New retry path missing metrics emission.',
        file: 'src/retry/metrics.ts',
        line: 45,
        codeSnippet: `42  export async function handleRetryEvent(event: RetryEvent) {
43    const result = await processRetry(event);
44    if (result.success) {
45      // ← No metrics.increment('retry.success') call
46      return result;
47    }
48    metrics.increment('retry.failure', { reason: result.error });
49  }`,
      },
    ],
  },
];
