"""Verify protected Hawk credential forwarding and duplicate-launch prevention."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Load the launcher with isolated fake Hawk and keyring services."""
    calls: list[dict[str, object]] = []
    credentials = {"refresh_token": "fixture-runner-refresh"}
    jobs: list[dict[str, str]] = []

    class SessionKeyring:
        """Represent the required process-local backend without accessing the OS."""

    class Client:
        """Record Hawk API operations without performing network requests."""

        def __init__(self, **kwargs: object) -> None:
            """Keep connection parameters inside the harmless fixture."""
            self.parameters = kwargs

        async def __aenter__(self) -> "Client":
            """Open the fake asynchronous API context."""
            return self

        async def __aexit__(self, *args: object) -> None:
            """Close the fake asynchronous API context."""

        async def get_jobs(self, **kwargs: object) -> list[dict[str, str]]:
            """Return the fixture's owned lifecycle records."""
            return jobs

        async def create_eval_set(self, **kwargs: object) -> str:
            """Record one submission and return a synthetic job identifier."""
            calls.append(kwargs)
            return "fixture-run-unique"

    class Config:
        """Stand in for schema validation in focused launcher unit tests."""

        @staticmethod
        def model_validate(value: object) -> object:
            """Accept the minimal harmless launch configuration."""
            return value

    async def authenticate() -> str:
        """Simulate access-token renewal and refresh-token rotation."""
        credentials["refresh_token"] = "fixture-rotated-refresh"
        return "fixture-access"

    keyring = ModuleType("keyring")
    keyring.get_keyring = SessionKeyring
    modules = {
        "keyring": keyring,
        "hawk": ModuleType("hawk"),
        "hawk.cli": SimpleNamespace(tokens=SimpleNamespace(get=credentials.get)),
        "hawk.cli.util": ModuleType("hawk.cli.util"),
        "hawk.cli.util.auth": SimpleNamespace(ensure_logged_in=authenticate),
        "hawk.client": SimpleNamespace(HawkClient=Client),
        "hawk.core": ModuleType("hawk.core"),
        "hawk.core.types": SimpleNamespace(EvalSetConfig=Config),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_cyber_eval.py"
    spec = importlib.util.spec_from_file_location("cyber_launcher_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return SimpleNamespace(
        module=module, calls=calls, credentials=credentials, jobs=jobs
    )


def plan(
    launcher: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Path:
    """Prepare one synthetic plan and inject its command-line arguments."""
    path = tmp_path / "cyber-eval.yaml"
    path.write_text("name: fixture-run\n")
    monkeypatch.setattr(
        launcher.module,
        "parse_args",
        lambda: SimpleNamespace(config=path, dry_run=False, resume_pending=False),
    )
    monkeypatch.setattr(
        launcher.module, "work_provider_key", lambda: "fixture-work-key"
    )
    return path


async def test_submission_forwards_runner_refresh(launcher: SimpleNamespace) -> None:
    """Send infrastructure refresh auth separately from the model-provider key."""
    config = {"name": "fixture-run"}
    identity = await launcher.module.submit_with_hawk_api(
        "fixture-access", config, "fixture-work", "fixture-refresh"
    )
    assert identity == "fixture-run-unique"
    assert launcher.calls == [
        {
            "eval_set_config": config,
            "secrets": {"OPENROUTER_API_KEY": "fixture-work"},
            "refresh_token": "fixture-refresh",
        }
    ]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_refresh_fails_closed(
    launcher: SimpleNamespace, value: str | None
) -> None:
    """Reject missing runner credentials before any submission can occur."""
    launcher.credentials["refresh_token"] = value
    with pytest.raises(RuntimeError, match="runner refresh credential"):
        launcher.module.runner_refresh_token()
    assert launcher.calls == []


async def test_blank_refresh_never_submits(launcher: SimpleNamespace) -> None:
    """Prevent direct callers from submitting without refresh authentication."""
    with pytest.raises(RuntimeError, match="runner refresh credential"):
        await launcher.module.submit_with_hawk_api("access", {}, "work", " ")
    assert launcher.calls == []


def test_unprotected_backend_is_rejected_before_reading_tokens(
    launcher: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prevent refresh-token access from falling through to the OS keyring."""
    monkeypatch.setattr(launcher.module.keyring, "get_keyring", object)
    with pytest.raises(RuntimeError, match="protected memory-only"):
        launcher.module.runner_refresh_token()


async def test_rotated_refresh_is_forwarded_without_persisting_secrets(
    launcher: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Use the refreshed session credential while keeping receipts and output safe."""
    path = plan(launcher, monkeypatch, tmp_path)
    await launcher.module.main()
    assert launcher.calls[0]["refresh_token"] == "fixture-rotated-refresh"
    receipt_text = path.with_suffix(".receipt.json").read_text()
    output = capsys.readouterr().out
    assert json.loads(receipt_text)["state"] == "submitted"
    for secret in ("fixture-access", "fixture-rotated-refresh", "fixture-work-key"):
        assert secret not in receipt_text + output


async def test_missing_refresh_leaves_no_pending_receipt(
    launcher: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep a failed credential preflight from creating a paid-submission intent."""
    path = plan(launcher, monkeypatch, tmp_path)

    async def authenticate_without_refresh() -> str:
        """Simulate a login session lacking the runner refresh credential."""
        launcher.credentials["refresh_token"] = None
        return "fixture-access"

    monkeypatch.setattr(
        launcher.module, "ensure_logged_in", authenticate_without_refresh
    )
    with pytest.raises(RuntimeError, match="runner refresh credential"):
        await launcher.module.main()
    assert not path.with_suffix(".receipt.json").exists()
    assert launcher.calls == []


async def test_truncated_hawk_job_name_blocks_duplicates(
    launcher: SimpleNamespace,
) -> None:
    """Recognize the server's shortened name even when the original label is longer."""
    label = "fixture-unique-long-launch-label"
    launcher.jobs.append({"job_id": label[:24] + "-randomsuffix"})
    assert await launcher.module.owned_job_exists("fixture-access", label)
    assert not await launcher.module.owned_job_exists("fixture-access", "different")


async def test_pending_retry_is_not_mistaken_for_completion(
    launcher: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require reconciliation for an earlier interrupted retry journal."""
    path = plan(launcher, monkeypatch, tmp_path)
    path.with_suffix(".receipt.json").write_text(
        json.dumps(
            {
                "state": "submission_retrying",
                "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    )
    with pytest.raises(RuntimeError, match="Reconcile the pending"):
        await launcher.module.main()
    assert launcher.calls == []
