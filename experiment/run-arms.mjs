#!/usr/bin/env node
// run-arms.mjs: run the guardrail experiment's agents, one Claude Code session per run.
// A fork of AgenticBootstrap's codes/agentic_bootstrap.mjs (MIT, Jiacheng Miao); every
// change is listed in NOTICE.txt. The run folders and runs.json come from
// make-run-folders.py (in this folder); this script only runs them.
//
//   node run-arms.mjs --config <abs>/batch.json            run every run not yet done
//   node run-arms.mjs --config <abs>/batch.json --dry-run  print one prompt and the options per arm
//   node run-arms.mjs --config <abs>/batch.json --ping     one call, no tools: does the model answer?
//   --only S1,S2   run only these run ids (smoke configs; a probe run reads smoke/<probe>.txt)
//   --root <dir>   a run root other than the config's (built by make-run-folders.py --root)
//
// Never bypassPermissions: every agent runs with permissionMode "dontAsk" and the config's
// allowlist, in a sandbox when the config asks for one (failIfUnavailable: true).

import { query } from "@anthropic-ai/claude-agent-sdk";
import { execFile } from "child_process";
import { appendFileSync, cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "fs";
import { homedir, tmpdir } from "os";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { loadTaskPrompt } from "./prompts/load_prompts.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const SDK_VERSION = JSON.parse(readFileSync(join(HERE, "node_modules/@anthropic-ai/claude-agent-sdk/package.json"), "utf-8")).version;
const INFRA_ERRORS = new Set(["rate_limit", "overloaded", "server_error", "unknown", "model_not_found", "authentication_failed"]);

const expand = (p) => (typeof p === "string" && p.startsWith("~/") ? join(homedir(), p.slice(2)) : p);

function parseArgs(argv) {
  const a = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--")) continue;
    const key = argv[i].slice(2);
    const next = argv[i + 1];
    a[key] = next !== undefined && !next.startsWith("--") ? (i++, next) : true;
  }
  return a;
}

// ── environment, sandbox, options ─────────────────────────────────────
function venvRoots(python) {
  // the venv, and the base interpreter it points to (uv keeps it under ~/.local/share/uv/python)
  const venv = resolve(dirname(python), "..");
  const roots = [venv];
  const cfg = join(venv, "pyvenv.cfg");
  if (existsSync(cfg)) {
    const home = readFileSync(cfg, "utf-8").split("\n").find((l) => l.startsWith("home"));
    if (home) roots.push(resolve(home.split("=")[1].trim(), "..", ".."));
  }
  return roots;
}

// Built from scratch: nothing from the parent's CLAUDE_* or ANTHROPIC_* variables, and no token.
function buildEnv(cfg) {
  const m = cfg.model;
  return {
    PATH: `${dirname(cfg.python)}:/usr/local/bin:/usr/bin:/bin`,
    VIRTUAL_ENV: resolve(dirname(cfg.python), ".."),
    HOME: homedir(), USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "",
    SHELL: "/bin/bash", LANG: "C.UTF-8", TERM: "dumb", TMPDIR: "/tmp",
    CLAUDE_CONFIG_DIR: cfg.claude_config_dir,
    ANTHROPIC_BASE_URL: cfg.base_url,
    ANTHROPIC_MODEL: m, ANTHROPIC_DEFAULT_OPUS_MODEL: m, ANTHROPIC_DEFAULT_SONNET_MODEL: m,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: m, ANTHROPIC_SMALL_FAST_MODEL: m, CLAUDE_CODE_SUBAGENT_MODEL: m,
    DISABLE_TELEMETRY: "1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1", DISABLE_AUTOUPDATER: "1",
    DISABLE_ERROR_REPORTING: "1", API_TIMEOUT_MS: String(cfg.api_timeout_ms || 900000),
    // On a 429 Claude Code otherwise gives up after 10 retries (about 3 minutes); the watchdog keeps
    // backing off (at most 5 minutes apart) until this much waiting has passed. Same in every arm.
    ...(cfg.retry_wait_max_ms ? { CLAUDE_CODE_RETRY_WATCHDOG: "1",
      CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS: String(cfg.retry_wait_max_ms) } : {}),
  };
}

function sandboxFor(cfg, ws) {
  if (!cfg.sandbox) return undefined;
  return {
    enabled: true,
    failIfUnavailable: true,
    autoAllowBashIfSandboxed: false, // the allowlist, not the sandbox, decides which commands run
    allowUnsandboxedCommands: false,
    filesystem: {
      allowWrite: [ws, "/tmp"],
      denyRead: [homedir(), "/mnt"],
      allowRead: [ws, ...venvRoots(cfg.python)],
    },
    network: { allowedDomains: [] },
  };
}

function baseOptions(cfg, env) {
  return {
    model: cfg.model,
    env,
    permissionMode: "dontAsk",
    settings: { apiKeyHelper: `${cfg.python} ${cfg.key_helper} ${cfg.env_file}` },
  };
}

function runOptions(cfg, run, persona, env, hooks, log) {
  return {
    ...baseOptions(cfg, env),
    systemPrompt: persona,
    cwd: run.workspace,
    tools: { type: "preset", preset: "claude_code" },
    allowedTools: cfg.allowed_tools,
    disallowedTools: cfg.disallowed_tools,
    settingSources: ["project"],
    skills: run.guardrail ? ["analysis-guardrails"] : [],
    maxTurns: run.max_turns ?? cfg.max_turns,
    sandbox: sandboxFor(cfg, run.workspace),
    hooks,
    stderr: (d) => appendFileSync(join(log, "stderr.txt"), d),
  };
}

function promptFor(cfg, run) {
  if (run.probe) {
    return readFileSync(join(HERE, "smoke", `${run.probe}.txt`), "utf-8")
      .replaceAll("{{WS}}", run.workspace).replaceAll("{{RUN_ROOT}}", cfg._root);
  }
  const task = loadTaskPrompt({ dataPath: run.data_path, researchQuestion: cfg.question,
                                numRounds: run.rounds || cfg.rounds, outDir: run.workspace });
  return run.prefix ? `${run.prefix}\n\n${task}` : task;
}

// ── the passive logger (every arm): records, never decides ────────────
function loggerHooks(cfg, run, kitHooks) {
  const L = (name, rec) => appendFileSync(join(run.log, name), JSON.stringify({ ts: new Date().toISOString(), ...rec }) + "\n");
  const py = (script, args, input) => new Promise((res) => {
    const p = execFile(cfg.python, [join(kitHooks, script), ...args], { timeout: 20000 },
      (err, stdout, stderr) => res({ err: err ? String(err) : null, stdout, stderr }));
    p.stdin.end(JSON.stringify(input));
  });
  const parse = (r) => { try { return JSON.parse(r.stdout); } catch { return { raw: r.stdout, stderr: r.stderr, err: r.err }; } };
  const head = (x) => (typeof x === "string" ? x : JSON.stringify(x ?? "")).slice(0, 500);
  // Every callback returns {} (continue), catches its own errors, and has a 30 s timeout.
  const safe = (fn) => async (input) => {
    try { await fn(input); } catch (e) { try { L("logger-errors.jsonl", { error: String(e) }); } catch { /* ignore */ } }
    return {};
  };
  const m = (fn) => [{ hooks: [safe(fn)], timeout: 30 }];
  return {
    PreToolUse: m(async (i) => L("logger-pre.jsonl", { tool_use_id: i.tool_use_id, tool: i.tool_name, tool_input: i.tool_input,
      verdict: parse(await py("guard-pre-tool.py", ["--mode", "log", "--workspace", run.workspace], i)) })),
    PostToolUse: m(async (i) => L("logger-post.jsonl", { tool_use_id: i.tool_use_id, tool: i.tool_name,
      input: head(i.tool_input).slice(0, 300), output_head: head(i.tool_response) })),
    PostToolUseFailure: m(async (i) => L("logger-post.jsonl", { tool_use_id: i.tool_use_id, tool: i.tool_name,
      input: head(i.tool_input).slice(0, 300), failure: head(i.error ?? i) })),
    Stop: m(async (i) => L("logger-stop.jsonl", { stop_hook_active: i.stop_hook_active,
      gate: parse(await py("stop-gate.py", ["--mode", "log", "--workspace", run.workspace], i)) })),
    PermissionDenied: m(async (i) => L("logger-denied.jsonl", { tool_use_id: i.tool_use_id, tool: i.tool_name,
      tool_input: i.tool_input, reason: i.reason })),
    InstructionsLoaded: m(async (i) => L("logger-instructions.jsonl", { file_path: i.file_path, memory_type: i.memory_type,
      load_reason: i.load_reason })),
    UserPromptExpansion: m(async (i) => L("logger-expansion.jsonl", { expansion_type: i.expansion_type,
      command_name: i.command_name, command_source: i.command_source ?? null, args_head: String(i.command_args ?? "").slice(0, 200) })),
  };
}

// ── one run ───────────────────────────────────────────────────────────
// LiteLLM's 429 text names the key by its sha256; not the token, but a stable id of a live key.
const scrubKeyHash = (s) => (typeof s === "string" ? s.replace(/api_key: [0-9a-f]{16,}/g, "api_key: <hash>") : s);

const countLines = (p, pred = () => true) => (existsSync(p)
  ? readFileSync(p, "utf-8").split("\n").filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return {}; } }).filter(pred).length
  : 0);

async function attemptOnce(cfg, run, persona, kitHooks) {
  const env = buildEnv(cfg);
  const opts = runOptions(cfg, run, persona, env, loggerHooks(cfg, run, kitHooks), run.log);
  const prompt = promptFor(cfg, run);
  writeFileSync(join(run.log, "prompt.txt"), prompt);
  let init = null, result = null, error = null;
  const assistantErrors = [];
  const retries = { count: 0, wait_ms: 0, lastAttempt: null, lastDelay: 0 };
  const t0 = Date.now();
  try {
    for await (const msg of query({ prompt, options: opts })) {
      appendFileSync(join(run.log, "transcript.jsonl"), JSON.stringify(msg) + "\n");
      if (msg.type === "system" && msg.subtype === "init") { init = msg; writeFileSync(join(run.log, "init.json"), JSON.stringify(msg, null, 1)); }
      if (msg.type === "system" && msg.subtype === "api_retry") {
        // the watchdog repeats an attempt's event every 30 s with the delay left; count each attempt once
        const d = msg.retry_delay_ms || 0;
        if (msg.attempt !== retries.lastAttempt || d > retries.lastDelay) { retries.count++; retries.wait_ms += d; }
        retries.lastAttempt = msg.attempt; retries.lastDelay = d;
      }
      if (msg.type === "assistant" && msg.error) assistantErrors.push(msg.error);
      if (msg.type === "result") result = msg;
    }
  } catch (e) {
    error = String(e?.message || e);
  }
  const elapsed = (Date.now() - t0) / 1000;
  // Infrastructure, at any round: an API, network or rate-limit error, or error_during_execution.
  // Running out of turns is the agent's own outcome, not infrastructure (the SDK throws for it too).
  const maxTurns = result?.subtype === "error_max_turns";
  const infra = !maxTurns && (error !== null || assistantErrors.some((x) => INFRA_ERRORS.has(x))
    || result?.subtype === "error_during_execution");
  return { init, result, error, assistantErrors, retries, elapsed, infra };
}

async function runOne(cfg, run, personas, kitHooks) {
  const base = dirname(run.workspace);
  const metaPath = join(base, "metadata.json");
  if (existsSync(metaPath)) {
    const st = JSON.parse(readFileSync(metaPath, "utf-8")).status;
    if (st === "complete" || st === "failed") { console.log(`[${run.id}] skip (${st})`); return null; }
  }
  const pristine = join(base, "pristine");
  if (!existsSync(pristine)) cpSync(run.workspace, pristine, { recursive: true });
  const persona = personas[run.persona];
  const maxAttempts = 1 + (cfg.rerun_infra_failures ?? 1);
  let a, attempt = 0;
  const reruns = [];
  while (true) {
    attempt++;
    console.log(`[${run.id}] attempt ${attempt} starting ${new Date().toISOString()}`);
    a = await attemptOnce(cfg, run, persona, kitHooks);
    if (!a.infra || attempt >= maxAttempts) break;
    // preregistered rerun rule: an infrastructure failure at any round runs again once, from scratch, on the same file
    const keep = join(run.log, `attempt-${attempt}`);
    mkdirSync(keep, { recursive: true });
    for (const f of readdirSync(run.log)) if (!f.startsWith("attempt-")) renameSync(join(run.log, f), join(keep, f));
    rmSync(run.workspace, { recursive: true, force: true });
    cpSync(pristine, run.workspace, { recursive: true });
    reruns.push({ attempt, reason: scrubKeyHash(a.error || a.assistantErrors.join(",") || a.result?.subtype) });
  }
  const rounds = run.rounds || cfg.rounds;
  const roundStatus = {};
  for (let r = 1; r <= rounds; r++) roundStatus[`has_r${r}`] = existsSync(join(run.workspace, `results_r${r}.csv`));
  const r = a.result;
  const meta = {
    id: run.id, arm: run.arm, persona: run.persona, index: run.index, variant: run.variant ?? null, probe: run.probe ?? null,
    data_seed: run.data_seed, blind_seed: run.blind_seed, model: cfg.model, model_reported: a.init?.model ?? null,
    claude_code_version: a.init?.claude_code_version ?? null, api_key_from_helper: a.init ? a.init.apiKeySource === "apiKeyHelper" : null,
    permission_mode: a.init?.permissionMode ?? null, skills_loaded: a.init?.skills ?? null,
    sdk_version: SDK_VERSION, route: cfg.route, rounds, max_turns: run.max_turns ?? cfg.max_turns,
    api_retries: a.retries.count, api_retry_wait_seconds: a.retries.wait_ms / 1000,
    elapsed_seconds: a.elapsed, cost_usd_reported: r?.total_cost_usd ?? null, usage: r?.usage ?? null,
    num_turns: r?.num_turns ?? null, result_subtype: r?.subtype ?? null, stop_reason: r?.stop_reason ?? null,
    permission_denials: r?.permission_denials?.length ?? null,
    logger_denials: countLines(join(run.log, "logger-denied.jsonl")),
    hook_denies: countLines(join(run.log, "hook-decisions.jsonl"), (x) => x.decision === "deny"),
    stop_continuations: countLines(join(run.log, "stop-gate.jsonl"), (x) => x.decision === "block"),
    stop_attempts_logged: countLines(join(run.log, "logger-stop.jsonl")),
    ...roundStatus, has_final: existsSync(join(run.workspace, "results_final.csv")),
    attempts: attempt, reruns, assistant_errors: a.assistantErrors,
    status: a.infra || (a.error && !r) ? "failed" : "complete", error: scrubKeyHash(a.error ?? (r?.is_error ? (r.errors || []).join("; ") : null)),
    finished_at: new Date().toISOString(),
  };
  writeFileSync(metaPath, JSON.stringify(meta, null, 2));
  console.log(`[${run.id}] ${meta.status} in ${a.elapsed.toFixed(0)}s, turns=${meta.num_turns}, final=${meta.has_final}`);
  return meta;
}

async function runPool(items, concurrency, fn) {
  const out = [];
  let i = 0;
  const worker = async () => { while (i < items.length) { const it = items[i++]; out.push(await fn(it)); } };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
  return out;
}

async function ping(cfg) {
  const dir = join(tmpdir(), `gs-ping-${process.pid}`);
  mkdirSync(dir, { recursive: true });
  const t0 = Date.now();
  let init = null, result = null, error = null;
  try {
    for await (const msg of query({ prompt: "Reply with the single word: ready", options: {
      ...baseOptions(cfg, buildEnv(cfg)), cwd: dir, tools: [], maxTurns: 1, settingSources: [] } })) {
      if (msg.type === "system" && msg.subtype === "init") init = msg;
      if (msg.type === "result") result = msg;
    }
  } catch (e) { error = String(e?.message || e); }
  rmSync(dir, { recursive: true, force: true });
  const out = { ok: !error && result?.subtype === "success", seconds: (Date.now() - t0) / 1000, sdk_version: SDK_VERSION,
    claude_code_version: init?.claude_code_version ?? null, model_reported: init?.model ?? null,
    api_key_from_helper: init ? init.apiKeySource === "apiKeyHelper" : null, reply: result?.result?.slice(0, 80) ?? null,
    usage: result?.usage ?? null, error };
  console.log(JSON.stringify(out, null, 1));
  return out;
}

// ── main ──────────────────────────────────────────────────────────────
async function main() {
  const a = parseArgs(process.argv.slice(2));
  if (!a.config) { console.log("usage: node run-arms.mjs --config <abs path> [--dry-run | --ping] [--only id1,id2] [--root <dir>]"); process.exit(1); }
  const cfgPath = resolve(String(a.config));
  const cfg = JSON.parse(readFileSync(cfgPath, "utf-8"));
  if (typeof a.root === "string") cfg.run_root = a.root;
  for (const k of ["run_root", "python", "key_helper", "env_file", "claude_config_dir"]) cfg[k] = expand(cfg[k]);
  cfg._root = cfg.run_root;
  mkdirSync(cfg.claude_config_dir, { recursive: true });

  if (a.ping) { const p = await ping(cfg); process.exit(p.ok ? 0 : 1); }

  const runsDoc = JSON.parse(readFileSync(join(cfg.run_root, "runs.json"), "utf-8"));
  let runs = runsDoc.runs;
  if (typeof a.only === "string") { const ids = new Set(a.only.split(",")); runs = runs.filter((r) => ids.has(r.id)); }
  const personas = {};
  for (const p of new Set(runs.map((r) => r.persona))) {
    personas[p] = readFileSync(join(HERE, "prompts", "persona", "_generic", `${p}.md`), "utf-8").trim();
  }

  if (a["dry-run"]) {
    const seen = new Set();
    for (const run of runs) {
      const key = `${run.arm}:${run.variant ?? ""}:${run.probe ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const opts = runOptions(cfg, run, personas[run.persona], buildEnv(cfg), { "(logger)": "PreToolUse, PostToolUse, PostToolUseFailure, Stop, PermissionDenied, InstructionsLoaded, UserPromptExpansion" }, run.log);
      delete opts.stderr;
      console.log(`\n===== ${run.id} (arm ${run.arm}${run.variant ? ", " + run.variant : ""}${run.probe ? ", probe " + run.probe : ""}) =====`);
      console.log(`--- system prompt (${run.persona}) ---\n${opts.systemPrompt}`);
      console.log(`--- prompt ---\n${promptFor(cfg, run)}`);
      console.log(`--- options ---\n${JSON.stringify({ ...opts, systemPrompt: "(above)" }, null, 1)}`);
      console.log(`--- workspace .claude/settings.json ---\n${readFileSync(join(run.workspace, ".claude", "settings.json"), "utf-8")}`);
    }
    console.log(`\n[dry-run] ${runs.length} runs, concurrency ${cfg.concurrency}. No model calls.`);
    return;
  }

  const p = await ping(cfg);
  if (!p.ok) { console.error("The model did not answer the ping; nothing was started."); process.exit(1); }
  console.log(`Running ${runs.length} runs, concurrency ${cfg.concurrency}, model ${cfg.model}, SDK ${SDK_VERSION}`);
  const t0 = Date.now();
  const metas = (await runPool(runs, cfg.concurrency || 1, (r) => runOne(cfg, r, personas, runsDoc.kit_hooks))).filter(Boolean);
  const summaryPath = join(cfg.run_root, "summary.json");
  const prev = existsSync(summaryPath) ? JSON.parse(readFileSync(summaryPath, "utf-8")) : { sessions: [] };
  prev.sessions.push({ started: new Date(t0).toISOString(), minutes: (Date.now() - t0) / 60000, ran: metas.length,
    complete: metas.filter((m) => m.status === "complete").length, failed: metas.filter((m) => m.status === "failed").length });
  writeFileSync(summaryPath, JSON.stringify(prev, null, 2));
  console.log(`\nDone: ${metas.length} runs in ${((Date.now() - t0) / 60000).toFixed(1)} min.`);
}

main().catch((err) => { console.error("Fatal:", err); process.exit(1); });
