// 开发服务器启动脚本：npm run dev [-- --host 127.0.0.1 --port 7100]
// 负责拉起 Python (FastAPI/uvicorn) 后端，并把 host/port 参数透传过去。
const { spawn } = require("child_process");
const path = require("path");

const args = process.argv.slice(2);
function argVal(name, dflt) {
  const i = args.indexOf(name);
  if (i >= 0 && args[i + 1]) return args[i + 1];
  const p = args.find((a) => a.startsWith(name + "="));
  if (p) return p.split("=").slice(1).join("=");
  return dflt;
}
const host = argVal("--host", "127.0.0.1");
const port = argVal("--port", "7100");

const py = process.platform === "win32" ? "python" : "python3";
const child = spawn(
  py,
  ["-m", "uvicorn", "backend.app:app", "--host", host, "--port", port],
  { cwd: path.join(__dirname, ".."), stdio: "inherit" }
);

function shutdown() {
  try { child.kill("SIGINT"); } catch {}
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
child.on("exit", (code) => process.exit(code == null ? 0 : code));
