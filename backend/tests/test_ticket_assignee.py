"""Bearbeiter und Vorgangstyp beim Ticket-Anlegen — an den echten Fällen IMSP/Scholtysik.

Auf Jira ist Scholtysik eindeutig (jscholtysik); im ServiceDesk gibt es ihn zweimal
(E-Mail-Konto und mujscholtysik). IMSP kennt keine "Serviceanfrage", nur "Projektticket "
— mit Leerzeichen am Ende des Namens in Jira.
"""
from __future__ import annotations

from app.services.ticketing import pick_assignee, pick_issue_type

JIRA = [{"name": "jscholtysik", "display_name": "Scholtysik, Joachim", "email": ""}]
SD = [{"name": "Joachim.Scholtysik@merkur.de", "display_name": "Joachim.Scholtysik@merkur.de", "email": ""},
      {"name": "mujscholtysik", "display_name": "Scholtysik, Joachim", "email": ""}]


def test_single_candidate_is_taken():
    assert pick_assignee(JIRA, "Scholtysik") == ("jscholtysik", "")


def test_exact_login_wins_among_several():
    assert pick_assignee(SD, "mujscholtysik") == ("mujscholtysik", "")
    assert pick_assignee(SD, "MUJSCHOLTYSIK")[0] == "mujscholtysik"


def test_ambiguous_name_is_not_guessed():
    login, err = pick_assignee(SD, "Scholtysik")
    assert login is None
    assert "mehrdeutig" in err and "mujscholtysik" in err


def test_no_assignable_person_names_the_reason():
    login, err = pick_assignee([], "Niemand")
    assert login is None and "Kein zuweisbarer Bearbeiter" in err


def test_no_assignee_requested():
    assert pick_assignee(JIRA, "") == (None, "")


IMSP = ["Projektticket ", "Unterprojekt"]


def test_missing_type_falls_back_and_says_so():
    t, hint = pick_issue_type(IMSP, "Serviceanfrage")
    assert t == "Projektticket "                  # exakter Jira-Name, mit Leerzeichen
    assert "Serviceanfrage" in hint and "Projektticket" in hint


def test_type_matches_ignoring_case_and_trailing_space():
    assert pick_issue_type(IMSP, "projektticket") == ("Projektticket ", "")


def test_no_type_requested_picks_a_preferred_one_silently():
    assert pick_issue_type(["Bug", "Aufgabe"], None) == ("Aufgabe", "")


import pytest  # noqa: E402

from app.services.ticketing import normalize_target  # noqa: E402


@pytest.mark.parametrize("said,target", [
    ("Jira", "jira"), ("ServiceDesk", "jira_sd"), ("Service Desk", "jira_sd"),
    ("servicedesk", "jira_sd"), ("SD", "jira_sd"), ("jira_sd", "jira_sd"),
])
def test_named_target_maps_to_the_instance(said, target):
    assert normalize_target(said) == (target, "")


def test_no_target_means_find_it():
    assert normalize_target(None) == (None, "")


def test_unknown_target_is_refused_with_reason():
    t, err = normalize_target("Confluence")
    assert t is None and "Jira oder ServiceDesk" in err
