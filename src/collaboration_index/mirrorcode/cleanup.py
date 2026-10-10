"""Preserve grading cleanup when a concurrent process removes an observed descendant."""

import hashlib

from inspect_ai.util import SandboxEnvironment

CLEANUP_LINE = "            shutil.rmtree(item)"
CLEANUP_REPAIR = '''            def missing_descendant(function, path, error):
                """Accept vanished cleanup paths while retaining all other filesystem errors."""
                if not isinstance(error, FileNotFoundError):
                    raise error

            shutil.rmtree(item, onexc=missing_descendant)'''


def repair_cleanup_script(content: str) -> str:
    """Adapt only the known upstream directory cleanup call without mutating its package."""
    if content.count(CLEANUP_LINE) != 1:
        raise RuntimeError("Unsupported MirrorCode grading cleanup script")
    return content.replace(CLEANUP_LINE, CLEANUP_REPAIR, 1)


def install_cleanup_repair() -> str:
    """Adapt every reference and agent scoring context using the same validated script."""
    import mc.scorer as scorer
    from mc.scorer import SandboxScoringContext

    original = scorer.SandboxScoringContext
    if getattr(original, "team_cleanup_repair", False):
        return original.team_cleanup_script_sha256
    script = (scorer.PROJECT_ROOT / "mc/_data/batch_score_test_cases.py").read_text()
    digest = hashlib.sha256(repair_cleanup_script(script).encode()).hexdigest()

    class CleanupScoringContext(SandboxScoringContext):
        """Retain upstream context lifecycle while repairing its outgoing cleanup script."""

        team_cleanup_repair = True
        team_cleanup_script_sha256 = digest

        def __init__(
            self,
            sandbox_env: SandboxEnvironment,
            script_content: str,
            test_cases_json: str,
            cmd_json: str,
        ) -> None:
            """Validate and repair the script before upstream writes grading inputs."""
            super().__init__(
                sandbox_env,
                repair_cleanup_script(script_content),
                test_cases_json,
                cmd_json,
            )

    scorer.SandboxScoringContext = CleanupScoringContext
    return digest
