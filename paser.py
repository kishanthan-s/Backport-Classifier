import re


GUMTREE_ACTIONS = {
    "insert-tree",
    "insert-node",
    "delete-tree",
    "delete-node",
    "update-node",
    "move-tree",
    "move-node",
}


def extract_node_line(line):
    """
    Extract node information from a GumTree node line.

    The indentation is also stored so that the hierarchy
    can be reconstructed later.
    """

    if not line.strip():
        return None

    # Count indentation BEFORE removing it
    indent = len(line) - len(line.lstrip())
    # print("Indentation:", indent, "line:", line.strip())

    # Remove indentation only for extracting node information
    text = line.strip()
    # print("Extracting node information from:", text)
    # Find [start,end] at the end of the line
    match = re.search(r"\[(\d+),(\d+)\]\s*$", text)

    # print("Match found:", match)

    if not match:
        return None

    start = int(match.group(1))
    end = int(match.group(2))
    # print("Start:", start, "End:", end)
    # Get everything before [start,end]
    head = text[:match.start()].strip()
    # print("Node head:", head)
    if not head:
        return None

    # Split node type and value
    if ": " in head:
        node_type, value = head.split(": ", 1)

        node_type = node_type.strip()
        value = value.strip()

        # print("Node type:", node_type, "Value:", value)
        if value == "":
            value = None

    else:
        node_type = head
        value = None
        # print("Node type:", node_type, "Value: None")
    if not node_type:
        return None

    return {
        "node_type": node_type,
        "value": value,
        "start": start,
        "end": end,
        "indent": indent,
        "children": [],
    }


def add_node_to_tree(root, node):
    """
    Add a node to the correct parent based on indentation.

    Example:

    declaration
        primitive_type
        init_declarator
            identifier

    becomes:

    declaration
        |
        +-- primitive_type
        |
        +-- init_declarator
                |
                +-- identifier
    """

    if node["indent"] > root["indent"]:
        # print("Adding node", node["node_type"], "as child of", root["node_type"])
        # The new node belongs somewhere inside root.
        # Check the existing children first.

        if root["children"]:
            # # print("Root has children, checking last child:", root["children"][-1]["node_type"])
            last_child = root["children"][-1]

            if node["indent"] > last_child["indent"]:
                # print("Node", node["node_type"], "is deeper than last child", last_child["node_type"])
                add_node_to_tree(last_child, node)
                return

        # Otherwise it is a direct child
        root["children"].append(node)
        # print("Node", node["node_type"], "added as child of", root["node_type"])
        return

    # This node does not belong directly under root.
    # The caller will handle moving up the hierarchy.


def build_node_tree(lines, start_index):
    """
    Build a complete node hierarchy starting from start_index.

    Returns:

        root
        next_index
    """

    if start_index >= len(lines):
        return None, start_index

    root = extract_node_line(lines[start_index])
    # print(root)
    if not root:
        return None, start_index

    root_indent = root["indent"]

    j = start_index + 1

    # Keep track of the nodes at each indentation level.
    stack = [root]

    while j < len(lines):

        line = lines[j]

        stripped = line.strip()

        # These mean the tree has ended
        if stripped == "to":
            break

        if stripped == "===":
            break

        if stripped in GUMTREE_ACTIONS:
            break

        node = extract_node_line(line)
        if not node:
            j += 1
            continue

        # Ignore anything that is not actually inside the root
        if node["indent"] <= root_indent:
            break

        # --------------------------------------------------
        # Find the correct parent
        # --------------------------------------------------

        while stack and node["indent"] <= stack[-1]["indent"]:
            stack.pop()

        if stack:
            stack[-1]["children"].append(node)

        # This node can now become a parent
        stack.append(node)
        j += 1

    return root, j


def copy_node_info(node):
    """
    Remove parser-only information such as indentation
    and recursively copy the hierarchy.
    """

    if not node:
        return None

    result = {
        "node_type": node["node_type"],
        "value": node["value"],
        "start": node["start"],
        "end": node["end"],
        "children": [],
    }

    for child in node["children"]:
        result["children"].append(copy_node_info(child))

    # print(result)

    return result


def parse_gumtree_actions(output_file):
    """
    Parse GumTree output into a list of actions.

    Every node hierarchy is preserved.

    Each action contains:

        action
        line_number
        node_type
        value
        new_value
        start
        end
        parent_type
        parent_start
        parent_end
        at
        children
    """

    # ------------------------------------------------------------
    # Read GumTree output
    # ------------------------------------------------------------

    with open(output_file, "r", encoding="utf-8") as file:
        lines = file.readlines()

    actions = []

    i = 0

    # ------------------------------------------------------------
    # Process each line
    # ------------------------------------------------------------

    while i < len(lines):

        action = lines[i].strip()

        # print("Reading:", i, action)

        # Ignore lines that are not actions
        if action not in GUMTREE_ACTIONS:
            i += 1
            continue

        # --------------------------------------------------------
        # Create action information
        # --------------------------------------------------------

        action_info = {
            "action": action,
            "line_number": i + 1,

            "node_type": None,
            "value": None,
            "new_value": None,

            "start": None,
            "end": None,

            "parent_type": None,
            "parent_start": None,
            "parent_end": None,

            "at": None,

            "children": [],
        }

        # --------------------------------------------------------
        # Find "---"
        # --------------------------------------------------------

        j = i + 1

        while j < len(lines):

            if lines[j].strip() == "---":
                break

            j += 1

        # Move to the first node after "---"
        j += 1

        # ========================================================
        # INSERT-TREE / DELETE-TREE
        # ========================================================

        if action in ["insert-tree", "delete-tree"]:

            if j < len(lines):

                root, next_index = build_node_tree(lines, j)
                # print("Built node tree from line", j, "to", next_index, ":", root)
                if root:

                    action_info["node_type"] = root["node_type"]
                    action_info["value"] = root["value"]
                    action_info["start"] = root["start"]
                    action_info["end"] = root["end"]

                    # Store complete hierarchy
                    action_info["children"] = root["children"]

                    j = next_index

                # ------------------------------------------------
                # Read parent
                # ------------------------------------------------

                if j < len(lines) and lines[j].strip() == "to":
                    j += 1

                    if j < len(lines):

                        parent = extract_node_line(lines[j])

                        if parent:

                            action_info["parent_type"] = (
                                parent["node_type"]
                            )

                            action_info["parent_start"] = (
                                parent["start"]
                            )

                            action_info["parent_end"] = (
                                parent["end"]
                            )

                        j += 1

                    # ------------------------------------------------
                    # Read "at N"
                    # ------------------------------------------------

                    if j < len(lines):

                        match = re.match(
                            r"at\s+(\d+)",
                            lines[j].strip()
                        )
                        if match:

                            action_info["at"] = int(
                                match.group(1)
                            )

                            j += 1

            actions.append(action_info)

            i = j

            continue

        # ========================================================
        # INSERT-NODE / DELETE-NODE
        # ========================================================

        if action in ["insert-node", "delete-node"]:

            if j < len(lines):

                node = extract_node_line(lines[j])

                if node:

                    action_info["node_type"] = node["node_type"]
                    action_info["value"] = node["value"]
                    action_info["start"] = node["start"]
                    action_info["end"] = node["end"]

                    # Even a single node has a children field
                    action_info["children"] = node["children"]

                j += 1

            # ------------------------------------------------
            # INSERT-NODE parent information
            # ------------------------------------------------

            if action == "insert-node":

                if j < len(lines) and lines[j].strip() == "to":

                    j += 1

                    if j < len(lines):

                        parent = extract_node_line(lines[j])

                        if parent:

                            action_info["parent_type"] = (
                                parent["node_type"]
                            )

                            action_info["parent_start"] = (
                                parent["start"]
                            )

                            action_info["parent_end"] = (
                                parent["end"]
                            )

                        j += 1

                    # ------------------------------------------------
                    # Read "at N"
                    # ------------------------------------------------

                    if j < len(lines):

                        match = re.match(
                            r"at\s+(\d+)",
                            lines[j].strip()
                        )

                        if match:

                            action_info["at"] = int(
                                match.group(1)
                            )

                            j += 1

            actions.append(action_info)

            i = j

            continue

        # ========================================================
        # UPDATE-NODE
        # ========================================================

        if action == "update-node":

            if j < len(lines):

                node = extract_node_line(lines[j])

                if node:

                    action_info["node_type"] = node["node_type"]
                    action_info["value"] = node["value"]
                    action_info["start"] = node["start"]
                    action_info["end"] = node["end"]

                j += 1

            # ------------------------------------------------
            # Read:
            #
            # replace old_value by new_value
            # ------------------------------------------------

            if j < len(lines):

                match = re.match(
                    r"replace\s+(.*?)\s+by\s+(.*)",
                    lines[j].strip()
                )

                if match:

                    action_info["value"] = match.group(1)
                    action_info["new_value"] = match.group(2)

                    j += 1

            actions.append(action_info)

            i = j

            continue

        # ========================================================
        # MOVE-NODE / MOVE-TREE
        # ========================================================

        if action in ["move-node", "move-tree"]:

            if j < len(lines):

                # For move-tree, preserve the complete hierarchy
                if action == "move-tree":

                    root, next_index = build_node_tree(lines, j)

                    if root:

                        action_info["node_type"] = root["node_type"]
                        action_info["value"] = root["value"]
                        action_info["start"] = root["start"]
                        action_info["end"] = root["end"]

                        action_info["children"] = root["children"]

                        j = next_index

                else:

                    # move-node contains one node
                    node = extract_node_line(lines[j])

                    if node:

                        action_info["node_type"] = node["node_type"]
                        action_info["value"] = node["value"]
                        action_info["start"] = node["start"]
                        action_info["end"] = node["end"]

                    j += 1

            # ------------------------------------------------
            # Read parent
            # ------------------------------------------------

            if j < len(lines) and lines[j].strip() == "to":

                j += 1

                if j < len(lines):

                    parent = extract_node_line(lines[j])

                    if parent:

                        action_info["parent_type"] = (
                            parent["node_type"]
                        )

                        action_info["parent_start"] = (
                            parent["start"]
                        )

                        action_info["parent_end"] = (
                            parent["end"]
                        )

                    j += 1

                # ------------------------------------------------
                # Read "at N"
                # ------------------------------------------------

                if j < len(lines):

                    match = re.match(
                        r"at\s+(\d+)",
                        lines[j].strip()
                    )

                    if match:

                        action_info["at"] = int(
                            match.group(1)
                        )

                        j += 1

            actions.append(action_info)

            i = j

            continue

        i += 1

    return actions


def print_tree(nodes, level=0):

    """
    Print the hierarchy clearly.
    """

    for node in nodes:

        indent = "    " * level

        print(
            indent
            + "- "
            + node["node_type"]
            + " | value: "
            + str(node["value"])
            + " | "
            + str(node["start"])
            + ","
            + str(node["end"])
        )

        if node["children"]:
            print_tree(node["children"], level + 1)


