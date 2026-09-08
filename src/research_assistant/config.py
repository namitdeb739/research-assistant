"""What a project records about itself, and how it is found.

A vault path and a contact address are configuration, not credentials. They used
to reach the CLI through the environment, which in a repo meant `.env` — the
secrets convention, denied wholesale by sandboxes and loaded only by an
interactive shell's direnv hook. Pooling configuration with secrets gave it their
access rules. One tool's typed settings in their own file can be read without
exposing anything, which an open-ended file of exports cannot promise.

The file is found from the working directory upwards, so a command works from
anywhere inside a project.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Final

NAME: Final = ".research-assistant.toml"
DEFAULT_USER_AGENT: Final = "research-assistant/0.1"


class ConfigError(Exception):
    """The project config exists but cannot be used."""


def path() -> Path | None:
    """The nearest project config, from the working directory upwards."""
    here = Path.cwd()
    for folder in (here, *here.parents):
        if (candidate := folder / NAME).is_file():
            return candidate
    return None


def _load() -> tuple[Path, dict[str, Any]] | None:
    found = path()
    if found is None:
        return None
    try:
        return found, tomllib.loads(found.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{found}: {exc}") from exc


def _string(key: str) -> tuple[Path, str] | None:
    """The value of a string key, with the file that recorded it."""
    loaded = _load()
    if loaded is None:
        return None
    found, config = loaded
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{found}: {key} must be a string.")
    return found, value


def papers_dir() -> Path | None:
    """The vault folder the project records, if it records one."""
    recorded = _string("papers_dir")
    if recorded is None:
        return None
    found, value = recorded
    # Against the file, not the working directory: the same config has to mean
    # the same folder from anywhere in the project.
    return found.parent / Path(value).expanduser()


def user_agent() -> str:
    """The User-Agent for the Crossref and OpenAlex polite pools.

    The address must be the caller's own, so there is no shared literal to bake
    in: that would make everyone's traffic look like one user's. Falling back to
    the default is the anonymous pool, which is slower but not an error.
    """
    from_env = os.getenv("RESEARCH_ASSISTANT_USER_AGENT")
    if from_env:
        return from_env
    recorded = _string("user_agent")
    return DEFAULT_USER_AGENT if recorded is None else recorded[1]
