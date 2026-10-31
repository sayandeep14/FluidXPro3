"""`fluid package` (ROADMAP E-19): Dockerfile, app.pyz, .env.example; never secrets."""

from __future__ import annotations

import subprocess
import sys

from fl_helpers import Recorder, project, reply, run_build

from fluid.cli import main as fluid_main
from fluid.package import dockerfile, env_names

API = '''import Server;
import Env;
function[<Request>|<Response>] health = Function(Request r) => Response.text("ok");
function[<Request>|<Response>] hello = Function(Request r) => Response.text("hi " + (Env.get("GREETING_NAME") ?? "you"));
function[<>|<>] @entry main = Function() {
    Server s = Server.create(port = 8080);
    s.get("/health", health);
    s.get("/", hello);
    s.keepRunning();
}
'''


def test_package_a_program(tmp_path, capfd):
    src = tmp_path / "api.fx3"
    src.write_text(API)
    (tmp_path / "secrets.env").write_text("SHOP_TOKEN=very-secret-value\n")
    assert fluid_main(["package", str(src)]) == 0
    out = tmp_path / "build" / "package"
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert {"Dockerfile", ".dockerignore", ".env.example", "app.pyz", "app/api.py", "app/fx3rt.py",
            "app/fx3std.py", "app/requirements.txt"} <= files
    everything = "".join(p.read_bytes().decode("latin-1") for p in out.rglob("*") if p.is_file())
    assert "very-secret-value" not in everything and "secrets.env" not in files
    env = (out / ".env.example").read_text()
    assert "GREETING_NAME=" in env and "SERVER_PORT=" in env and "SERVER_HOST=" in env
    docker = (out / "Dockerfile").read_text()
    assert "USER app" in docker and "SERVER_HOST=0.0.0.0" in docker and "/health" in docker
    assert 'CMD ["python", "api.py"]' in docker
    assert "pip install" not in docker                 # nothing outside the standard library is needed


def test_the_pyz_runs(tmp_path, capfd):
    src = tmp_path / "hello.fx3"
    src.write_text('import Math;\nprint("root", Math.sqrt(16.0));\n')
    assert fluid_main(["package", str(src), "-o", str(tmp_path / "pkg")]) == 0
    proc = subprocess.run([sys.executable, str(tmp_path / "pkg" / "app.pyz")], capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == "root 4.0", proc.stderr


def test_a_fluid_program_is_packaged_with_its_secret_names(tmp_path, capfd):
    fluid = project(tmp_path).read_text() + "\nsecrets\n    REPORT_API_KEY for the report service\n"
    f = project(tmp_path, fluid)
    r = reply()
    r["blocks"].append({"id": "secrets", "fx3": ""})          # read where it's used; nothing to declare
    assert run_build(f, Recorder([r])).ok
    capfd.readouterr()
    assert fluid_main(["package", str(f), "--offline"]) == 0
    env = (tmp_path / "build" / "package" / ".env.example").read_text()
    assert "REPORT_API_KEY=    # secret" in env
    assert (tmp_path / "build" / "package" / "app" / "grades.py").exists()


def test_unverified_code_is_not_packaged(tmp_path, capfd):
    f = project(tmp_path, config='{"verification": {"examples": "off"}}')
    assert run_build(f, Recorder([reply()])).ok
    assert fluid_main(["package", str(f), "--offline"]) == 1
    assert "not packaging" in capfd.readouterr().err
    assert fluid_main(["package", str(f), "--offline", "--trust"]) == 0


def test_env_names_and_dockerfile():
    names = dict(env_names('Env.require("DB_URL"); Ai.ask(x); Schedule.every(1.0, f)', ["API_TOKEN"]))
    assert names["API_TOKEN"] == "secret" and names["ANTHROPIC_API_KEY"] == "secret"
    assert "DB_URL" in names and "SCHEDULE_STATE" in names and "SERVER_PORT" not in names
    plain = dockerfile("main.py", requirements=True, server=False, health=False)
    assert "pip install" in plain and "EXPOSE" not in plain and "HEALTHCHECK" not in plain


def test_healthchecks_are_valid_docker_json():
    import json
    for health in (True, False):
        line = next(x for x in dockerfile("main.py", False, True, health).splitlines() if x.startswith("HEALTHCHECK"))
        cmd = json.loads(line.split("CMD ", 1)[1])
        assert cmd[:2] == ["python", "-c"]
        compile(cmd[2], "<healthcheck>", "exec")
