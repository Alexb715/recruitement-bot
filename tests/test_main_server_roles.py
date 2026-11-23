"""Unit tests for main-server role assignment on join: the pure
`main_server_role_ids` helper that picks which roles an accepted applicant gets,
and the guard keeping the department -> env-var mapping in sync with the
`department` question. The Discord-side grant (permissions, hierarchy, rules
screening) is verified manually in a dev guild."""

from __future__ import annotations

from cogs.interview import main_server_role_ids
from config import DEPARTMENT_ROLE_ENV_VARS
from questions import by_key

ACCESS = 1
DEPT_ROLES = {
    "OPP": 10,
    "Fire/EMS": 20,
    "Civilian Ops": 30,
    "Communications": 40,
}


def test_department_mapping_covers_every_department_choice():
    # A renamed/added choice in questions.py would otherwise silently stop
    # granting that department's role.
    assert set(DEPARTMENT_ROLE_ENV_VARS) == set(by_key("department").choices)


def test_access_role_plus_each_picked_department():
    assert main_server_role_ids(["OPP", "Communications"], ACCESS, DEPT_ROLES) == [
        ACCESS,
        10,
        40,
    ]


def test_access_role_only_when_no_departments():
    assert main_server_role_ids([], ACCESS, DEPT_ROLES) == [ACCESS]
    assert main_server_role_ids(None, ACCESS, DEPT_ROLES) == [ACCESS]


def test_department_roles_without_access_role():
    assert main_server_role_ids(["Fire/EMS"], None, DEPT_ROLES) == [20]


def test_bare_string_department_answer():
    assert main_server_role_ids("Civilian Ops", ACCESS, DEPT_ROLES) == [ACCESS, 30]


def test_unconfigured_or_unknown_departments_are_skipped():
    partial = {"OPP": 10}
    assert main_server_role_ids(["OPP", "Fire/EMS", "Bogus"], None, partial) == [10]


def test_duplicate_role_ids_are_collapsed():
    shared = {"OPP": 10, "Fire/EMS": 10}
    assert main_server_role_ids(["OPP", "Fire/EMS", "OPP"], 10, shared) == [10]


def test_nothing_configured_yields_no_roles():
    assert main_server_role_ids(["OPP"], None, {}) == []
