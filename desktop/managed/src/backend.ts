import { type ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { closeSync, constants, fstatSync, fsyncSync, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Activation, BackendInventory } from "./inventory.js";
import type { Profile } from "./profile.js";
import { BROKER_CODE } from "./runtime-code.js";

export interface BackendEndpoint {
  readonly origin: string;
  readonly url: string;
  readonly instance: string;
  readonly version: string;
  readonly ui_sha256: string;
  readonly deployment: string;
  readonly generation: number;
}
export class BackendError extends Error {
  constructor() {
    super("The owned backend could not be verified. Open desktop recovery to repair this profile.");
  }
}
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

/** Materialize only sealed helper bytes under a private identity, refusing changed or linked existing helpers. */
function helper(profile: Readonly<Profile>): string {
  const folder = join(profile.directory, "helpers");
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  const directory = openSync(folder, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(directory);
    if (stat.uid !== process.getuid?.() || stat.mode & 0o077) throw new BackendError();
    const path = join(folder, `${hash(BROKER_CODE)}.py`);
    let descriptor: number;
    try {
      descriptor = openSync(
        path,
        constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW,
        0o600,
      );
      try {
        writeFileSync(descriptor, BROKER_CODE);
        fsyncSync(descriptor);
      } finally {
        closeSync(descriptor);
      }
      fsyncSync(directory);
    } catch (error) {
      if (!error || typeof error !== "object" || !("code" in error) || error.code !== "EEXIST") throw error;
    }
    descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const file = fstatSync(descriptor);
      if (
        !file.isFile() ||
        file.size !== Buffer.byteLength(BROKER_CODE) ||
        file.uid !== process.getuid?.() ||
        file.nlink !== 1 ||
        file.mode & 0o077 ||
        hash(readFileSync(descriptor)) !== hash(BROKER_CODE)
      )
        throw new BackendError();
    } finally {
      closeSync(descriptor);
    }
    return path;
  } finally {
    closeSync(directory);
  }
}

/** One controller lease starts only an inventoried deployment, never PATH or an already-listening HTTP process. */
export class OwnedBackend {
  readonly #profile: Readonly<Profile>;
  readonly #deployments: BackendInventory;
  readonly #port: number;
  #child: ChildProcessWithoutNullStreams | null = null;
  #endpoint: BackendEndpoint | null = null;
  #graceful = false;
  #stopping = false;
  #stopPromise: Promise<boolean> | null = null;
  #cancelStartup: (() => void) | null = null;
  constructor(profile: Readonly<Profile>, deployments: BackendInventory, port: number) {
    // Port zero asks the broker to choose and retain an available loopback port.
    if (!Number.isInteger(port) || port < 0 || port > 65535) throw new BackendError();
    this.#profile = profile;
    this.#deployments = deployments;
    this.#port = port;
  }
  get endpoint(): BackendEndpoint | null {
    return this.#endpoint;
  }

  /** Whether this controller still owns a child, independent of a ready endpoint or a remembered PID. */
  get running(): boolean {
    return this.#child !== null;
  }

  async start(expected?: Activation): Promise<BackendEndpoint> {
    if (this.#stopping) throw new BackendError();
    const activation = this.#deployments.active();
    if (
      !activation ||
      (expected &&
        (activation.generation !== expected.generation ||
          activation.deployment !== expected.deployment ||
          activation.previous !== expected.previous))
    )
      throw new BackendError();
    if (this.#child) {
      if (this.#endpoint) return this.#endpoint;
      throw new BackendError();
    }
    const deployment = this.#deployments.verify(activation.deployment);
    const workerHash = deployment.manifest.files["worker.py"];
    if (!workerHash) throw new BackendError();
    const nonce = randomUUID();
    const environment = Object.fromEntries(
      Object.entries(process.env).filter(([key]) =>
        ["PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"].includes(key),
      ),
    );
    const child = spawn(deployment.manifest.python, ["-I", "-S", "-B", helper(this.#profile)], {
      cwd: this.#profile.directory,
      env: environment,
      stdio: "pipe",
    });
    this.#graceful = false;
    this.#child = child;
    child.stdin.on("error", () => undefined); // The child error/exit path owns startup failure and cleanup.
    // Worker diagnostics are intentionally not forwarded into renderer errors or protocol logs.
    child.stderr.resume();
    child.stdin.write(
      `${JSON.stringify({ nonce, port: this.#port, worker: join(deployment.directory, "worker.py"), worker_sha256: workerHash, version: deployment.manifest.core_version, ui_sha256: deployment.manifest.ui_sha256, profile: this.#profile.directory, deployment: activation.deployment, generation: activation.generation })}\n`,
    );
    try {
      return await new Promise<BackendEndpoint>((resolve, reject) => {
        let pending = "";
        let settled = false;
        const finish = (error?: Error, endpoint?: BackendEndpoint) => {
          if (settled) return;
          settled = true;
          this.#cancelStartup = null;
          clearTimeout(timer);
          if (error || !endpoint) reject(error ?? new BackendError());
          else resolve(endpoint);
        };
        const timer = setTimeout(() => finish(new BackendError()), 30_000);
        this.#cancelStartup = () => finish(new BackendError());
        child.on("error", () => finish(new BackendError()));
        child.on("close", () => {
          if (this.#child === child) {
            this.#child = null;
            this.#endpoint = null;
          }
          finish(new BackendError());
        });
        child.stdout.on("data", (chunk: Buffer) => {
          pending += chunk.toString("utf8");
          if (pending.length > 64 * 1024) {
            finish(new BackendError());
            child.stdin.end();
            return;
          }
          for (;;) {
            const boundary = pending.indexOf("\n");
            if (boundary < 0) break;
            const line = pending.slice(0, boundary);
            pending = pending.slice(boundary + 1);
            try {
              const value: unknown = JSON.parse(line);
              if (!value || typeof value !== "object" || Array.isArray(value)) throw new BackendError();
              const data = value as Record<string, unknown>;
              if (data.event === "failed") {
                finish(new BackendError());
                return;
              }
              if (data.event === "stopped" && data.nonce === nonce) {
                this.#graceful = data.graceful === true;
                continue;
              }
              if (data.event !== "ready") continue;
              if (this.#stopping) continue;
              const url = typeof data.url === "string" ? new URL(data.url) : null;
              if (
                data.nonce !== nonce ||
                data.version !== deployment.manifest.core_version ||
                data.ui_sha256 !== deployment.manifest.ui_sha256 ||
                typeof data.instance !== "string" ||
                !/^[a-f0-9]{32}$/.test(data.instance) ||
                !url ||
                url.protocol !== "http:" ||
                url.hostname !== "127.0.0.1" ||
                (this.#port !== 0 && Number(url.port) !== this.#port) ||
                !url.port ||
                url.pathname !== "/" ||
                !/^#token=[A-Za-z0-9_-]{32,}$/.test(url.hash) ||
                url.search ||
                url.username ||
                url.password
              )
                throw new BackendError();
              this.#endpoint = Object.freeze({
                origin: url.origin,
                url: url.href,
                instance: data.instance,
                version: data.version as string,
                ui_sha256: data.ui_sha256 as string,
                deployment: deployment.id,
                generation: activation.generation,
              });
              finish(undefined, this.#endpoint);
            } catch {
              finish(new BackendError());
              child.stdin.end();
            }
          }
        });
      });
    } catch {
      await this.stop();
      throw new BackendError();
    }
  }

  async stop(options: { readonly requireGraceful?: boolean } = {}): Promise<void> {
    const child = this.#child;
    if (!child) {
      if (options.requireGraceful && !this.#graceful) throw new BackendError();
      return;
    }
    if (!this.#stopPromise) {
      this.#stopping = true;
      this.#endpoint = null;
      child.stdin.end();
      this.#stopPromise = new Promise<boolean>((resolve, reject) => {
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
          reject(new BackendError());
        }, 15_000);
        child.once("close", (code) => {
          clearTimeout(timer);
          resolve(this.#graceful && code === 0);
        });
      });
      this.#cancelStartup?.();
    }
    const stopping = this.#stopPromise;
    try {
      const graceful = await stopping;
      if (options.requireGraceful && !graceful) throw new BackendError();
    } finally {
      if (this.#stopPromise === stopping) {
        this.#stopPromise = null;
        this.#stopping = false;
      }
    }
  }
}
