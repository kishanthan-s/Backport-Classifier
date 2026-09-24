from matcher import STRUCTURAL_ACTIONS, match_actions


def classify_type_5(main_actions, backport_actions):

    matches, unmatched_main, unmatched_backport = match_actions(main_actions, backport_actions)

    for backport_action in unmatched_backport:

        action = backport_action["action"]

        if action in STRUCTURAL_ACTIONS:
            return "Type V"

    return "Not Type V"