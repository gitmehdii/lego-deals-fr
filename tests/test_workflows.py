"""The bridge between a GitHub secret and the process that reads it.

A secret is not an environment variable until the workflow names it in `env:`.
Nothing fails when one is missing: `Settings.webhook_for` falls back to the
catch-all by design, so a channel whose secret was never plumbed through goes
on posting to the wrong room while `alerts.channel_id` records the right one.

That happened. Five per-channel secrets were created, the routing was correct
in the database, and every message went to the single original channel for
five days. This file is why it cannot happen quietly again.
"""

from pathlib import Path

import pytest
import yaml

from bricks.config import Settings
from bricks.core.channels import CHANNELS

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def _env_of(workflow: str) -> dict[str, str]:
    spec = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    (job,) = spec["jobs"].values()
    return job.get("env", {})


def test_the_ingest_workflow_passes_every_webhook():
    """One `env:` line per channel, or that channel silently uses the catch-all."""
    passed = _env_of("ingest.yml")
    expected = {f"DISCORD_WEBHOOK_{channel.upper()}" for channel in CHANNELS}

    missing = sorted(expected - passed.keys())
    assert not missing, (
        f"{missing} exist as Settings fields but ingest.yml never names them, "
        "so their deals would fall back to DISCORD_WEBHOOK_URL"
    )


@pytest.mark.parametrize("workflow", ["ingest.yml", "catalogue.yml"])
def test_every_variable_passed_is_one_the_settings_read(workflow):
    """The other direction: a typo in a name here is dead weight nobody reads."""
    unknown = [
        name for name in _env_of(workflow) if name.lower() not in Settings.model_fields
    ]
    assert not unknown, (
        f"{workflow} passes variables no Settings field reads: {unknown}"
    )


@pytest.mark.parametrize("workflow", ["ingest.yml", "catalogue.yml"])
def test_a_variable_is_only_ever_fed_from_a_secret(workflow):
    """Credentials come from the secret store, never inline in the file."""
    inline = [
        name
        for name, value in _env_of(workflow).items()
        if "secrets." not in str(value)
    ]
    assert not inline, f"{workflow} sets {inline} without going through secrets"


def test_the_database_url_reaches_both_workflows():
    """Neither command can do anything without it, and a runner's disk is
    wiped between runs."""
    for workflow in ("ingest.yml", "catalogue.yml"):
        assert "DATABASE_URL" in _env_of(workflow), workflow
