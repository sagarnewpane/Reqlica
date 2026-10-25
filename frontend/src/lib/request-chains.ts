import { type RequestConfig, type RequestResult } from './request-execution';
export type ChainStep = RequestConfig & {
  name: string;
  expectedStatus: number;
};
export type ChainResult = {
  step: string;
  result?: RequestResult;
  error?: string;
  passed: boolean;
};
export function resolveChainStep(
  step: ChainStep,
  values: Record<string, unknown>,
): RequestConfig {
  function lookup(reference: string): unknown {
    const parts = reference.trim().split('.');
    let value: unknown = values;
    for (const part of parts) {
      if (
        ['__proto__', 'constructor', 'prototype'].includes(part) ||
        !value ||
        typeof value !== 'object' ||
        !Object.hasOwn(value, part)
      )
        throw new Error(`Missing chain value: ${reference}`);
      value = (value as Record<string, unknown>)[part];
    }
    return value;
  }
  function text(value: string, path = false): string {
    return value.replace(/\{\{([^{}]+)\}\}/g, (_, key: string) => {
      const found = lookup(key);
      if (found !== null && typeof found === 'object')
        throw new Error('Use a scalar value in paths and embedded strings.');
      const plain = String(found);
      return path ? encodeURIComponent(plain) : plain;
    });
  }
  function body(value: unknown): unknown {
    if (typeof value === 'string') {
      const match = value.match(/^\{\{([^{}]+)\}\}$/);
      return match ? lookup(match[1]) : text(value);
    }
    if (Array.isArray(value)) return value.map(body);
    if (value && typeof value === 'object')
      return Object.fromEntries(
        Object.entries(value).map(([key, value]) => [key, body(value)]),
      );
    return value;
  }
  return {
    method: step.method,
    path: text(step.path, true),
    body: step.body.trim() ? JSON.stringify(body(JSON.parse(step.body))) : '',
    headerText: text(step.headerText || ''),
    draftId: step.draftId,
  };
}
