# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Release profiles (R9): the version-specific variants the engine consults.

A profile is data (resources/release_profiles.json). Its status is:

validated  a lab validation for the release has been recorded in the journal and passed;
untested   no profile for the release, or a profile with no recorded validation.

An untested release is read only: inventory, checks and reports run, and every step that
changes the cloud is refused (require_mutation_allowed).
"""
import json

from . import paths

VALIDATION_KIND = "validation"


class ReadOnlyRelease(Exception):
    """Raised when a step that changes the cloud is attempted on an untested release."""


def load_profiles():
    with open(paths.resource_dir() / "release_profiles.json", encoding="utf-8") as fh:
        return json.load(fh)["profiles"]


def record_validation(journal, release, passed, evidence=None):
    """Record the outcome of a lab validation (gate G0) for a release."""
    journal.record(VALIDATION_KIND, release, {"release": release, "result": "passed" if passed else "failed",
                                              "evidence": evidence or {}})


def status_for(inference, journal=None, profiles=None):
    """Return {'status', 'reason', 'release', 'variants'} for an inferred release."""
    profiles = profiles if profiles is not None else load_profiles()
    release = inference.get("release")
    if release is None:
        return {"status": "untested", "release": None, "variants": {},
                "reason": "the release could not be identified (%s)" % inference.get("confidence")}
    profile = profiles.get(release)
    if profile is None:
        return {"status": "untested", "release": release, "variants": {},
                "reason": "no profile exists for %s" % release}
    if inference.get("confidence") in ("disagree", "newer_than_known", "unknown"):
        return {"status": "untested", "release": release, "variants": profile.get("variants", {}),
                "reason": "the release reading is not reliable (%s)" % inference.get("confidence")}
    latest = journal.latest(VALIDATION_KIND, release) if journal is not None else None
    if latest is None or latest["data"].get("result") != "passed":
        return {"status": "untested", "release": release, "variants": profile.get("variants", {}),
                "reason": "a profile exists for %s but no passed lab validation is recorded (gate G0)" % release}
    return {"status": "validated", "release": release, "variants": profile.get("variants", {}),
            "reason": "lab validation passed on %s" % latest["ts"]}


def require_mutation_allowed(profile_status):
    if profile_status.get("status") != "validated":
        raise ReadOnlyRelease("this OpenStack release is %s and read only: %s" % (
            profile_status.get("status"), profile_status.get("reason")))
