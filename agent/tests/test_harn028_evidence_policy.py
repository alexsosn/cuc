"""HARN-028a RED gate: evidence policy identity for ablation arms."""

from __future__ import annotations

import importlib
import json

import pytest


def _policy_module():
    try:
        return importlib.import_module("harness.evidence_policy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-028 evidence policy is not implemented yet: {exc}")


SHA_A = "a" * 64
SHA_B = "b" * 64


def _availability(mod, source_id: str, *, enabled=True, available=True, kind="sibling", digest=SHA_A):
    return mod.ResourceAvailability(
        source_id=source_id,
        enabled=enabled,
        available=available,
        locator_kind=kind,
        resource_sha256=digest if available else None,
    )


def _policy(mod, enabled: tuple[str, ...], availability=None):
    if availability is None:
        availability = tuple(
            _availability(mod, source_id, kind="repository" if source_id == "auto-parsing" else "sibling")
            for source_id in enabled
        )
    return mod.EvidencePolicy(enabled_sources=enabled, availability=availability)


def test_known_source_ids_match_the_ticket_and_exclude_translations() -> None:
    mod = _policy_module()
    assert mod.ALL_SOURCE_IDS == (
        "auto-parsing",
        "dulat",
        "tropper",
        "eupt",
        "legacy-review",
        "corpus-parallels",
        "burns-cultic-vocabulary",
    )
    assert "published-translations" not in mod.ALL_SOURCE_IDS


def test_auto_parsing_cannot_be_disabled_and_unknown_ids_are_rejected() -> None:
    mod = _policy_module()
    with pytest.raises(ValueError, match="auto-parsing"):
        _policy(mod, ("dulat",))
    with pytest.raises(ValueError, match="unknown|source"):
        _policy(mod, ("auto-parsing", "published-translations"))
    with pytest.raises(ValueError, match="duplicate"):
        _policy(mod, ("auto-parsing", "dulat", "dulat"))


def test_availability_must_cover_exactly_the_enabled_sources() -> None:
    mod = _policy_module()
    with pytest.raises(ValueError, match="availability"):
        mod.EvidencePolicy(
            enabled_sources=("auto-parsing", "dulat"),
            availability=(_availability(mod, "auto-parsing", kind="repository"),),
        )
    with pytest.raises(ValueError, match="availability"):
        mod.EvidencePolicy(
            enabled_sources=("auto-parsing",),
            availability=(
                _availability(mod, "auto-parsing", kind="repository"),
                _availability(mod, "dulat"),
            ),
        )


def test_absent_resource_is_recorded_without_digest_and_without_path() -> None:
    mod = _policy_module()
    absent = mod.ResourceAvailability(
        source_id="tropper", enabled=True, available=False, locator_kind="absent", resource_sha256=None
    )
    assert absent.to_dict() == {
        "source_id": "tropper",
        "enabled": True,
        "available": False,
        "locator_kind": "absent",
        "resource_sha256": None,
    }
    with pytest.raises(ValueError, match="digest|available"):
        mod.ResourceAvailability("tropper", True, False, "absent", SHA_A)
    with pytest.raises(ValueError, match="digest|available"):
        mod.ResourceAvailability("tropper", True, True, "sibling", None)
    with pytest.raises(ValueError, match="locator_kind"):
        mod.ResourceAvailability("tropper", True, True, "/Users/someone/dulat.sqlite", SHA_A)


def test_policy_hash_changes_with_enabled_set_and_with_resource_version() -> None:
    mod = _policy_module()
    base = _policy(mod, ("auto-parsing", "dulat"))
    fewer = _policy(mod, ("auto-parsing",))
    other_version = mod.EvidencePolicy(
        enabled_sources=("auto-parsing", "dulat"),
        availability=(
            _availability(mod, "auto-parsing", kind="repository"),
            _availability(mod, "dulat", digest=SHA_B),
        ),
    )
    same_again = _policy(mod, ("auto-parsing", "dulat"))

    assert len(base.sha256) == 64
    assert base.sha256 == same_again.sha256
    assert base.sha256 != fewer.sha256
    assert base.sha256 != other_version.sha256


def test_enabled_but_absent_resource_hashes_differently_from_present_resource() -> None:
    mod = _policy_module()
    present = _policy(mod, ("auto-parsing", "dulat"))
    absent = mod.EvidencePolicy(
        enabled_sources=("auto-parsing", "dulat"),
        availability=(
            _availability(mod, "auto-parsing", kind="repository"),
            _availability(mod, "dulat", available=False, kind="absent"),
        ),
    )
    assert present.sha256 != absent.sha256
    assert absent.available_sources == ("auto-parsing",)
    assert absent.absent_sources == ("dulat",)


def test_policy_json_round_trip_and_order_normalisation() -> None:
    mod = _policy_module()
    policy = mod.EvidencePolicy(
        enabled_sources=("dulat", "auto-parsing"),
        availability=(
            _availability(mod, "dulat"),
            _availability(mod, "auto-parsing", kind="repository"),
        ),
    )
    # Canonical order follows ALL_SOURCE_IDS regardless of input order.
    assert policy.enabled_sources == ("auto-parsing", "dulat")
    payload = json.loads(policy.to_json())
    restored = mod.EvidencePolicy.from_json(policy.to_json())
    assert restored == policy
    assert restored.sha256 == policy.sha256
    assert set(payload) == {"enabled_sources", "availability"}
    # No filesystem path can appear in the serialised policy.
    assert "/" not in policy.to_json()
