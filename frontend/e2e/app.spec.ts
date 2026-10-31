import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const PAGES = [
  ["/command", "Command Centre"],
  ["/intelligence", "Attack Intelligence"],
  ["/investigate", "Investigation Workbench"],
  ["/models", "Model Intelligence"],
  ["/defence", "Defence & Audit"],
  ["/replay", "Replay Lab"],
  ["/data", "Data Manager"],
  ["/decisions", "Agent Decisions"],
] as const;

async function backendReady(request: APIRequestContext) {
  await expect(async () => {
    const health = await (await request.get("/api/health")).json();
    expect(health.status).toBe("ok");
  }).toPass({ timeout: 60_000 });
}

/** Fails a test on any uncaught page error or console error, such as a broken API call. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  return errors;
}

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ request }) => { await backendReady(request); });

for (const [path, title] of PAGES) {
  test(`${title} renders with backend data and no errors`, async ({ page }) => {
    const errors = watchErrors(page);
    const calls: string[] = [];
    page.on("response", (r) => { if (r.url().includes("/api/") && r.ok()) calls.push(r.url()); });
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    await expect.poll(() => calls.length).toBeGreaterThan(0);
    await page.waitForTimeout(1000);  // Pages poll and stream, so the network never goes idle.
    expect(errors).toEqual([]);
  });
}

test("replay controls drive the real engine and SSE streams real flows", async ({ page, request }) => {
  const errors = watchErrors(page);
  await page.goto("/replay");
  await expect(page.getByText("Stream connected").or(page.getByText(/connected/i)).first()).toBeVisible();
  await page.getByRole("button", { name: "Start", exact: true }).click();

  const feed = page.locator("table tbody tr").first();
  await expect(feed).toBeVisible({ timeout: 30_000 });
  const firstFlow = (await feed.locator("a").first().textContent())!;
  const detail = await request.get(`/api/flows/${firstFlow}`);
  expect(detail.ok()).toBeTruthy();
  const body = await detail.json();
  expect(body.flow_id).toBe(firstFlow);

  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect.poll(async () => (await (await request.get("/api/replay/status")).json()).status).toBe("paused");
  const held = (await (await request.get("/api/replay/status")).json()).cursor;
  await page.waitForTimeout(600);
  expect((await (await request.get("/api/replay/status")).json()).cursor).toBe(held);

  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect.poll(async () => (await (await request.get("/api/replay/status")).json()).cursor, { timeout: 30_000 }).toBeGreaterThan(held);

  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect.poll(async () => (await (await request.get("/api/replay/status")).json()).status).toBe("stopped");
  const stopped = (await (await request.get("/api/replay/status")).json()).cursor;
  await page.waitForTimeout(500);
  expect((await (await request.get("/api/replay/status")).json()).cursor).toBe(stopped);
  expect(errors).toEqual([]);
});

test("command centre shows the same totals as the API", async ({ page, request }) => {
  await page.goto("/command");
  const status = await (await request.get("/api/replay/status")).json();
  const total = status.counts.total.toLocaleString("en-IN");
  await expect(page.getByText(total, { exact: true }).first()).toBeVisible();
});

test("workbench shows agent outputs, tool calls and a rendered evidence graph", async ({ page, request }) => {
  const list = await (await request.get("/api/flows?scope=historical&disagree=true&limit=1")).json();
  const flowId = list.items[0].flow_id;
  await page.goto(`/investigate?flow=${flowId}`);
  await expect(page.getByRole("heading", { name: flowId })).toBeVisible();
  await expect(page.getByText("Agent 1: threat detection", { exact: true })).toBeVisible();
  await expect(page.getByText("Agent 2", { exact: false }).first()).toBeVisible();
  const detail = await (await request.get(`/api/flows/${flowId}`)).json();
  await expect(page.getByText(detail.fusion.rationale)).toBeVisible();
  await expect(page.getByText(detail.investigation.tool_calls[0].tool).first()).toBeVisible();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  expect(await page.locator(".react-flow__node").count()).toBeGreaterThan(3);
});

test("model intelligence draws charts and the measured evaluation modes", async ({ page, request }) => {
  await page.goto("/models");
  const metrics = await (await request.get("/api/metrics")).json();
  await page.getByTestId("reference-results").locator("summary").click();
  await expect(page.getByText("Evaluation modes")).toBeVisible();
  const macro = metrics.evaluation_report.evaluation_modes.mode_b_with_anomaly_and_fusion.macro_f1.toFixed(5);
  await expect(page.getByRole("cell", { name: macro }).first()).toBeVisible();
  expect(await page.locator(".recharts-surface").count()).toBeGreaterThan(0);
});

test("what-if simulation calls the backend and changes nothing until applied", async ({ page, request }) => {
  const before = (await (await request.get("/api/policy")).json()).thresholds;
  await page.goto("/defence");
  await page.locator("#allow_benign_confidence").fill("0.999");
  await page.getByRole("button", { name: "Simulate what-if" }).click();
  await expect(page.getByText(/changed|No decisions change/i).first()).toBeVisible({ timeout: 15_000 });
  expect((await (await request.get("/api/policy")).json()).thresholds).toEqual(before);
  await expect(page.locator("td").getByText("policy_simulation", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
});

test("agents 4 and 5 report their real state", async ({ page, request }) => {
  const models = await (await request.get("/api/models/status")).json();
  if (!models.defense_available) {
    await page.goto("/defence");
    await expect(page.getByText("Off for this input")).toBeVisible();
    await expect(page.getByText(/need source_ip/)).toBeVisible();
    return;
  }
  if (models.replay_input.includes("rehearsal")) {
    await page.goto("/command");
    await expect(page.getByText("Rehearsal input with invented addresses")).toBeVisible();
  }
  await request.post("/api/replay/start", { data: { speed: 100000, batch_size: 1000, max_investigations_per_batch: 1 } });
  await expect.poll(async () => (await (await request.get("/api/replay/status")).json()).status, { timeout: 60_000 }).toBe("completed");
  const defense = await (await request.get("/api/defense")).json();
  expect(defense.policies.length).toBeGreaterThan(0);

  await page.goto("/defence");
  const policy = defense.policies[0];
  await expect(page.getByRole("button", { name: policy.policy_id, exact: true })).toBeVisible();
  await page.getByRole("button", { name: policy.policy_id, exact: true }).click();
  await expect(page.getByText(policy.rationale)).toBeVisible();

  await page.goto("/intelligence");
  await expect(page.getByText("Agent 4: temporal correlation")).toBeVisible();
  await expect(page.getByRole("cell", { name: defense.groups[0].group_id, exact: true })).toBeVisible();

  const blocked = await (await request.get("/api/flows?scope=replay&limit=500")).json();
  expect(blocked.items.some((f: { defense_action: string }) => f.defense_action)).toBeTruthy();
  const hit = blocked.items.find((f: { defense_action: string }) => f.defense_action === "BLOCK");
  if (hit) {
    await page.goto(`/investigate?flow=${hit.flow_id}`);
    await expect(page.getByText(`${hit.applied_policy_id}`).first()).toBeVisible();
  }
});

test("the orchestrator turns to face each agent as it works, from real replay events", async ({ page, request }) => {
  await page.setViewportSize({ width: 1536, height: 1024 });
  const errors = watchErrors(page);
  const status = await (await request.get("/api/replay/status")).json();
  if (status.status === "running" || status.status === "paused") await request.post("/api/replay/stop");
  await page.goto("/command");
  await expect(page).toHaveTitle("CyberSentinel by NexAI");
  await expect(page.getByText("by NexAI", { exact: true })).toBeVisible();
  const stage = page.getByTestId("agent-stage");
  await expect(stage).toHaveAttribute("data-yaw", /.+/, { timeout: 20_000 });
  await expect(stage).toHaveAttribute("data-focus", "none");
  await page.getByRole("button", { name: "Start replay" }).click();

  const expected = (await (await request.get("/api/replay/status")).json()).defense_available ? ["A1", "A2", "A3", "A4", "A5"] : ["A1", "A2", "A3"];
  const facing = new Set<string>();
  let shot = false;
  const signs: Record<string, number> = {};
  await expect(async () => {
    // Read together: the focus moves on between separate reads.
    const now = await stage.evaluate((el) => ({ ...(el as HTMLElement).dataset }));
    if (now.focus && now.focus !== "none") {
      signs[now.focus] = Number(now.yawTarget);
      if (Math.abs(Number(now.yaw) - Number(now.yawTarget)) < 0.2) {
        facing.add(now.focus);
        if (now.focus === "A5" && !shot) {
          shot = true;
          await page.screenshot({ path: "../data/processed/e2e-artifacts/command-centre.png" });
        }
      }
    }
    expect([...facing].sort()).toEqual(expected);
  }).toPass({ timeout: 60_000, intervals: [100] });

  // Left-hand agents are a turn one way and right-hand agents the other, so it really is looking at them.
  expect(signs.A1).toBeLessThan(0);
  if (expected.includes("A5")) expect(signs.A5).toBeGreaterThan(0);
  await expect(page.getByTestId("stage-A1")).toContainText(/\d+%/);
  await expect(page.getByTestId("agent-trace")).toContainText(/Anomalous|Normal/);

  await page.getByRole("button", { name: "Stop replay" }).click();
  await expect(stage).toHaveAttribute("data-focus", "none", { timeout: 10_000 });
  await page.getByRole("button", { name: /^Show record / }).last().click();
  await expect.poll(() => stage.getAttribute("data-focus"), { timeout: 5_000 }).toBe("A1");
  expect(errors).toEqual([]);
});

test("without WebGL the screen explains itself, and the agents' state is still in the panels", async ({ page }) => {
  await page.addInitScript(() => {
    const real = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      return kind.startsWith("webgl") ? null : (real as (...a: unknown[]) => unknown).call(this, kind, ...rest);
    } as typeof real;
  });
  await page.goto("/command");
  await expect(page.getByText("3D view is unavailable in this browser")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("agent-trace")).toBeVisible();
});

test("the Command Centre fits a phone with no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/command");
  await expect(page.getByTestId("agent-stage")).toHaveAttribute("data-yaw", /.+/, { timeout: 20_000 });
  const overflow = await page.evaluate(() => [document.documentElement.scrollWidth - innerWidth, document.querySelector("main")!.scrollWidth - document.querySelector("main")!.clientWidth]);
  expect(overflow).toEqual([0, 0]);
  await expect(page.getByRole("button", { name: "STOP", exact: true })).toBeInViewport();
  await expect(page.getByText("by NexAI", { exact: true })).toBeInViewport();
  // The scene must really be drawn: a black canvas once passed every other check here.
  await page.waitForTimeout(500);
  const picture = (await page.getByTestId("agent-stage").screenshot()).toString("base64");
  const brightness = await page.evaluate(async (png) => {
    const image = new Image();
    image.src = `data:image/png;base64,${png}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d")!;
    context.drawImage(image, 0, 0);
    const { data } = context.getImageData(0, Math.floor(image.height * 0.3), image.width, Math.floor(image.height * 0.6));
    let total = 0;
    for (let i = 0; i < data.length; i += 4) total += data[i] + data[i + 1] + data[i + 2];
    return total / (data.length / 4) / 3;
  }, picture);
  expect(brightness).toBeGreaterThan(8);
  await page.screenshot({ path: "../data/processed/e2e-artifacts/command-phone.png" });
});

