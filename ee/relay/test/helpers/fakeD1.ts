// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5.
import { DatabaseSync } from "node:sqlite";

class FakeD1Statement {
  constructor(private readonly db: any, private readonly sql: string, private readonly params: unknown[] = []) {}
  bind(...params: unknown[]): FakeD1Statement {
    return new FakeD1Statement(this.db, this.sql, params);
  }
  async run(): Promise<{ success: true; meta: { changes: number } }> {
    this.db.prepare(this.sql).run(...this.params);
    const row = this.db.prepare("SELECT changes() AS changes").get() as { changes?: number } | undefined;
    return { success: true, meta: { changes: Number(row?.changes || 0) } };
  }
  async first<T>(): Promise<T | null> {
    return (this.db.prepare(this.sql).get(...this.params) || null) as T | null;
  }
  async all<T>(): Promise<{ results: T[]; success: true }> {
    return { results: this.db.prepare(this.sql).all(...this.params) as T[], success: true };
  }
}

export class FakeD1Database {
  readonly db = new DatabaseSync(":memory:");
  prepare(sql: string): FakeD1Statement {
    return new FakeD1Statement(this.db, sql);
  }
  close(): void {
    this.db.close();
  }
}
