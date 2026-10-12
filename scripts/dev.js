// 开发服务器启动脚本：npm run dev [-- --host 127.0.0.1 --port 7100]
// 负责拉起 Python (FastAPI/uvicorn) 后端，并把 host/port 参数透传过去。
// 依次尝试多个 Python 解释器来源，避免不同环境下 PATH 里没有 python 导致启动失败。
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

const cwd = path.join(__dirname, "..");
const uvicornArgs = ["-m", "uvicorn", "backend.app:app", "--host", host, "--port", port];

// Python 解释器候选（按优先级）
const candidates = [];
if (process.env.PYTHON_PATH) candidates.push(process.env.PYTHON_PATH);
candidates.push(process.platform === "win32" ? "python" : "python3");
if (process.platform === "win32") candidates.push("py"); // Windows py 启动器
if (process.env.DAIMON_USER_PYTHON) candidates.push(process.env.DAIMON_USER_PYTHON);
candidates.push("python3");

function trySpawn(index) {
  if (index >= candidates.length) {
    console.error("[dev] 错误：找不到可用的 Python 解释器，请先安装 Python 3.10+ 并加入 PATH");
    process.exit(1);
  }
  const cmd = candidates[index];
  const child = spawn(cmd, cmd === "py" ? ["-3", ...uvicornArgs] : uvicornArgs, {
    cwd,
    stdio: "inherit",
  });
  let settled = false;
  const fallback = (why) => {
    if (settled) return;
    settled = true;
    console.error(`[dev] ${cmd} 启动失败（${why}），尝试下一个候选…`);
    trySpawn(index + 1);
  };
  child.on("error", (err) => fallback(err.code || err.message));
  child.on("exit", (code, signal) => {
    if (settled) return;
    settled = true;
    if (signal) return; // 被信号终止（如 SIGINT），不再回退
    if (code === 0) process.exit(0);
    console.error(`[dev] ${cmd} 退出码 ${code}`);
    trySpawn(index + 1);
  });
  function shutdown() {
    try { child.kill("SIGINT"); } catch {}
  }
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

trySpawn(0);
