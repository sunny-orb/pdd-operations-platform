import { existsSync } from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const api = path.join(root, "apps/api");
const localPython = path.join(
  api,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const python = existsSync(localPython) ? localPython : process.env.PYTHON || "python3";
const commands = {
  dev: ["uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
  check: ["ruff", "check", "."],
  "schema:check": [path.join(root, "scripts/render-db-schema.py"), "--check"],
  "schema:render": [path.join(root, "scripts/render-db-schema.py")],
  test: ["pytest", "-q"],
  "db:up": [],
  "db:test": [],
  "db:status": [],
  "sync:forecast": ["app.jobs.forecast_sop"],
  "sync:m1": ["app.jobs.sync_product_board"],
};
const command = commands[process.argv[2]];
if (!command) throw new Error("不支持的 API 命令");
const localDatabaseActions = {
  "db:up": "up",
  "db:test": "test",
  "db:status": "status",
};
const localAction = localDatabaseActions[process.argv[2]];
const args = localAction
  ? [path.join(root, "scripts/db-local.py"), localAction, ...process.argv.slice(3)]
  : [
      ...(process.argv[2].startsWith("schema:") ? [] : ["-m"]),
      ...command,
      ...process.argv.slice(3),
    ];
const child = spawn(python, args, { cwd: api, stdio: "inherit" });
child.on("error", () => {
  console.error("无法启动 Python，请按 README 配置 Python 3.12+ 和后端依赖。");
  process.exitCode = 1;
});
child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
