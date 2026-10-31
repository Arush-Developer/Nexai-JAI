// The Data Manager, in real Chrome against the real backend and saved models.
// Input endpoints and timestamps are SYNTHETIC (scripts/make_local_fixture.py), so nothing here measures performance.
// Chrome's native folder dialog cannot be driven by a test. The real File System Access API runs instead on
// handles from the browser's private file system, so every call after the dialog is the real one.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const FIXTURE = resolve(ROOT, "data/processed/local_fixture");
const SHOTS = resolve(ROOT, "data/processed/e2e-artifacts");
const noDataset = !!process.env.E2E_NO_DATASET;

test.describe.configure({ mode: "serial" });

test.beforeAll(() => {
  if (noDataset) return;  // That run's config has no eval partition, so it reuses the file built by a normal run.
  execFileSync(resolve(ROOT, ".venv/bin/python"), ["scripts/make_local_fixture.py", "--rows", "20000", "--out-dir", FIXTURE], { cwd: ROOT });
});

/** The dialog is replaced; the handle it returns, and everything done with it, is the real API. */
async function usePrivateFileSystem(page: Page) {
  await page.addInitScript(() => {
    (window as unknown as Record<string, unknown>).showDirectoryPicker = async (options?: { mode?: string }) => {
      const root = await navigator.storage.getDirectory();
      return root.getDirectoryHandle(options?.mode === "readwrite" ? "chosen-output" : "chosen-input", { create: true });
    };
  });
}

async function seedInput(page: Page, files: Record<string, string>) {
  await page.evaluate(async (all) => {
    const root = await navigator.storage.getDirectory();
    for (const name of ["chosen-input", "chosen-output"]) await root.removeEntry(name, { recursive: true }).catch(() => undefined);
    let dir = await root.getDirectoryHandle("chosen-input", { create: true });
    for (const [path, base64] of Object.entries(all)) {
      const parts = path.split("/");
      dir = await root.getDirectoryHandle("chosen-input");
      for (const part of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(part, { create: true });
      const writable = await (await dir.getFileHandle(parts[parts.length - 1], { create: true })).createWritable();
      await writable.write(Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)));
      await writable.close();
    }
  }, files);
}

const base64 = (name: string) => readFileSync(resolve(FIXTURE, name)).toString("base64");

async function chooseType(page: Page, value: string) {
  await page.getByLabel("Dataset type").selectOption(value);
  await page.getByRole("button", { name: "INSPECT DATA" }).click();
  await expect(page.getByRole("heading", { name: "Inspection report" })).toBeVisible({ timeout: 60_000 });
}

async function runPipeline(page: Page) {
  const repeat = page.getByText("Process these identical files again");
  if (await repeat.isVisible()) await repeat.click();
  await page.getByRole("button", { name: "RUN DAY 2 PIPELINE" }).click();
  await expect(page.getByTestId("run-progress")).toContainText("State: completed", { timeout: 120_000 });
}

test.describe("with the official dataset", () => {
  test.skip(noDataset, "Only the no-dataset check runs in that mode.");
  test.use({ viewport: { width: 1280, height: 2000 } });

  test("the folder API path: connect, inspect, watch all five agents, validate and export", async ({ page }) => {
    await usePrivateFileSystem(page);
    await page.goto("/data");
    await expect(page.getByRole("heading", { name: "Data Manager" })).toBeVisible();
    await expect(page.getByRole("button", { name: "RUN DAY 2 PIPELINE" })).toHaveCount(0);
    await seedInput(page, { "day2/hidden_style_synthetic.csv": base64("hidden_style_synthetic.csv"), "day2/hidden_style_synthetic.zip": base64("hidden_style_synthetic.zip") });

    await page.getByRole("button", { name: "CONNECT LOCAL INPUT FOLDER" }).click();
    await expect(page.getByTestId("input-folder")).toHaveText("chosen-input");
    await expect(page.getByRole("cell", { name: "hidden_style_synthetic.csv", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "hidden_style_synthetic.zip", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "day2", exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "INSPECT DATA" })).toBeDisabled();

    // Both are the same flows, so with no stated order they must not be joined.
    await chooseType(page, "hidden");
    await expect(page.getByText("order across files is unknown").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "RUN DAY 2 PIPELINE" })).toBeDisabled();

    await page.getByLabel("Select day2/hidden_style_synthetic.zip").uncheck();
    await expect(page.getByText("Selection changed")).toBeVisible();
    await page.getByRole("button", { name: "INSPECT DATA" }).click();
    await expect(page.getByText("20,000").first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("From this input's timestamps")).toBeVisible();
    await expect(page.getByRole("button", { name: "RUN DAY 2 PIPELINE" })).toBeEnabled();
    await page.screenshot({ path: resolve(SHOTS, "data-manager-inspection.png"), fullPage: true });

    await runPipeline(page);
    for (const agent of ["A1", "A2", "A3", "A4", "A5"]) await expect(page.getByTestId(`agent-${agent}`)).toContainText("done");
    await expect(page.getByTestId("run-progress")).toContainText("of 20,000 records");
    await expect(page.getByTestId("policy-events")).toContainText("ACTIVATED");
    await expect(page.getByTestId("policy-events")).toContainText("EXPIRED");
    await expect(page.getByTestId("validation")).toContainText("Every rule passed");
    await expect(page.getByTestId("validation")).toContainText("Ready to submit");
  await expect(page.getByTestId("submission")).toContainText("record_id, action, active_policy_id");
  await expect(page.getByTestId("submission").getByRole("link", { name: "policies.csv" })).toBeVisible();
    await page.screenshot({ path: resolve(SHOTS, "data-manager-run.png"), fullPage: true });

    await page.getByRole("button", { name: "VALIDATE OUTPUTS" }).click();
    await expect(page.getByTestId("validation")).toContainText("Every rule passed");

    await expect(page.getByRole("button", { name: "EXPORT ALL OUTPUTS" })).toBeDisabled();
    await page.getByRole("button", { name: "CHOOSE OUTPUT FOLDER" }).click();
    await expect(page.getByTestId("output-folder")).toHaveText("chosen-output");
    await page.getByRole("button", { name: "EXPORT ALL OUTPUTS" }).click();
    await expect(page.getByText("Export finished")).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: resolve(SHOTS, "data-manager-export.png"), fullPage: true });

    // What the browser wrote must be byte-for-byte what the backend generated.
    const runId = (await page.getByText(/^Pipeline: RUN_/).textContent())!.replace("Pipeline: ", "");
    const written = await page.evaluate(async (id) => {
      const root = await navigator.storage.getDirectory();
      const dir = await (await root.getDirectoryHandle("chosen-output")).getDirectoryHandle(id);
      const out: Record<string, string> = {};
      for await (const handle of (dir as unknown as { values(): AsyncIterable<FileSystemFileHandle> }).values()) out[handle.name] = await (await handle.getFile()).text();
      return out;
    }, runId);
    expect(Object.keys(written)).toEqual(expect.arrayContaining(["classification.csv", "policies.csv", "replay.csv", "run_summary.json", "validation_report.json", "replay_events.jsonl"]));
    for (const name of Object.keys(written)) {
      expect(written[name], name).toBe(await (await page.request.get(`/api/local-data/runs/${runId}/files/${name}`)).text());
    }
    expect(written["replay.csv"]).toContain(",ALLOW,NONE");
    expect(written["classification.csv"]).toMatch(/,(AGREE|CONFLICT|UNCERTAIN)\n/);
    expect(written["classification.csv"].trim().split("\n")).toHaveLength(20001);
    expect(written["replay.csv"].trim().split("\n")[0]).toBe("record_id,action,active_policy_id");

    // An existing run folder is never overwritten without approval.
    await page.evaluate(async (id) => {
      const root = await navigator.storage.getDirectory();
      const dir = await (await root.getDirectoryHandle("chosen-output")).getDirectoryHandle(id);
      const writable = await (await dir.getFileHandle("policies.csv")).createWritable();
      await writable.write("edited by hand");
      await writable.close();
    }, runId);
    await page.getByRole("button", { name: "EXPORT ALL OUTPUTS" }).click();
    await expect(page.getByText(`${runId} already exists in that folder`)).toBeVisible();
    const kept = await page.evaluate(async (id) => {
      const root = await navigator.storage.getDirectory();
      const dir = await (await root.getDirectoryHandle("chosen-output")).getDirectoryHandle(id);
      return (await (await dir.getFileHandle("policies.csv")).getFile()).text();
    }, runId);
    expect(kept).toBe("edited by hand");
    await page.getByRole("button", { name: `Replace the files in ${runId}` }).click();
    await expect(page.getByText("Export finished")).toBeVisible({ timeout: 30_000 });

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "DOWNLOAD OUTPUT ZIP" }).click();
    expect((await download).suggestedFilename()).toBe(`${runId}_outputs.zip`);
  });

  test("without the folder API: the operating-system picker still works, and outputs still download", async ({ page }) => {
    await page.addInitScript(() => { delete (window as unknown as Record<string, unknown>).showDirectoryPicker; delete (Window.prototype as unknown as Record<string, unknown>).showDirectoryPicker; });
    await page.goto("/data");
    await expect(page.getByText("This browser cannot write to a folder")).toBeVisible();
    await expect(page.getByRole("button", { name: "CHOOSE OUTPUT FOLDER" })).toBeDisabled();
    await page.getByTestId("folder-fallback").setInputFiles(FIXTURE);
    await expect(page.getByRole("cell", { name: "hidden_style_synthetic.csv", exact: true })).toBeVisible();
    await page.getByLabel("Select hidden_style_synthetic.zip").uncheck();
    await chooseType(page, "hidden");
    await runPipeline(page);
    await expect(page.getByTestId("validation")).toContainText("Every rule passed");
    const download = page.waitForEvent("download");
    await page.getByTestId("export-files").getByRole("link", { name: "classification.csv" }).click();
    expect((await download).suggestedFilename()).toBe("classification.csv");
  });

  test("error state: a dataset type the file does not match blocks the run", async ({ page }) => {
    await page.addInitScript(() => { delete (window as unknown as Record<string, unknown>).showDirectoryPicker; delete (Window.prototype as unknown as Record<string, unknown>).showDirectoryPicker; });
    await page.goto("/data");
    await page.getByTestId("folder-fallback").setInputFiles(FIXTURE);
    await page.getByLabel("Select hidden_style_synthetic.zip").uncheck();
    await chooseType(page, "validation");
    await expect(page.getByText("no label column").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "RUN DAY 2 PIPELINE" })).toBeDisabled();
  });
});

test.describe("the real hidden file", () => {
  // E2E_HIDDEN is a folder holding the organisers' file. It is never committed, and only a local run sets it.
  test.skip(!process.env.E2E_HIDDEN || noDataset, "Set E2E_HIDDEN to run this.");
  test.use({ viewport: { width: 1536, height: 1024 } });
  test("upload, run, and watch all five agents in the Command Centre", async ({ page }) => {
    test.setTimeout(240_000);
    await page.addInitScript(() => { delete (window as unknown as Record<string, unknown>).showDirectoryPicker; delete (Window.prototype as unknown as Record<string, unknown>).showDirectoryPicker; });
    await page.goto("/data");
    await page.getByTestId("folder-fallback").setInputFiles(process.env.E2E_HIDDEN!);
    await chooseType(page, "hidden");
    await expect(page.getByText("From this input's timestamps")).toBeVisible();
    await runPipeline(page);
    await expect(page.getByTestId("validation")).toContainText("Every rule passed");
    await expect(page.getByTestId("tradeoff")).toBeVisible();
    await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-data-manager.png"), fullPage: true });
    await page.getByRole("button", { name: "SHOW IN COMMAND CENTRE" }).click();
    await expect(page).toHaveURL(/command/);
    const stage = page.getByTestId("agent-stage");
    await expect(stage).toHaveAttribute("data-yaw", /.+/, { timeout: 20_000 });
    await expect(page.getByTestId("orchestrator-card")).toBeVisible();
    await expect(page.getByLabel("Replay speed")).toHaveValue("2");
    await page.getByRole("button", { name: "Start replay" }).click();
    // The panels must not show the future: this file's first policy comes well after the start.
    await expect(page.getByText("No policies yet")).toBeVisible();
    const facing = new Set<string>();
    let shot = false;
    await expect(async () => {
      const now = await stage.evaluate((el) => ({ ...(el as HTMLElement).dataset }));
      if (now.focus && now.focus !== "none" && Math.abs(Number(now.yaw) - Number(now.yawTarget)) < 0.2) {
        facing.add(now.focus);
        if (now.focus === "A4" && !shot) { shot = true; await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-command.png") }); }
      }
      expect([...facing].sort()).toEqual(["A1", "A2", "A3", "A4", "A5"]);
    }).toPass({ timeout: 60_000, intervals: [100] });
    await expect(page.getByTestId("agent-trace")).not.toContainText("Needs source_ip");
    await expect(page.getByTestId("live-record")).toContainText("F000");
    // The policy is announced when it really activates, and its card explains it.
    await expect(page.getByTestId("policy-banner")).toContainText("POL-00001", { timeout: 60_000 });
    await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-banner.png") });
    // Let the whole file replay, so the policies panel and graph show the finished state.
    await expect(page.getByTestId("replay-state")).toContainText("completed", { timeout: 150_000 });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-command-done.png") });
    const card = page.getByTestId("policy-POL-00001");
    await expect(card).toContainText("172.16.0.1");
    await expect(card).toContainText("F000020");
    await expect(card).toContainText("97");
    await expect(card).toContainText("BRUTE_FORCE");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-ledger.png") });
    await page.goto("/decisions");
    await expect(page.getByRole("heading", { name: "Agent Decisions" })).toBeVisible();
    await expect(page.getByTestId("decision-rows")).toContainText("F000200");
    await page.getByRole("button", { name: /Blocked/ }).click();
    await expect(page.getByTestId("decision-rows")).toContainText("POL-00001");
    await page.getByLabel("Filter by record or source").fill("F000034");
    await page.getByTestId("decision-rows").getByText("F000034").click();
    await expect(page.getByTestId("decision-detail")).toContainText("Its prediction is kept");
    await page.screenshot({ path: resolve(process.env.E2E_SHOTS!, "hidden-decisions.png") });
  });
});

test.describe("no copy of the dataset", () => {
  test.skip(!noDataset, "Run with E2E_NO_DATASET=1 and a config whose eval partition does not exist.");
  test("other screens explain the problem, and the Data Manager still runs the pipeline", async ({ page }) => {
    // Nothing is preloaded: every screen leads to the Data Manager, which asks for data.
    await page.goto("/models");
    await expect(page).toHaveURL(/\/data/, { timeout: 30_000 });
    await expect(page.getByText("Start here: no data is connected")).toBeVisible();
    await page.addInitScript(() => { delete (window as unknown as Record<string, unknown>).showDirectoryPicker; delete (Window.prototype as unknown as Record<string, unknown>).showDirectoryPicker; });
    await page.goto("/data");
    await expect(page.getByRole("heading", { name: "Data Manager" })).toBeVisible();
    await page.getByTestId("folder-fallback").setInputFiles(FIXTURE);
    await page.getByLabel("Select hidden_style_synthetic.zip").uncheck();
    await chooseType(page, "hidden");
    await runPipeline(page);
    await expect(page.getByTestId("validation")).toContainText("Every rule passed");
    // The run is now what every screen reports.
    await expect(page.getByText(/Every screen now shows RUN_/)).toBeVisible();
    await page.getByRole("link", { name: "Model Lab" }).click();
    await expect(page.getByTestId("no-labels")).toBeVisible();
    await expect(page.getByTestId("connected-accuracy")).toHaveCount(0);
    await expect(page.getByTestId("connected-classes")).toContainText("BENIGN");
  });
});
