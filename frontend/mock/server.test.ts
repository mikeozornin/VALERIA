import { afterAll, beforeEach, describe, expect, test } from "bun:test";
import * as D from "./data";
import { createMockServer } from "./server";

const mock = createMockServer({ port: 0, speed: 40, quiet: true });
afterAll(() => mock.stop());

class Client {
  cookies = new Map<string, string>();
  csrf = "";
  async req(method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
    const h: Record<string, string> = { ...headers };
    if (this.cookies.size) h.cookie = [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; ");
    if (body !== undefined) h["content-type"] = "application/json";
    if (this.csrf && method !== "GET" && !("x-csrf-token" in headers)) h["x-csrf-token"] = this.csrf;
    const res = await fetch(mock.url + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), redirect: "manual" });
    for (const c of res.headers.getSetCookie()) {
      const [pair] = c.split(";");
      const i = pair.indexOf("=");
      const name = pair.slice(0, i), value = pair.slice(i + 1);
      if (/Max-Age=0/.test(c)) this.cookies.delete(name); else this.cookies.set(name, value);
    }
    const text = await res.text();
    return { status: res.status, body: text ? JSON.parse(text) : null };
  }
  async as(persona: "user" | "tasks" | "admin", extra: Record<string, unknown> = {}) {
    const r = await this.req("POST", "/__mock/session", { as: persona, ...extra });
    this.csrf = r.body.csrf_token;
    return this;
  }
}

async function collectEvents(client: Client, until: (events: any[]) => boolean, timeoutMs = 4000) {
  const controller = new AbortController();
  const res = await fetch(`${mock.url}/api/events`, {
    headers: { cookie: [...client.cookies].map(([k, v]) => `${k}=${v}`).join("; ") }, signal: controller.signal,
  });
  expect(res.headers.get("content-type")).toBe("text/event-stream");
  const reader = res.body!.getReader();
  const events: any[] = [];
  let buffer = "";
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const done = (async () => {
    try {
      while (!until(events)) {
        const { value, done: end } = await reader.read();
        if (end) break;
        buffer += new TextDecoder().decode(value);
        let i: number;
        while ((i = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, i);
          buffer = buffer.slice(i + 2);
          if (frame.startsWith("data: ")) events.push(JSON.parse(frame.slice(6)));
        }
      }
    } catch { /* aborted */ }
  })();
  return {
    events,
    finished: done.then(() => { clearTimeout(timer); controller.abort(); return events; }),
  };
}

beforeEach(async () => {
  await fetch(`${mock.url}/__mock/reset`, { method: "POST", body: JSON.stringify({ as: "none" }) });
});

describe("auth + CSRF", () => {
  test("logged out → 401, login, me, CSRF on non-GET, logout", async () => {
    const c = new Client();
    expect(await c.req("GET", "/api/auth/me")).toEqual({ status: 401, body: { detail: "Authentication required" } });
    const bad = await c.req("POST", "/api/auth/login", { login: "i.sokolova", password: "nope" });
    expect(bad).toEqual({ status: 401, body: { detail: "Неверный логин или пароль" } });
    const blocked = await c.req("POST", "/api/auth/login", { login: "s.egorova", password: D.DEMO_PASSWORD });
    expect(blocked.status).toBe(401);
    const ok = await c.req("POST", "/api/auth/login", { login: "i.sokolova", password: D.DEMO_PASSWORD });
    expect(ok.status).toBe(200);
    expect(Object.keys(ok.body).sort()).toEqual(["csrf_token", "user"]);
    expect(ok.body.user).toMatchObject({ id: "u-isokolova", login: "i.sokolova", role: "user", tasks_only: false });
    expect(c.cookies.has("ocw_session")).toBe(true);

    const me = await c.req("GET", "/api/auth/me");
    expect(me.body.csrf_token).toBe(ok.body.csrf_token);

    // no / wrong CSRF header → 403
    const noCsrf = await c.req("POST", "/api/chats/q-gcc13/archive", {}, {});
    expect(noCsrf).toEqual({ status: 403, body: { detail: "CSRF check failed" } });
    const wrong = await c.req("POST", "/api/auth/logout", undefined, { "x-csrf-token": "bad" });
    expect(wrong.status).toBe(403);

    c.csrf = ok.body.csrf_token;
    expect((await c.req("POST", "/api/auth/logout")).status).toBe(204);
    expect((await c.req("GET", "/api/auth/me")).status).toBe(401);
  });

  test("admin-only routes → 403 for user", async () => {
    const c = await new Client().as("user");
    expect(await c.req("GET", "/api/admin/users")).toEqual({ status: 403, body: { detail: "Administrator role required" } });
    const admin = await new Client().as("admin");
    expect((await admin.req("GET", "/api/admin/users")).status).toBe(200);
  });
});

describe("tasks_only", () => {
  test("allowlist: tasks, consultations, items, chat actions; everything else 403", async () => {
    const c = await new Client().as("tasks");
    for (const path of ["/api/options", "/api/chats", "/api/templates"]) {
      expect(await c.req("GET", path)).toEqual({ status: 403, body: { detail: "This account can only use tasks and consultations" } });
    }
    for (const path of ["/api/tasks", "/api/consultations", "/api/task-runs", "/api/config/public", "/api/me/items", "/api/queue/me"]) {
      expect((await c.req("GET", path)).status).toBe(200);
    }
    const items = (await c.req("GET", "/api/me/items")).body;
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((i: any) => i.kind !== "folder")).toBe(true);
    expect((await c.req("POST", "/api/chats/q-gcc13/unsnooze", {})).status).toBe(200);
    // folder chats are forbidden even for the owner
    expect((await c.req("POST", "/api/chats/f-planning/messages", { message: "x" })).status).toBe(403);
  });
});

describe("idempotency", () => {
  test("same key replays, other body → 409, blank key → 422", async () => {
    const c = await new Client().as("user");
    const key = { "Idempotency-Key": "run-1" };
    const first = await c.req("POST", "/api/tasks/t-ci/runs", { user_input: "Pipeline #48211" }, key);
    expect(first.status).toBe(202);
    expect(Object.keys(first.body).sort()).toEqual(["created_at", "error_code", "finished_at", "id", "phase", "plan_first", "started_at", "status", "task_id", "title", "user_input"]);
    expect(first.body.user_input).toBe("Pipeline #48211");
    const again = await c.req("POST", "/api/tasks/t-ci/runs", { user_input: "Pipeline #48211" }, key);
    expect(again.status).toBe(202);
    expect(again.body.id).toBe(first.body.id);
    const other = await c.req("POST", "/api/tasks/t-ci/runs", { user_input: "Pipeline #1" }, key);
    expect(other).toEqual({ status: 409, body: { detail: "Idempotency-Key was used for another request" } });
    const blank = await c.req("POST", "/api/tasks/t-ci/runs", {}, { "Idempotency-Key": "  " });
    expect(blank).toEqual({ status: 422, body: { detail: "Invalid Idempotency-Key" } });
    const extra = await c.req("POST", "/api/tasks/t-ci/runs", { user_input: "", foo: 1 });
    expect(extra.status).toBe(422);
    expect(extra.body.detail[0]).toMatchObject({ type: "extra_forbidden", loc: ["body", "foo"] });
    const runs = (await c.req("GET", "/api/task-runs")).body.filter((r: any) => r.id === first.body.id);
    expect(runs).toHaveLength(1);
  });
});

describe("new contract: tasks", () => {
  test("public_task has input_hint; duplicate data = task_id + user_input; real waiting status", async () => {
    const c = await new Client().as("user");
    const tasks = (await c.req("GET", "/api/tasks")).body;
    expect(Object.keys(tasks[0]).sort()).toEqual(["description", "id", "input_hint", "plan_first", "title"]);
    expect(tasks.find((t: any) => t.id === "t-mr").input_hint).toBe("Ссылка на merge request");
    const runs = (await c.req("GET", "/api/task-runs")).body;
    const dup = runs.filter((r: any) => r.task_id === "t-mr" && r.user_input === D.MR_URL && !["completed", "failed", "cancelled"].includes(r.status));
    expect(dup.map((r: any) => r.id)).toEqual(["r-running"]);
    expect(runs.find((r: any) => r.id === "r-failed")).toMatchObject({ error_code: "assistant_error", status: "failed" });

    const stream = await collectEvents(c, (evs) => evs.some((e) => e.type === "task.updated" && e.task.status === "completed"));
    const run = (await c.req("POST", "/api/tasks/t-dup/runs", { user_input: "FW-2401 #permission" })).body;
    const statuses = (await stream.finished).filter((e) => e.type === "task.updated" && e.task.id === run.id).map((e) => e.task.status);
    expect(statuses).toContain("waiting_permission");
  });

  test("admin create/PATCH accept and return input_hint (≤ 500)", async () => {
    const c = await new Client().as("admin");
    const created = await c.req("POST", "/api/admin/tasks", { title: "T", content: "C", agent: "build", model: D.DEFAULT_MODEL, input_hint: "Номер задачи" });
    expect(created.status).toBe(201);
    expect(created.body.input_hint).toBe("Номер задачи");
    const patched = await c.req("PATCH", `/api/admin/tasks/${created.body.id}`, { input_hint: "Ссылка" });
    expect(patched.body.input_hint).toBe("Ссылка");
    expect((await c.req("PATCH", `/api/admin/tasks/${created.body.id}`, { input_hint: "x".repeat(501) })).status).toBe(422);
    expect((await c.req("GET", "/api/admin/tasks")).body.find((t: any) => t.id === "t-ci").input_hint).toBe("Ссылка на пайплайн или job GitLab");
  });
});

describe("SSE lifecycle", () => {
  test("task run: queued → dispatching → running → completed with title update", async () => {
    const c = await new Client().as("user");
    const stream = await collectEvents(c, (evs) => evs.some((e) => e.type === "task.updated" && e.task.status === "completed"));
    await Bun.sleep(50);
    const run = await c.req("POST", "/api/tasks/t-mr/runs", { user_input: "!1932 — Поддержка TLS 1.3 в HTTP-клиенте" });
    expect(run.status).toBe(202);
    const events = await stream.finished;
    const statuses = events.filter((e) => e.type === "task.updated" && e.task.id === run.body.id).map((e) => e.task.status);
    expect(statuses).toEqual(["queued", "dispatching", "running", "completed"]);
    const title = events.find((e) => e.type === "task.title.updated");
    expect(title).toMatchObject({ run_id: run.body.id, title: `${"Ревью merge request"}: !1932 — Поддержка TLS 1.3 в HTTP-клиенте` });
    const result = await c.req("GET", `/api/task-runs/${run.body.id}/result`);
    expect(result.body).toMatchObject({ run_id: run.body.id, status: "available" });
  });

  test("folder chat: permission → once → completed; stop → cancelled", async () => {
    const c = await new Client().as("user");
    const stream = await collectEvents(c, (evs) => evs.some((e) => e.type === "opencode.event" && e.event.payload.type === "permission.asked"));
    const created = await c.req("POST", "/api/chats", { message: "Обнови conanfile до Conan 2", agent: "build" });
    expect(created.status).toBe(201);
    expect(Object.keys(created.body).sort()).toEqual(["chat", "job"]);
    await stream.finished;
    const chatId = created.body.chat.id;
    const perms = (await c.req("GET", `/api/chats/${chatId}/permissions`)).body;
    expect(perms).toHaveLength(1);
    expect((await c.req("POST", `/api/chats/${chatId}/permissions/${perms[0].id}`, { response: "maybe" })).body).toEqual({ detail: "Invalid permission response" });
    const done = await collectEvents(c, (evs) => evs.some((e) => e.type === "job.updated" && e.job.status === "completed"));
    expect((await c.req("POST", `/api/chats/${chatId}/permissions/${perms[0].id}`, { response: "once" })).body).toEqual({ accepted: true });
    await done.finished;
    expect((await c.req("POST", `/api/chats/${chatId}/abort`)).body).toEqual({ detail: "Chat is not running" });

    const run = (await c.req("POST", "/api/tasks/t-ci/runs", {})).body;
    const stopped = await collectEvents(c, (evs) => evs.some((e) => e.type === "task.updated" && e.task.status === "cancelled"));
    expect((await c.req("POST", `/api/task-runs/${run.id}/stop`)).status).toBe(202);
    await stopped.finished;
  });
});

describe("chat actions", () => {
  test("snooze validation, archive, unarchive, delete + chat.deleted", async () => {
    const c = await new Client().as("user");
    const past = new Date(Date.now() - 60000).toISOString();
    expect(await c.req("POST", "/api/chats/q-gcc13/snooze", { until: past })).toEqual({ status: 422, body: { detail: "Snooze time must be in the future" } });
    const archived = await c.req("POST", "/api/chats/q-gcc13/archive", {});
    expect(archived.status).toBe(200);
    expect(archived.body).toMatchObject({ kind: "question", id: "q-gcc13", chat_id: "q-gcc13", snoozed_until: null });
    expect(archived.body.archived_at).toBeTruthy();
    const future = new Date(Date.now() + 3600000).toISOString();
    expect((await c.req("POST", "/api/chats/q-gcc13/snooze", { until: future })).status).toBe(409);
    expect((await c.req("POST", "/api/chats/q-gcc13/unarchive", {})).body.archived_at).toBeNull();
    const snoozed = await c.req("POST", "/api/chats/q-gcc13/snooze", { until: future });
    expect(Date.parse(snoozed.body.snoozed_until)).toBe(Date.parse(future));

    // snooze expiry clears snoozed_until and emits chat.updated
    const soon = new Date(Date.now() + 300).toISOString();
    const expiry = await collectEvents(c, (evs) => evs.some((e) => e.type === "chat.updated" && e.chat_id === "q-gcc13" && e.snoozed_until === null));
    await c.req("POST", "/api/chats/q-gcc13/snooze", { until: soon });
    await expiry.finished;
    expect(expiry.events.some((e) => e.type === "chat.updated" && e.snoozed_until === null)).toBe(true);

    const deleted = await collectEvents(c, (evs) => evs.some((e) => e.type === "chat.deleted"));
    expect((await c.req("DELETE", "/api/chats/q-gcc13")).status).toBe(204);
    expect(await deleted.finished).toContainEqual({ type: "chat.deleted", chat_id: "q-gcc13" });
    expect((await c.req("POST", "/api/chats/q-gcc13/archive", {})).status).toBe(404);
    // someone else's chat is 404 for the action
    expect((await c.req("POST", "/api/chats/adm-q/archive", {})).status).toBe(404);
  });
});

describe("admin", () => {
  test("batch block: self / already / not found skipped, others blocked", async () => {
    const c = await new Client().as("admin");
    const r = await c.req("POST", "/api/admin/users/batch", { ids: ["u-dorlov", "u-segorova", "u-pvolkov", "u-missing"], status: "blocked" });
    expect(r.status).toBe(200);
    expect(r.body.results).toEqual([
      { id: "u-dorlov", login: "d.orlov", ok: false, skipped: true, reason: "self" },
      { id: "u-segorova", login: "s.egorova", ok: true, skipped: true, reason: "already" },
      { id: "u-pvolkov", login: "p.volkov", ok: true, skipped: false, reason: null },
      { id: "u-missing", login: null, ok: false, skipped: true, reason: "not_found" },
    ]);
    const users = (await c.req("GET", "/api/admin/users")).body;
    expect(users.find((u: any) => u.id === "u-pvolkov").status).toBe("disabled");
    expect((await c.req("POST", "/api/admin/users/batch", { ids: [], status: "gone" })).status).toBe(422);
  });

  test("PATCH optimistic concurrency: 409 conflict, 404 deleted", async () => {
    const c = await new Client().as("admin");
    const task = (await c.req("GET", "/api/admin/tasks")).body.find((t: any) => t.id === "t-ci");
    await c.req("POST", "/__mock/patch", { collection: "tasks", id: "t-ci", set: { title: "Разбор CI" }, by: "p.volkov" });
    const conflict = await c.req("PATCH", "/api/admin/tasks/t-ci", { title: "Мой заголовок", updated_at: task.updated_at });
    expect(conflict.status).toBe(409);
    expect(conflict.body.detail).toMatchObject({ code: "conflict", updated_by: "p.volkov" });
    const gone = await c.req("PATCH", "/api/admin/tasks/t-old-notes", { title: "x" });
    expect(gone).toEqual({ status: 404, body: { detail: { code: "deleted", deleted_by: "d.orlov" } } });
  });

  test("audit filters + cursor", async () => {
    const c = await new Client().as("admin");
    const page1 = (await c.req("GET", "/api/admin/audit?limit=5")).body;
    expect(page1.items).toHaveLength(5);
    expect(page1.next_cursor).toBeTruthy();
    const page2 = (await c.req("GET", `/api/admin/audit?limit=5&cursor=${page1.next_cursor}`)).body;
    expect(page2.items[0].id).toBeLessThan(page1.items[4].id);
    const stops = (await c.req("GET", "/api/admin/audit?action=job.stop.")).body.items;
    expect(stops.length).toBeGreaterThan(0);
    expect(stops.every((r: any) => r.action.startsWith("job.stop."))).toBe(true);
    const byUser = (await c.req("GET", "/api/admin/audit?user=s.egorova")).body.items;
    expect(byUser.every((r: any) => r.actor_login === "s.egorova" || r.effective_login === "s.egorova")).toBe(true);
  });

  test("statistics runs + requests filter", async () => {
    const c = await new Client().as("admin");
    const runs = (await c.req("GET", "/api/admin/statistics/runs?type=task")).body;
    expect(runs.items.length).toBeGreaterThan(100);
    expect(runs.items.every((r: any) => r.kind === "task")).toBe(true);
    const reqs = (await c.req("GET", "/api/admin/requests?type=question&user=i.sokolova")).body;
    expect(reqs.length).toBeGreaterThan(0);
    expect(reqs.every((r: any) => r.kind === "question" && r.user.login === "i.sokolova")).toBe(true);
  });
});

describe("scenario controls", () => {
  test("no folder, delay header, forced error, 404 ids", async () => {
    const c = await new Client().as("user", { folder: 0 });
    expect((await c.req("GET", "/api/auth/me")).body.user.workspace_path).toBeNull();
    expect(await c.req("POST", "/api/chats", { message: "x" })).toEqual({ status: 422, body: { detail: "No workspace assigned" } });
    const t0 = performance.now();
    await c.req("GET", "/api/tasks", undefined, { "x-mock-delay": "300" });
    expect(performance.now() - t0).toBeGreaterThanOrEqual(290);
    expect(await c.req("GET", "/api/tasks", undefined, { "x-mock-fail": "500" })).toEqual({ status: 500, body: { detail: "Mock forced error" } });
    expect((await c.req("GET", "/api/task-runs/r-nope")).body).toEqual({ detail: "Task run not found" });
  });
});
