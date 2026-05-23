import {
  REDIS_ATTACH_LEASE_SCRIPT,
  REDIS_RELEASE_LEASE_SCRIPT,
  REDIS_RENEW_LEASE_SCRIPT,
  type RedisLikeClient,
} from "../../src/adapters/standalone/redisCoordinator.js";

type Entry = { value: string; expiresAt?: number };
type Listener = (message: string, channel: string) => void;

export class FakeRedis implements RedisLikeClient {
  private readonly values = new Map<string, Entry>();
  private readonly sets = new Map<string, Set<string>>();
  private readonly listeners = new Map<string, Set<Listener>>();
  now = 0;

  advance(ms: number): void {
    this.now += ms;
    this.purgeExpired();
  }

  flushAll(): void {
    this.values.clear();
    this.sets.clear();
  }

  async get(key: string): Promise<string | null> {
    this.purgeKey(key);
    return this.values.get(key)?.value ?? null;
  }

  async set(key: string, value: string, options: { PX?: number } = {}): Promise<string> {
    this.values.set(key, {
      value,
      expiresAt: options.PX ? this.now + options.PX : undefined,
    });
    return "OK";
  }

  async del(key: string | string[]): Promise<number> {
    const keys = Array.isArray(key) ? key : [key];
    let count = 0;
    for (const item of keys) {
      if (this.values.delete(item)) count += 1;
      if (this.sets.delete(item)) count += 1;
    }
    return count;
  }

  async eval(script: string, options: { keys: string[]; arguments: string[] }): Promise<unknown> {
    this.purgeExpired();
    if (script === REDIS_ATTACH_LEASE_SCRIPT) return this.evalAttach(options.keys, options.arguments);
    if (script === REDIS_RENEW_LEASE_SCRIPT) return this.evalRenew(options.keys, options.arguments);
    if (script === REDIS_RELEASE_LEASE_SCRIPT) return this.evalRelease(options.keys, options.arguments);
    throw new Error("unknown fake redis script");
  }

  async publish(channel: string, message: string): Promise<number> {
    const listeners = Array.from(this.listeners.get(channel) || []);
    for (const listener of listeners) listener(message, channel);
    return listeners.length;
  }

  async subscribe(channel: string, listener: Listener): Promise<void> {
    const listeners = this.listeners.get(channel) || new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(channel, listeners);
  }

  async unsubscribe(channel: string, listener?: Listener): Promise<void> {
    if (!listener) {
      this.listeners.delete(channel);
      return;
    }
    const listeners = this.listeners.get(channel);
    if (!listeners) return;
    listeners.delete(listener);
    if (listeners.size === 0) this.listeners.delete(channel);
  }

  async sAdd(key: string, member: string): Promise<number> {
    const set = this.sets.get(key) || new Set<string>();
    const before = set.size;
    set.add(member);
    this.sets.set(key, set);
    return set.size === before ? 0 : 1;
  }

  async sRem(key: string, member: string): Promise<number> {
    const set = this.sets.get(key);
    if (!set) return 0;
    const removed = set.delete(member) ? 1 : 0;
    if (set.size === 0) this.sets.delete(key);
    return removed;
  }

  async sMembers(key: string): Promise<string[]> {
    return Array.from(this.sets.get(key) || []);
  }

  async pExpire(key: string, ttlMs: number): Promise<boolean> {
    this.purgeKey(key);
    const entry = this.values.get(key);
    if (!entry) return false;
    entry.expiresAt = this.now + ttlMs;
    return true;
  }

  async pTTL(key: string): Promise<number> {
    this.purgeKey(key);
    const entry = this.values.get(key);
    if (!entry) return -2;
    if (entry.expiresAt === undefined) return -1;
    return Math.max(0, entry.expiresAt - this.now);
  }

  private evalAttach(keys: string[], args: string[]): unknown[] {
    const [leaseKey, epochKey] = keys;
    const ttlMs = Number(args[0] || 0);
    const minEpoch = Number(args[1] || 0);
    const currentEpoch = Math.max(Number(this.values.get(epochKey)?.value || 0), minEpoch);
    this.values.set(epochKey, { value: String(currentEpoch) });
    const currentLease = this.values.get(leaseKey)?.value;
    if (currentLease) return [0, currentEpoch, currentLease];
    const epoch = currentEpoch + 1;
    this.values.set(epochKey, { value: String(epoch) });
    const payload = JSON.stringify({
      daemon_id: args[2],
      connection_id: args[3],
      process_id: args[4],
      owner_user_id: args[5],
      credential_id: args[6],
      lease_id: args[7],
      issued_at_ms: Number(args[8]),
      expires_at_ms: Number(args[9]),
      epoch,
    });
    this.values.set(leaseKey, { value: payload, expiresAt: this.now + ttlMs });
    return [1, epoch, payload];
  }

  private evalRenew(keys: string[], args: string[]): unknown[] {
    const [leaseKey] = keys;
    this.purgeKey(leaseKey);
    const entry = this.values.get(leaseKey);
    if (!entry) return [0, ""];
    const lease = JSON.parse(entry.value) as Record<string, unknown>;
    if (
      lease.lease_id !== args[0] ||
      lease.process_id !== args[1] ||
      lease.connection_id !== args[2] ||
      lease.owner_user_id !== args[3] ||
      lease.credential_id !== args[4] ||
      Number(lease.epoch) !== Number(args[5])
    ) return [0, entry.value];
    lease.expires_at_ms = Number(args[7]);
    const payload = JSON.stringify(lease);
    this.values.set(leaseKey, { value: payload, expiresAt: this.now + Number(args[6]) });
    return [1, payload];
  }

  private evalRelease(keys: string[], args: string[]): unknown[] {
    const [leaseKey] = keys;
    this.purgeKey(leaseKey);
    const entry = this.values.get(leaseKey);
    if (!entry) return [0, ""];
    const lease = JSON.parse(entry.value) as Record<string, unknown>;
    if (
      lease.lease_id !== args[0] ||
      lease.process_id !== args[1] ||
      lease.connection_id !== args[2] ||
      lease.owner_user_id !== args[3] ||
      lease.credential_id !== args[4] ||
      Number(lease.epoch) !== Number(args[5])
    ) return [0, entry.value];
    this.values.delete(leaseKey);
    return [1, entry.value];
  }

  private purgeExpired(): void {
    for (const key of Array.from(this.values.keys())) this.purgeKey(key);
  }

  private purgeKey(key: string): void {
    const entry = this.values.get(key);
    if (entry?.expiresAt !== undefined && entry.expiresAt <= this.now) {
      this.values.delete(key);
    }
  }
}
