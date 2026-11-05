"""Explicit Docker acceptance checks. Run manually after building the sandbox image."""
import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.core.config import AgentConfig
from agent.core.orchestrator import Orchestrator
from agent.planning.replanner import Replanner
from agent.state.manager import StateManager
from agent.tools import REGISTRY
from agent.tools.sandbox import SandboxTimeout, run_isolated
from examples.demo_cross_source import CrossSourcePlanner, prepare


def main():
    output = Path(tempfile.mkdtemp(prefix="m3_acceptance_", dir=ROOT / "outputs"))
    source = output / "source.csv"
    source.write_text("a\n1\n2\n", encoding="utf-8")
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    results = []

    def check(name, action):
        try:
            detail = action()
            results.append({"case": name, "passed": True, "detail": detail})
        except Exception as exc:
            results.append({"case": name, "passed": False, "error": str(exc)})
        payload = {
            "image": subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", "autodataagent-sandbox:local"],
                text=True).strip(),
            "agent_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in sorted((ROOT / "agent").rglob("*.py"))},
            "results": results,
        }
        (output / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(name, results[-1]["passed"], flush=True)

    def isolation():
        # This host sentinel is outside both mounts.
        sentinel = output / "grader-only.txt"
        sentinel.write_text("hidden", encoding="utf-8")
        code = """
import os, socket
from pathlib import Path
assert not os.environ.get("OPENAI_API_KEY")
assert not os.environ.get("OPENCODE_API_KEY")
assert not Path(HOST_SENTINEL).exists()
assert not Path("/var/run/docker.sock").exists()
try:
    Path("/input/data.csv").write_text("changed")
except OSError:
    pass
else:
    raise AssertionError("input mount is writable")
try:
    Path("/root-write-test").write_text("changed")
except OSError:
    pass
else:
    raise AssertionError("root filesystem is writable")
sock = socket.socket()
sock.settimeout(1)
assert sock.connect_ex(("1.1.1.1", 443)) != 0
sock.close()
assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "536870912"
assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "64"
quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
assert int(quota) == int(period)
result = df.assign(a=df.a * 2)
""".replace("HOST_SENTINEL", repr(str(sentinel.resolve())))
        target = output / "isolated.csv"
        run_isolated(code, source, target, 45, backend="docker")
        assert list(csv.DictReader(target.open())) == [{"a": "2"}, {"a": "4"}]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hash
        return "read-only input/root, hidden grader, network, credentials and cgroup limits checked"

    def timeout():
        before = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
        try:
            run_isolated("import time; time.sleep(30); result=df", source,
                         output / "timeout.csv", 4, backend="docker")
        except SandboxTimeout:
            pass
        else:
            raise AssertionError("timeout did not fire")
        after = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines()
        assert set(after) <= set(before)
        return "timed-out container removed"

    def host_crash():
        before = set(subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines())
        script = (
            "from agent.tools.sandbox import run_isolated; "
            "run_isolated('import time; time.sleep(60); result=df', "
            + repr(str(source)) + ", " + repr(str(output / "host-crash.csv"))
            + ", 6, backend='docker')"
        )
        child = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        observed = set()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and child.poll() is None:
                active = set(subprocess.check_output(
                    ["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines())
                observed = {name for name in active - before if name.startswith("ada-")}
                if observed:
                    break
                time.sleep(0.2)
            assert observed, "host exited before a running container was observed"
            child.kill()
            child.wait(timeout=5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                active = set(subprocess.check_output(
                    ["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines())
                if not observed & active:
                    return "container-side timer removed the container after host termination"
                time.sleep(0.2)
            raise AssertionError("container survived its independent deadline")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for name in observed:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)

    def cross_source():
        data = output / "data"
        spec = prepare(data)

        class Planner(CrossSourcePlanner):
            def plan(self, goal, ctx):
                graph = super().plan(goal, ctx)
                step = graph.get("aggregate")
                step.tool = "python_exec"
                step.args = {"input": "joined", "name": "sales_by_region",
                             "code": "result = df.groupby('region', as_index=False)['amount'].sum()"}
                return graph

        config = AgentConfig(data_root=str(data), outputs_root=str(output / "runs"),
                             sandbox_backend="docker")
        agent = Orchestrator(Planner(), Replanner(), REGISTRY, StateManager.new_run, config)
        summary = agent.run(spec)
        assert summary.status == "finished", summary.trace_path
        art = next(a for a in summary.artifacts if a["name"] == "sales_by_region")
        with (Path(summary.run_dir) / art["path"]).open() as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2 and {r["region"]: float(r["amount"]) for r in rows} == {"East": 85, "West": 70}
        assert "policy.md:2" in Path(summary.report_path).read_text(encoding="utf-8")
        restored = agent.resume(summary.run_dir)
        assert restored.status == "finished"
        return {"run_dir": summary.run_dir, "report": summary.report_path}

    check("docker_isolation", isolation)
    check("docker_timeout_cleanup", timeout)
    check("host_crash_container_deadline", host_crash)
    check("cross_source_docker_and_resume", cross_source)
    print(output)
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
