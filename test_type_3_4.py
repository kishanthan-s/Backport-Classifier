#!/usr/bin/env python3
"""Direct checks for the Type III and Type IV classification paths."""

import tempfile
from pathlib import Path

from Is_namespace_change import compare_action_lists
from is_location_change import location_change_check
from type_5_classification import classify_type_5


SOURCE = """int first() {
    int old = 1;
    return old;
}

int second() {
    int old = 1;
    return old;
}
"""


def update_action(start, new_value):
    return {
        "action": "update-node",
        "node_type": "identifier",
        "value": "old",
        "new_value": new_value,
        "start": start,
        "end": start + len("old"),
    }


def classify_type_3_or_4(main_actions, backport_actions, path, repo_dir):
    if classify_type_5(main_actions, backport_actions) == "Type V":
        return "Type V", {}

    (
        _,
        _,
        _,
        type_3_flags,
        type_5_flags,
        name_changes,
    ) = compare_action_lists(main_actions, backport_actions)

    namespace_changed = (
        any(type_3_flags)
        and any(not unchanged for unchanged in name_changes)
    )

    if any(type_5_flags):
        return "Type V", {
            "type_3_flags": type_3_flags,
            "name_changes": name_changes,
        }

    (
        _,
        function_mismatches,
        unmatched_main,
        unmatched_backport,
    ) = location_change_check(path, main_actions, backport_actions, repo_dir)

    if unmatched_main or unmatched_backport:
        return "Type V", {
            "type_3_flags": type_3_flags,
            "name_changes": name_changes,
            "function_mismatches": function_mismatches,
            "unmatched_main": unmatched_main,
            "unmatched_backport": unmatched_backport,
        }

    location_changed = bool(function_mismatches)
    if location_changed and namespace_changed:
        classification = "Type IV"
    elif namespace_changed:
        classification = "Type III"
    elif location_changed:
        classification = "Type II"
    else:
        classification = "Type I"

    return classification, {
        "type_3_flags": type_3_flags,
        "name_changes": name_changes,
        "function_mismatches": function_mismatches,
    }


def run_case(name, main_start, backport_start, expected, source_path, repo_dir):
    main_actions = [update_action(main_start, "main_name")]
    backport_actions = [update_action(backport_start, "backport_name")]
    classification, details = classify_type_3_or_4(
        main_actions,
        backport_actions,
        source_path,
        repo_dir,
    )

    print(f"{name}: {classification}")
    print(f"  type_3_flags={details.get('type_3_flags')}")
    print(f"  name_changes={details.get('name_changes')}")
    print(f"  function_mismatches={len(details.get('function_mismatches', []))}")

    assert classification == expected, (
        f"{name}: expected {expected}, got {classification}"
    )


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        repo_dir = Path(temp_dir)
        source_path = repo_dir / "sample.c"
        source_path.write_text(SOURCE, encoding="utf-8")

        first_start = SOURCE.index("old")
        second_start = SOURCE.index("old", SOURCE.index("int second()"))

        run_case(
            "namespace change in same function",
            first_start,
            first_start,
            "Type III",
            "sample.c",
            str(repo_dir),
        )
        run_case(
            "namespace change in different functions",
            first_start,
            second_start,
            "Type IV",
            "sample.c",
            str(repo_dir),
        )

    print("Type III and Type IV checks passed.")


if __name__ == "__main__":
    main()