"""Choose a delivery strategy and forge from the payload and config."""

from __future__ import annotations

from wiab_team.config import Config
from wiab_team.delivery.local import LocalDelivery
from wiab_team.delivery.protocol import DeliveryStrategy
from wiab_team.errors import ConfigError
from wiab_team.forge.protocol import Forge
from wiab_team.models.input import DeliveryKind, ForgeKind, TeamRunInput


def build_delivery(payload: TeamRunInput, config: Config) -> DeliveryStrategy:
    if payload.delivery is DeliveryKind.LOCAL:
        return LocalDelivery()

    if payload.delivery is DeliveryKind.PUSH:
        from wiab_team.delivery.push import PushDelivery

        return PushDelivery()

    from wiab_team.delivery.pr import PullRequestDelivery

    return PullRequestDelivery(build_forge(payload, config))


def build_forge(payload: TeamRunInput, config: Config) -> Forge:
    repo = payload.repo
    if not config.git_token:
        raise ConfigError(
            f"delivery={payload.delivery.value} needs WIAB_TEAM_GIT_TOKEN "
            f"to authenticate against the {repo.forge.value} forge"
        )

    if repo.forge is ForgeKind.GITHUB:
        from wiab_team.forge.github import GitHubForge

        assert repo.github_repo is not None  # guaranteed by RepoRef validation
        return GitHubForge(repo=repo.github_repo, token=config.git_token)

    if repo.forge is ForgeKind.WORKINABOX:
        from wiab_team.forge.workinabox import WorkinaboxForge

        assert repo.workinabox_api_url is not None
        assert repo.workinabox_repo_id is not None
        return WorkinaboxForge(
            api_url=repo.workinabox_api_url,
            repo_id=repo.workinabox_repo_id,
            token=config.git_token,
        )

    raise ConfigError(f"cannot open a pull request with forge={repo.forge.value}")
