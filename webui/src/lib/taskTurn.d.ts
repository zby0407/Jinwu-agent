export function continuedTaskTurn<T extends { configurable?: Record<string, unknown> }>(config: T, started: unknown): T & { configurable: Record<string, unknown> };
