#!/usr/bin/env python3
"""
find_enclosing_function.py

Given a source file and a list of GumTree-style actions
(insert-node / update-node / delete-node / move-node, each with
'start' and 'end' byte offsets), find the enclosing function/method
for every action by re-parsing the file with tree-sitter and walking
up from the offset via node.parent.

Install:
    pip install tree-sitter tree-sitter-c tree-sitter-cpp tree-sitter-java tree-sitter-python

Usage:
    python find_enclosing_function.py myfile.c actions.json
"""

import os
import sys
import json
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c
# ---- 1. language selection ---------------------------------------------

# Node types that count as "the enclosing function" per language.
FUNCTION_NODE_TYPES = {
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "java": {"method_declaration", "constructor_declaration"},
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition"},
    "typescript": {"function_declaration", "method_definition"},
}

# Field name (or fallback search) used to extract the function's name node.
def get_name_node(func_node, lang_key):
    # print("get_name_node:", func_node,"funct_node_type:", func_node.type, "lang_key:", lang_key)
    if lang_key in ("c", "cpp"):
        # function_definition -> declarator -> (possibly nested) -> identifier
        declarator = func_node.child_by_field_name("declarator")
        node = declarator
        while node is not None and node.type != "identifier":
            # unwrap pointer_declarator / function_declarator / etc.
            inner = node.child_by_field_name("declarator")
            # print("inner:", inner, "node:", node)
            if inner is None:
                # fall back: search named children for an identifier
                ident = None
                for c in node.children:
                    if c.type == "identifier":
                        ident = c
                        break
            node = inner
        return node
    elif lang_key == "java":
        return func_node.child_by_field_name("name")
    elif lang_key == "python":
        return func_node.child_by_field_name("name")
    elif lang_key in ("javascript", "typescript"):
        return func_node.child_by_field_name("name")
    return None


def load_language(lang_key):
    """Load a tree-sitter Language object for the given key."""
    if lang_key in ("c",):
        import tree_sitter_c as tsc
        return Language(tsc.language())
    if lang_key in ("cpp",):
        import tree_sitter_cpp as tscpp
        return Language(tscpp.language())
    if lang_key == "java":
        import tree_sitter_java as tsjava
        return Language(tsjava.language())
    if lang_key == "python":
        import tree_sitter_python as tspython
        return Language(tspython.language())
    if lang_key in ("javascript",):
        import tree_sitter_javascript as tsjs
        return Language(tsjs.language())
    if lang_key in ("typescript",):
        import tree_sitter_typescript as tsts
        return Language(tsts.language_typescript())
    raise ValueError(f"Unsupported language key: {lang_key}")


EXT_TO_LANG = {
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}


# ---- 2. core lookup -------------------------------------------------------

def find_enclosing_function_name(tree, source_bytes, start, end, lang_key):
    """Walk up from the node at [start, end) until a function/method node
    is found; return its name (str) or None if not inside a function."""
    node = tree.root_node.descendant_for_byte_range(start, end)
    target_types = FUNCTION_NODE_TYPES.get(lang_key, set())
    # print(node)
    # print(node.type)
    # print(target_types)
    while node is not None:
        # print(node)
        # print(node.type)
        # print(target_types)
        if node.type in target_types:
            # print("found function node:", node, "node.type:", node.type)
            name_node = get_name_node(node, lang_key)
            # print("name node:", name_node)
            if name_node is not None:
                # print("inside if", name_node)
                return source_bytes[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
            return None  # matched a function node but couldn't extract a name
        node = node.parent
        # print("walking up to parent:", node)
    return None  # not inside any function (e.g. global scope)


def annotate_actions(file_path: str, actions: list) -> list:
    """Re-parse file_path once and add 'enclosing_function' to every action."""
    ext = Path(file_path).suffix
    lang_key = EXT_TO_LANG.get(ext)
    if lang_key is None:
        raise ValueError(f"No language mapping for extension '{ext}'")

    language = load_language(lang_key)
    parser = Parser(language)

    source_bytes = Path(file_path).read_bytes()
    tree = parser.parse(source_bytes)

    for action in actions:
        start, end = action.get("start"), action.get("end")
        if start is None or end is None:
            action["enclosing_function"] = None
            continue
        action["enclosing_function"] = find_enclosing_function_name(
            tree, source_bytes, start, end, lang_key
        )
    return actions


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

def location_change_check(filepath, main_actions, backport_actions, repo_dir="."):
    used_main_indices = set()
    used_backport_indices = set()

    matched = []
    unmatched_main = []
    unmatched_backport = []
    function_mismatches = []

    for main_index, main_action in enumerate(main_actions):

        # Find an unused corresponding backport action
        match_index = None

        for backport_index, backport_action in enumerate(backport_actions):

            if backport_index in used_backport_indices:
                continue
            if main_action.get("action") != backport_action.get("action"):
                continue

            if main_action.get("node_type") != backport_action.get("node_type"):
                continue

            if main_action["action"] in {
                    "insert-tree",
                    "delete-tree",
                    "move-tree",
                }:
                    if not nodes_equal(main_action, backport_action):
                        continue
            # Your action matching condition goes here
            if main_action["value"] == backport_action["value"]:
                match_index = backport_index
                break

        if match_index is None:
            # No corresponding backport action exists
            unmatched_main.append(main_action)
            continue

        # Mark BOTH actions as used
        used_main_indices.add(main_index)
        used_backport_indices.add(match_index)

        backport_action = backport_actions[match_index]

        # Compare enclosing functions
        source_path = os.path.join(repo_dir, filepath)
        annotated_main = annotate_actions(source_path, [main_action])
        annotated_backport = annotate_actions(source_path, [backport_action])

        main_function = annotated_main[0]["enclosing_function"]
        backport_function = annotated_backport[0]["enclosing_function"]
        # print("main_function:", main_function, "backport_function:", backport_function)
        # input("Press Enter to continue...")
        if main_function != backport_function:
            function_mismatches.append({
                "main_action": main_action,
                "backport_action": backport_action,
                "main_function": main_function,
                "backport_function": backport_function
            })
        else:
            matched.append({
                "main_action": main_action,
                "backport_action": backport_action,
                "function": main_function
            })

    # Any backport action that was never used is unmatched
    unmatched_backport = [
        backport_action
        for backport_index, backport_action in enumerate(backport_actions)
        if backport_index not in used_backport_indices
    ]

    return (
        matched,
        function_mismatches,
        unmatched_main,
        unmatched_backport
    )
