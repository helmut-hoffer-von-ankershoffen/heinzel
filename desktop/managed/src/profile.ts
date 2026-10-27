import { randomUUID } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  fsyncSync,
  linkSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

export type Channel = "stable" | "preview" | "development";
export interface ProfileChoice {
  name: string;
  channel: Channel;
}
export interface LaunchChoice extends ProfileChoice {
  newWindow: boolean;
}
export interface Profile extends ProfileChoice {
  id: string;
  directory: string;
}
interface Identity extends ProfileChoice {
  version: 1;
  id: string;
  owner: number;
}

const NAME = /^[a-z][a-z0-9-]{0,31}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const CHANNELS = new Set<string>(["stable", "preview", "development"]);
const KEYS = ["channel", "id", "name", "owner", "version"];

export class ProfileError extends Error {
  constructor() {
    super("This desktop profile is unavailable or invalid; use another profile or repair its private state.");
  }
}

/** Parse only declared profile/window choices; arbitrary URLs and path overrides are not a desktop entry point. */
export function parseLaunch(arguments_: readonly string[]): LaunchChoice {
  let name = "default";
  let channel: Channel = "stable";
  let newWindow = false;
  const seen = new Set<string>();
  for (const value of arguments_) {
    const parts = value.split("=");
    if (parts.length > 2) throw new ProfileError();
    const [key, supplied] = parts;
    if (!key || seen.has(key)) throw new ProfileError();
    seen.add(key);
    if (key === "--new-window" && supplied === undefined) newWindow = true;
    else if (key === "--profile" && supplied && NAME.test(supplied)) name = supplied;
    else if (key === "--channel" && supplied && CHANNELS.has(supplied)) channel = supplied as Channel;
    else throw new ProfileError();
  }
  return { name, channel, newWindow };
}

function privateDirectory(path: string, owner: number): void {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const entry = lstatSync(path);
  if (!entry.isDirectory() || entry.isSymbolicLink() || entry.uid !== owner || (entry.mode & 0o077) !== 0) {
    throw new ProfileError();
  }
}

function identity(value: unknown, expected: ProfileChoice, owner: number): Identity {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProfileError();
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).sort().join(",") !== KEYS.join(",") ||
    data.version !== 1 ||
    typeof data.id !== "string" ||
    !UUID.test(data.id) ||
    data.owner !== owner ||
    data.name !== expected.name ||
    data.channel !== expected.channel
  )
    throw new ProfileError();
  return data as unknown as Identity;
}

function readIdentity(path: string, expected: ProfileChoice, owner: number): Identity {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const file = fstatSync(descriptor);
    if (!file.isFile() || file.uid !== owner || (file.mode & 0o077) !== 0 || file.size > 4096) throw new ProfileError();
    return identity(JSON.parse(readFileSync(descriptor, "utf8")), expected, owner);
  } finally {
    closeSync(descriptor);
  }
}

function isExisting(error: unknown): boolean {
  return !!error && typeof error === "object" && "code" in error && error.code === "EEXIST";
}

/** Create a completed immutable identity with exclusive link publication; concurrent openers never read half JSON. */
export function openProfile(root: string, choice: ProfileChoice): Readonly<Profile> {
  if (!NAME.test(choice.name) || !CHANNELS.has(choice.channel) || !process.getuid) throw new ProfileError();
  const owner = process.getuid();
  let temporary: string | undefined;
  try {
    privateDirectory(resolve(root), owner);
    const base = realpathSync(root);
    const channel = join(base, choice.channel);
    privateDirectory(channel, owner);
    const directory = join(channel, choice.name);
    privateDirectory(directory, owner);
    const file = join(directory, "profile.json");
    const value: Identity = { version: 1, id: randomUUID(), owner, name: choice.name, channel: choice.channel };
    temporary = join(directory, `.profile-${randomUUID()}.tmp`);
    const descriptor = openSync(
      temporary,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    try {
      writeFileSync(descriptor, `${JSON.stringify(value)}\n`);
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    try {
      linkSync(temporary, file);
    } catch (error) {
      if (!isExisting(error)) throw error;
    }
    unlinkSync(temporary);
    temporary = undefined;
    const opened = openSync(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      fsyncSync(opened);
    } finally {
      closeSync(opened);
    }
    const checked = readIdentity(file, choice, owner);
    return Object.freeze({ id: checked.id, name: checked.name, channel: checked.channel, directory });
  } catch {
    throw new ProfileError();
  } finally {
    if (temporary) {
      try {
        unlinkSync(temporary);
      } catch {
        /* A failed creation must not overwrite another identity. */
      }
    }
  }
}
