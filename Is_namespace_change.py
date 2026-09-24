type_3_node_types = {
    'identifier',
    'field_identifier',
    'type_identifier'
}

type_5_node_types = {
    # Operators / expressions
    'operator',
    'binary_expression',
    'unary_expression',
    'assignment_expression',
    'update_expression',
    'conditional_expression',

    # Control-flow / logic
    'if_statement',
    'else_clause',
    'switch_statement',
    'case_statement',
    'for_statement',
    'while_statement',
    'do_statement',

    # Return / calls / structural logic
    'return_statement',
    'call_expression',
    'argument_list',
}
def compare_actions(main_action, backport_action):
    """
    Compare one main action with one backport action.
    """

    main_initial_value = main_action.get('value')
    main_new_value = main_action.get('new_value')

    backport_initial_value = backport_action.get('value')
    backport_new_value = backport_action.get('new_value')

    values_match = (main_new_value == backport_new_value)

    return {
        'type': 'namespace_or_identifier_change',
        'match': values_match,

        'main_initial_value': main_initial_value,
        'main_new_value': main_new_value,

        'backport_initial_value': backport_initial_value,
        'backport_new_value': backport_new_value,

        'line_number': {
            'main': main_action.get('line_number'),
            'backport': backport_action.get('line_number'),
        },
    }


def compare_action_lists(main_actions, backport_actions):

    main_updates = [
        a for a in main_actions
        if a.get('action') == 'update-node'
    ]

    backport_updates = [
        a for a in backport_actions
        if a.get('action') == 'update-node'
    ]

    matches = []
    unmatched_main = []
    unmatched_backport = []
    type_3_flags = []
    type_5_flags = []
    nam_un_change = []

    used_backport_indices = set()

    for main_action in main_updates:

        match_idx = None

        for idx, backport_action in enumerate(backport_updates):

            # Backport action already paired
            if idx in used_backport_indices:
                continue

            is_type_3 = (
                main_action.get('node_type') in type_3_node_types
                and backport_action.get('node_type') in type_3_node_types
            )

            is_type_5 = (
                main_action.get('node_type') in type_5_node_types
                and backport_action.get('node_type') in type_5_node_types
            )

            
            # Pair using the initial value
            if main_action.get('value') == backport_action.get('value'):
                match_idx = idx
                type_3_flags.append(is_type_3)
                type_5_flags.append(is_type_5)
                break

        if match_idx is not None:

            backport_action = backport_updates[match_idx]

            result = compare_actions(
                main_action,
                backport_action
            )

            matches.append(result)
            nam_un_change.append(result['match'])

            used_backport_indices.add(match_idx)

        else:

            unmatched_main.append(main_action)

    # Anything not used is an unmatched backport action
    unmatched_backport = [
        backport_action
        for idx, backport_action in enumerate(backport_updates)
        if idx not in used_backport_indices
    ]

    return (
        matches,
        unmatched_main,
        unmatched_backport,
        type_3_flags,
        type_5_flags,
        nam_un_change,
    )

