GUMTREE_ACTIONS = {
    "insert-tree",
    "insert-node",
    "delete-tree",
    "delete-node",
    "update-node",
    "move-tree",
    "move-node",
}

STRUCTURAL_ACTIONS = {
    "insert-tree",
    "insert-node",
    "delete-tree",
    "delete-node",
}

TREE_ACTIONS = {
    "insert-tree",
    "delete-tree",
    "move-tree",
}


def get_child_signature(children):
    """
    Recursively build a comparable signature of node types
    from a list of children, ignoring positions/values.
    """
    signature = []
    for child in children or []:
        signature.append((
            child.get("node_type"),
            get_child_signature(child.get("children"))
        ))
    return signature


def children_match(main_action, backport_action):
    """
    Only meaningful for tree actions. Compares the recursive
    node_type structure of children between the two actions.
    """
    main_children = main_action.get("children") or []
    backport_children = backport_action.get("children") or []

    return get_child_signature(main_children) == get_child_signature(backport_children)


def match_actions(main_actions, backport_actions):
    """
    Greedily match each main action to an unused backport action with the
    same action + node_type (and, for tree actions, matching child
    structure).

    Returns a tuple of (matches, unmatched_main, unmatched_backport):
      - matches: list of (main_action, backport_action) pairs, using the
        original action dicts (input format), in match order
      - unmatched_main: list of main_action dicts that had no match
      - unmatched_backport: list of backport_action dicts that had no match
    """
    used_main_indices = set()
    used_backport_indices = set()
    matches = []

    for main_index, main_action in enumerate(main_actions):

        for backport_index, backport_action in enumerate(backport_actions):

            if backport_index in used_backport_indices:
                continue

            if main_action["action"] != backport_action["action"]:
                continue

            if main_action["node_type"] != backport_action["node_type"]:
                continue

            # for tree-level actions, also compare the children structure
            if main_action["action"] in TREE_ACTIONS:
                if not children_match(main_action, backport_action):
                    continue

            used_main_indices.add(main_index)
            used_backport_indices.add(backport_index)
            matches.append((main_action, backport_action))

            break

    unmatched_main = [
        main_action
        for main_index, main_action in enumerate(main_actions)
        if main_index not in used_main_indices
    ]

    unmatched_backport = [
        backport_action
        for backport_index, backport_action in enumerate(backport_actions)
        if backport_index not in used_backport_indices
    ]

    return matches, unmatched_main, unmatched_backport