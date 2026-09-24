def nodes_equal(node1, node2):
    if node1.get("node_type") != node2.get("node_type"):
        return False

    if node1.get("value") != node2.get("value"):
        return False

    children1 = node1.get("children", [])
    children2 = node2.get("children", [])

    if len(children1) != len(children2):
        return False

    return all(
        nodes_equal(c1, c2)
        for c1, c2 in zip(children1, children2)
    )

main_action = {
    'action': 'insert-tree',
    'node_type': 'declaration',
    'value': None,
    'children': [
        {
            'node_type': 'primitive_type',
            'value': 'int',
            'children': []
        },
        {
            'node_type': 'init_declarator',
            'value': None,
            'children': [
                {
                    'node_type': 'identifier',
                    'value': 'z',
                    'children': []
                },
                {
                    'node_type': '=',
                    'value': '=',
                    'children': []
                },
                {
                    'node_type': 'number_literal',
                    'value': '30',
                    'children': []
                }
            ]
        },
        {
            'node_type': ';',
            'value': ';',
            'children': []
        }
    ]
}

backport_action = {
    'action': 'insert-tree',
    'node_type': 'declaration',
    'value': None,
    'children': [
        {
            'node_type': 'primitive_type',
            'value': 'int',
            'children': []
        },
        {
            'node_type': 'init_declarator',
            'value': None,
            'children': [
                {
                    'node_type': 'identifier',
                    'value': 'z',
                    'children': []
                },
                {
                    'node_type': '=',
                    'value': '=',
                    'children': []
                },
                {
                    'node_type': 'number_literal',
                    'value': '30',
                    'children': []
                }
            ]
        },
        {
            'node_type': ';',
            'value': '-',
            'children': []
        }
    ]
}

print(nodes_equal(main_action, backport_action))