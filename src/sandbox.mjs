import { spawn } from 'node:child_process';
import { constants } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { networkInterfaces } from 'node:os';
import { mkdir, open, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve, sep } from 'node:path';

const MAX_OUTPUT_BYTES = 1024 * 1024;
const BPF_LD_W_ABS = 0x20;
const BPF_JEQ_K = 0x15;
const BPF_JSET_K = 0x45;
const BPF_RET_K = 0x06;
const SECCOMP_RET_KILL_PROCESS = 0x80000000;
const SECCOMP_RET_ERRNO = 0x00050000;
const SECCOMP_RET_ALLOW = 0x7fff0000;
const CLONE_NEWUSER = 0x10000000;
const X32_SYSCALL_BIT = 0x40000000;
const SECCOMP_ARCH_OFFSET = 4;
const SECCOMP_SYSCALL_OFFSET = 0;
const SECCOMP_ARG0_OFFSET = 16;
const CLONE3_SYSCALL = 435;
const IO_URING_SETUP_SYSCALL = 425;
const IO_URING_ENTER_SYSCALL = 426;
const IO_URING_REGISTER_SYSCALL = 427;
const AF_VSOCK = 40;
const ERRNO_EPERM = 1;
const ERRNO_ENOSYS = 38;
const CANARY_OUTSIDE_READ_ALLOWED = 73;
const CANARY_INPUT_WRITE_ALLOWED = 76;
const CANARY_NETWORK_REACHABLE = 75;
const CANARY_NETWORK_BLOCKED = 10;
const quote = value => `'${value.replaceAll("'", "'\\''")}'`;

/** @typedef {{label?: string, code: number, value: number, yes?: string, no?: string}} FilterInstruction */

/** @typedef {{executable: string, args: string[], cwd: string, env: Record<string, string>, passFds?: number[]}} ProcessSpec */

function buildSeccompFilter() {
  const arch = process.arch === 'x64'
    ? { audit: 0xc000003e, unshare: 272, clone: 56, setns: 308, socket: 41 }
    : process.arch === 'arm64'
      ? { audit: 0xc00000b7, unshare: 97, clone: 220, setns: 268, socket: 198 }
      : null;
  if (!arch) throw new Error(`Unsupported Linux sandbox architecture: ${process.arch}.`);

  /** @type {FilterInstruction[]} */
  const instructions = [
    { label: 'arch', code: BPF_LD_W_ABS, value: SECCOMP_ARCH_OFFSET },
    { code: BPF_JEQ_K, value: arch.audit, yes: 'load-nr', no: 'kill' },
    { label: 'kill', code: BPF_RET_K, value: SECCOMP_RET_KILL_PROCESS },
    { label: 'load-nr', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    ...(process.arch === 'x64' ? [
      { code: BPF_JSET_K, value: X32_SYSCALL_BIT, yes: 'x32-kill', no: 'unshare' },
      { label: 'x32-kill', code: BPF_RET_K, value: SECCOMP_RET_KILL_PROCESS },
    ] : []),
    { label: 'unshare', code: BPF_JEQ_K, value: arch.unshare, yes: 'deny', no: 'load-clone' },
    { label: 'load-clone', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: arch.clone, yes: 'load-clone-flags', no: 'load-setns' },
    { label: 'load-clone-flags', code: BPF_LD_W_ABS, value: SECCOMP_ARG0_OFFSET },
    { code: BPF_JSET_K, value: CLONE_NEWUSER, yes: 'deny', no: 'load-setns' },
    { label: 'load-setns', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: arch.setns, yes: 'deny', no: 'load-clone3' },
    { label: 'load-clone3', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: CLONE3_SYSCALL, yes: 'clone3-deny', no: 'load-socket' },
    { label: 'clone3-deny', code: BPF_RET_K, value: SECCOMP_RET_ERRNO | ERRNO_ENOSYS },
    { label: 'load-socket', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: arch.socket, yes: 'load-socket-family', no: 'load-io-setup' },
    { label: 'load-socket-family', code: BPF_LD_W_ABS, value: SECCOMP_ARG0_OFFSET },
    { code: BPF_JEQ_K, value: AF_VSOCK, yes: 'deny', no: 'load-io-setup' },
    { label: 'load-io-setup', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: IO_URING_SETUP_SYSCALL, yes: 'deny', no: 'load-io-enter' },
    { label: 'load-io-enter', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: IO_URING_ENTER_SYSCALL, yes: 'deny', no: 'load-io-register' },
    { label: 'load-io-register', code: BPF_LD_W_ABS, value: SECCOMP_SYSCALL_OFFSET },
    { code: BPF_JEQ_K, value: IO_URING_REGISTER_SYSCALL, yes: 'deny', no: 'allow' },
    { label: 'deny', code: BPF_RET_K, value: SECCOMP_RET_ERRNO | ERRNO_EPERM },
    { label: 'allow', code: BPF_RET_K, value: SECCOMP_RET_ALLOW },
  ];
  const labels = new Map(instructions.flatMap((instruction, index) => instruction.label ? [[instruction.label, index]] : []));
  const filter = Buffer.alloc(instructions.length * 8);
  instructions.forEach((instruction, index) => {
    const offset = index * 8;
    filter.writeUInt16LE(instruction.code, offset);
    if (instruction.yes) filter.writeUInt8(labels.get(instruction.yes) - index - 1, offset + 2);
    if (instruction.no) filter.writeUInt8(labels.get(instruction.no) - index - 1, offset + 3);
    filter.writeUInt32LE(instruction.value, offset + 4);
  });
  return filter;
}

/** @param {string[]} args @param {string} root @param {string} target */
function addDirectories(args, root, target) {
  const path = relative(root, target);
  if (!path || path === '..' || path.startsWith(`..${sep}`) || isAbsolute(path)) throw new Error('Sandbox workspace must be inside its data directory.');
  let current = root;
  for (const segment of path.split(sep)) {
    current = join(current, segment);
    args.push('--dir', current);
  }
}

/** @param {string[]} args @param {string} dataDir @param {string} workspace */
function addDataMount(args, dataDir, workspace) {
  const maskedParents = ['/tmp', '/run', '/sys'];
  for (const root of maskedParents) args.push('--tmpfs', root);

  const maskedParent = maskedParents.find(root => dataDir === root || dataDir.startsWith(`${root}${sep}`));
  if (maskedParent && dataDir !== maskedParent) {
    addDirectories(args, maskedParent, dataDir);
    args.push('--tmpfs', dataDir);
  } else if (!maskedParent) {
    addDirectories(args, '/', dataDir);
    args.push('--tmpfs', dataDir);
  }

  addDirectories(args, dataDir, workspace);
  args.push('--remount-ro', '/tmp', '--remount-ro', '/run', '--remount-ro', '/sys');
  if (dataDir !== '/tmp' && dataDir !== '/run' && dataDir !== '/sys') args.push('--remount-ro', dataDir);
}

/** Execute a bounded process without a host shell and kill its whole process group.
 * @param {ProcessSpec} spec
 * @param {{signal?: AbortSignal, timeout?: number, maxBytes?: number}} options
 */
export function runBoundedProcess(spec, { signal, timeout = 60_000, maxBytes = MAX_OUTPUT_BYTES } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const stdio = ['ignore', 'pipe', 'pipe', ...(spec.passFds ?? [])];
    const child = spawn(spec.executable, spec.args, { cwd: spec.cwd, env: spec.env, shell: false, detached: true, stdio });
    const stdout = [], stderr = [];
    let bytes = 0, failure;
    const kill = () => { try { process.kill(-child.pid, 'SIGKILL'); } catch { child.kill('SIGKILL'); } };
    const stop = error => { failure ??= error; kill(); };
    const abort = () => stop(signal.reason ?? new Error('Request cancelled.'));
    const timer = setTimeout(() => stop(Object.assign(new Error(`Command timed out after ${timeout} ms.`), { code: 'timeout' })), timeout);
    signal?.addEventListener('abort', abort, { once: true });
    const capture = chunks => chunk => {
      bytes += chunk.length;
      if (bytes > maxBytes) { stop(Object.assign(new Error('Command exceeded the 1 MiB output limit.'), { code: 'output_limit' })); return; }
      chunks.push(chunk);
    };
    child.stdout.on('data', capture(stdout));
    child.stderr.on('data', capture(stderr));
    child.once('error', error => { failure ??= error; });
    child.once('exit', kill);
    child.once('close', (code, terminationSignal) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      if (failure) reject(failure);
      else resolve({ exitCode: code, signal: terminationSignal, stdout: Buffer.concat(stdout).toString('utf8'), stderr: Buffer.concat(stderr).toString('utf8') });
    });
  });
}

/** Executes commands in a private bubblewrap mount and network namespace. */
export class SandboxRunner {
  /** @param {{workspace: string, dataDir: string, port: number, timeoutMs?: number, launch?: typeof runBoundedProcess}} options */
  constructor({ workspace, dataDir, port, timeoutMs = 60_000, launch = runBoundedProcess }) {
    this.workspace = workspace;
    this.dataDir = dataDir;
    this.port = port;
    this.timeoutMs = timeoutMs;
    this.launch = launch;
    this.ready = null;
    this.queue = Promise.resolve();
  }

  async initialize() {
    if (process.platform !== 'linux') throw new Error('The workspace service supports Linux containers only.');
    this.workspace = resolve(this.workspace);
    this.dataDir = resolve(this.dataDir);
    const workspacePath = relative(this.dataDir, this.workspace);
    if (!workspacePath || workspacePath === '..' || workspacePath.startsWith(`..${sep}`) || isAbsolute(workspacePath)) {
      throw new Error('Sandbox workspace must be inside its data directory.');
    }
    this.home = join(this.workspace, '.exec', 'home');
    this.temp = join(this.workspace, '.exec', 'tmp');
    const inputs = join(this.workspace, 'inputs');
    for (const path of [this.home, this.temp, inputs, join(this.workspace, 'work'), join(this.workspace, 'outputs')]) await mkdir(path, { recursive: true, mode: 0o700 });
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|LANG|LC_.*|TZ)$/.test(key)));
    Object.assign(env, { HOME: this.home, TMPDIR: this.temp, TMP: this.temp, TEMP: this.temp, XDG_CONFIG_HOME: this.home, XDG_CACHE_HOME: this.temp,
      BRIDGE_OCR_LANG: process.env.WORKSPACE_OCR_LANG || 'eng+spa' });
    const args = [
      '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts', '--hostname', 'roko-workspace',
      '--cap-drop', 'ALL', '--die-with-parent', '--seccomp', '5',
      '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/sbin', '/sbin',
      '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
      '--ro-bind', '/etc', '/etc', '--ro-bind', '/opt', '/opt', '--ro-bind', '/app', '/app',
      '--dir', '/var', '--ro-bind', '/var/lib', '/var/lib', '--dev', '/dev', '--proc', '/proc',
    ];
    addDataMount(args, this.dataDir, this.workspace);
    args.push('--bind-fd', '3', this.workspace, '--ro-bind-fd', '4', inputs, '--chdir', this.workspace, '--');
    const runtimeDir = join(this.dataDir, '.runtime');
    this.seccompFilterPath = join(runtimeDir, `sandbox-${process.arch}.bpf`);
    const temporaryFilter = join(runtimeDir, `.sandbox-${process.arch}-${randomUUID()}.tmp`);
    await mkdir(runtimeDir, { recursive: true, mode: 0o700 });
    try {
      await writeFile(temporaryFilter, buildSeccompFilter(), { flag: 'wx', mode: 0o400 });
      await rename(temporaryFilter, this.seccompFilterPath);
    } finally { await rm(temporaryFilter, { force: true }); }
    this.spec = {
      executable: '/usr/bin/bwrap',
      args,
      cwd: this.workspace,
      env,
    };
    this.executable = '/bin/sh';
  }

  async raw(command, options = {}) {
    const workspace = await open(this.workspace, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    let inputs, seccomp;
    try {
      inputs = await open(join(this.workspace, 'inputs'), constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
      seccomp = await open(this.seccompFilterPath, constants.O_RDONLY | constants.O_NOFOLLOW);
      return await this.launch({ ...this.spec, passFds: [workspace.fd, inputs.fd, seccomp.fd], args: [...this.spec.args, this.executable, '-c', command] }, options);
    } finally {
      await Promise.all([workspace.close(), inputs?.close(), seccomp?.close()]);
    }
  }

  async verify(signal) {
    await this.initialize();
    const token = randomUUID();
    const outside = join(this.dataDir, `.outside-canary-${token}`);
    const outsideWrite = join(this.dataDir, `.outside-write-${token}`);
    const inside = join(this.workspace, '.exec', `.sandbox-proof-${token}`);
    const inputFile = join(this.workspace, 'inputs', `.sandbox-probe-${token}.txt`);
    try {
      await writeFile(outside, token, { mode: 0o600 });
      await writeFile(inputFile, token, { mode: 0o600 });
      const targets = [];
      for (const values of Object.values(networkInterfaces())) {
        for (const value of values ?? []) if (value.family === 'IPv4' && !value.internal) targets.push([value.address, this.port]);
      }
      targets.push(['1.1.1.1', 53]);
      const networkProbe = `import json,socket,sys\ntargets=json.loads(${JSON.stringify(JSON.stringify(targets))})\nfor host,port in targets:\n s=socket.socket(); s.settimeout(1)\n try: s.connect((host,port)); sys.exit(0)\n except OSError: pass\n finally: s.close()\nsys.exit(${CANARY_NETWORK_BLOCKED})`;
      const userNamespaceProbe = `import ctypes,errno,sys; libc=ctypes.CDLL(None,use_errno=True); result=libc.unshare(${CLONE_NEWUSER}); sys.exit(0 if result == -1 and ctypes.get_errno() == errno.EPERM else 1)`;
      const script = `set -eu; if cat ${quote(outside)} >/dev/null 2>&1; then exit ${CANARY_OUTSIDE_READ_ALLOWED}; fi; (printf bad >${quote(outsideWrite)}) 2>/dev/null || :; (printf bad >${quote(outside)}) 2>/dev/null || :; printf '%s' ${quote(token)} >${quote(inside)}; cat ${quote(inputFile)} >/dev/null; if (printf bad >${quote(inputFile)}) 2>/dev/null; then exit ${CANARY_INPUT_WRITE_ALLOWED}; fi; status=0; python3 -c ${quote(networkProbe)} || status=$?; if [ "$status" -ne ${CANARY_NETWORK_BLOCKED} ]; then exit ${CANARY_NETWORK_REACHABLE}; fi; python3 -c ${quote(userNamespaceProbe)}`;
      const result = await this.raw(script, { signal, timeout: 30_000 });
      if (result.exitCode !== 0) throw new Error(result.stderr || `Sandbox probe exited ${result.exitCode}.`);
      if (await readFile(outside, 'utf8') !== token) throw new Error('Sandbox canary was read or modified outside the workspace.');
      try { await readFile(outsideWrite); throw new Error('Sandbox created a file outside the workspace.'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (await readFile(inside, 'utf8') !== token) throw new Error('Sandbox workspace write proof failed.');
      if (await readFile(inputFile, 'utf8') !== token) throw new Error('Sandbox modified an input file.');
    } finally { await Promise.all([outside, outsideWrite, inside, inputFile].map(path => rm(path, { force: true }))); }
    return { workspaceWrite: true, outsideReadDenied: true, outsideWriteDenied: true, inputsReadOnly: true, networkDenied: true, nestedUserNamespacesBlocked: true };
  }

  execute(command, { signal, timeout = this.timeoutMs } = {}) {
    const run = async () => {
      this.ready ??= this.verify(signal).catch(error => { this.ready = null; throw error; });
      await this.ready;
      return this.raw(command, { signal, timeout });
    };
    const result = this.queue.then(run);
    this.queue = result.catch(() => {});
    return result;
  }

  /** Run an installed administrator-owned utility inside the same sandbox. */
  async runProgram(executable, args, options = {}) {
    return this.execute([executable, ...args].map(quote).join(' '), options);
  }
}
