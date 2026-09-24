import os
import re
import sys
import argparse
import pandas as pd
import tempfile
import subprocess

from Is_namespace_change import compare_action_lists
from paser import parse_gumtree_actions
from matcher import match_actions
from type_5_classification import classify_type_5
from is_location_change import location_change_check

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(BASE_DIR, "tensorflow")
GUMTREE_WORKDIR = os.path.join(BASE_DIR, "work", "output", "gumtree")
GUMTREE = os.path.join(
    BASE_DIR,
    "gumtree",
    "dist",
    "build",
    "install",
    "gumtree",
    "bin",
    "gumtree",
)

LOG_FILE = os.path.join(BASE_DIR, "process_flow.log")
OUTPUT_THRESHOLD = 5000

def log(msg: str):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def run_git(args, repo_dir=REPO_DIR, check=True, timeout=60):
    result = subprocess.run(["git", "-C", repo_dir] + args,
                            capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"Git command failed: {' '.join(args)}\n{result.stderr}")
    return result

def get_parent_commit(sha, repo_dir=REPO_DIR):
    out = run_git(["rev-list", "--parents", "-n", "1", sha], repo_dir)
    parts = out.stdout.strip().split()
    if len(parts) < 2:
        raise RuntimeError(f"No parent found for {sha}")
    parent = parts[1]
    log(f"parent -> {parent}")
    return parent

def get_changed_files(parent_sha, commit_sha, repo_dir=REPO_DIR):
    log(f"Getting changed files: {parent_sha}..{commit_sha}")
    result = run_git(["diff", "--name-status", "-M", parent_sha, commit_sha], repo_dir)
    files = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            files.append({"status": status, "old_path": parts[1], "new_path": parts[2]})
        elif len(parts) >= 2:
            files.append({"status": status, "old_path": parts[1], "new_path": parts[1]})
    log(f"Found {len(files)} changed files")
    return files

def match_files(main_files, backport_files):
    log("Matching files between mainline and backport")
    main_by_path = {f["new_path"]: f for f in main_files}
    backport_by_path = {f["new_path"]: f for f in backport_files}
    common = set(main_by_path) & set(backport_by_path)
    log(f"Common files count: {len(common)}")
    return [{"main": main_by_path[p], "backport": backport_by_path[p], "path": p}
            for p in sorted(common)]

GUMTREE_EXTENSIONS = {".py", ".cc", ".cpp", ".c", ".h", ".hpp", ".java", ".js", ".ts", ".html", ".xml"}
TEXT_EXTENSIONS = {".md", ".rst", ".txt", ".cmake", ".json", ".proto", ".bzl", ".build", ".sh", ".pbtxt", ".tpl", ".template"}


def get_extension(path):
    fn = os.path.basename(path)
    return os.path.splitext(fn)[1].lower() if "." in fn else ""

def get_file_content(sha, path, repo_dir=REPO_DIR):
    result = run_git(["show", f"{sha}:{path}"], repo_dir, check=False)
    return result.stdout if result.returncode == 0 else None

def get_comparison_method(path):
    ext = get_extension(path)
    if ext in GUMTREE_EXTENSIONS:
        return "gumtree"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"

def run_gumtree_textdiff(before_text, after_text, output_file, file_path, timeout=60):
    log(f"Running GumTree textdiff for {file_path}")
    with tempfile.TemporaryDirectory() as td:
        ext = os.path.splitext(file_path)[1]
        before_fp = os.path.join(td, f"before{ext}")
        after_fp = os.path.join(td, f"after{ext}")
        with open(before_fp, "w", encoding="utf-8") as f:
            f.write(before_text)
        with open(after_fp, "w", encoding="utf-8") as f:
            f.write(after_text)
        result = subprocess.run([GUMTREE, "textdiff", before_fp, after_fp],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"GumTree textdiff failed for {file_path}: {result.stderr}")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        log(f"GumTree textdiff finished for {file_path}")
        return result

    
def classify_commit_pair(commit_sha, main_sha, repo_dir=REPO_DIR, workdir=GUMTREE_WORKDIR, verbose=True):
    log("=" * 80)
    log(f"Processing commit pair: main={main_sha[:8]} backport={commit_sha[:8]}")

    os.makedirs(workdir, exist_ok=True)

    commit_parent = get_parent_commit(commit_sha, repo_dir)
    main_parent = get_parent_commit(main_sha, repo_dir)

    print(f"Main commit: {main_sha}")
    print(f"Main parent commit: {main_parent}")
    print(f"Backport commit: {commit_sha}")
    print(f"Backport parent commit: {commit_parent}")

    main_files = get_changed_files(main_parent, main_sha, repo_dir)
    backport_files = get_changed_files(commit_parent, commit_sha, repo_dir)

    matches = match_files(main_files, backport_files)

    results = []

    for match in matches:
        path = match["path"]
        file_renamed = False

        main_path = match.get("main", {}).get("new_path")
        backport_path = match.get("backport", {}).get("new_path")
        if main_path and backport_path and main_path != backport_path:
            file_renamed = True

        log(f"\n--- Analyzing file: {path} (renamed={file_renamed})")

        if get_comparison_method(path) != "gumtree":
            log(f"Skipping non-GumTree file: {path}")
            continue

        m_parent, m_curr, b_parent, b_curr = (
            get_file_content(main_parent, path, repo_dir),
            get_file_content(main_sha, path, repo_dir),
            get_file_content(commit_parent, path, repo_dir),
            get_file_content(commit_sha, path, repo_dir),
        )

        if any(c is None for c in (m_parent, m_curr, b_parent, b_curr)):
            log(f"Missing content for {path}; skipping")
            continue

        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", path)

        main_log = os.path.join(
            workdir,
            f"{commit_sha}_{safe}_main.txt"
        )

        back_log = os.path.join(
            workdir,
            f"{commit_sha}_{safe}_backport.txt"
        )

        try:
            run_gumtree_textdiff(
                m_parent,
                m_curr,
                main_log,
                path
            )

            run_gumtree_textdiff(
                b_parent,
                b_curr,
                back_log,
                path
            )

            main_actions = parse_gumtree_actions(main_log)
            back_actions = parse_gumtree_actions(back_log)

            print(
                f"Parsed actions for {path}: "
                f"main={len(main_actions)}, backport={len(back_actions)}"
            )

            # print(main_actions)
            # print(back_actions)
            # matches, unmatched_main, unmatched_backport= match_actions(main_actions, back_actions)
            # print("Used main indices:", [main_index for main_index, _ in matches])
            # print("Used backport indices:", [backport_index for _, backport_index in matches])
            # print("Matches (main_index, backport_index):", matches)
            type_5_classification = classify_type_5(main_actions, back_actions)
            if type_5_classification == "Type V":
                classification = "Type V"
            else:
                matches_name_iden, unmatched_main_name_iden, unmatched_backport_name_iden, type_3_flags, type_5_flags, nam_un_change= compare_action_lists(
                    main_actions,
                    back_actions
                )
                # if ((len(unmatched_main_name_iden) > 0 or len(unmatched_backport_name_iden) > 0) and any(type_3_flags) and len(type_3_flags) > 0):
                #     is_namespace_identifer_change = True
                # else:
                #     is_namespace_identifer_change=False

                is_namespace_identifer_change = (
                    any(type_3_flags)
                    and any(not unchanged for unchanged in nam_un_change)
                )

                
                if any(type_5_flags):
                    classification = "Type V"
                else:
                
                    matched_location, function_mismatches_location, unmatched_main_location, unmatched_backport_location = location_change_check(
                        path,
                        main_actions,
                        back_actions,
                        repo_dir,
                    )
                    # print("Function mismatches:", function_mismatches)
                    # print("Unmatched main actions:", unmatched_main)
                    # print("Unmatched backport actions:", unmatched_backport)
                    # print(matched)
                    if len(unmatched_main_location) > 0 or len(unmatched_backport_location) > 0:
                        classification = "Type V"
                    else:

                        if len(function_mismatches_location) > 0:
                            is_location_change=True
                        else:
                            is_location_change=False

                        print(f"File: {path}, is_location_change: {is_location_change}, is_namespace_identifer_change: {is_namespace_identifer_change}, file_renamed: {file_renamed}")
                        if not is_location_change and not is_namespace_identifer_change:
                            classification = "Type I"

                        elif is_location_change and not is_namespace_identifer_change:
                            classification = "Type II"

                        elif not is_location_change and is_namespace_identifer_change:
                            classification = "Type III"

                        elif is_location_change and is_namespace_identifer_change:
                            classification = "Type IV"

                        else:
                            classification = "Unclassified"
                                    
            log(f"Classification for {path}: {classification}")

            status, error = "success", ""

        except FileNotFoundError:
            classification = "Type V"
            status, error = "skipped", ""

        except Exception as exc:
            classification = ""
            status, error = "failed", str(exc)

            log(f"Error processing {path}: {error}")

        results.append({
            "commit_sha": commit_sha,
            "main_sha": main_sha,
            "file": path,
            "classification": classification,
            "status": status,
            "error": error,
            "file_renamed": file_renamed,
        })

        if verbose:
            log(f"{commit_sha[:10]} {path}: {classification or status}")

    if not results:
        log("No comparable GumTree files – returning Type V for whole pair")

        return [{
            "commit_sha": commit_sha,
            "main_sha": main_sha,
            "file": "",
            "classification": "Type V",
            "status": "no_comparable_file",
            "error": "No common GumTree-comparable changed file",
            "file_renamed": False,
        }]

    log("=" * 80)

    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Classify TensorFlow backport commit pairs."
    )

    parser.add_argument(
        "--csv",
        default=os.path.join(BASE_DIR, "commits.csv"),
        help="CSV with commit_sha, main_sha columns"
    )

    parser.add_argument(
        "--output",
        default=os.path.join(BASE_DIR, "classification_results.csv"),
        help="Output CSV"
    )

    parser.add_argument(
        "--commit-sha",
        help="Classify only this commit"
    )

    args = parser.parse_args()

    df = pd.read_csv(args.csv, dtype=str).fillna("")

    if args.commit_sha:
        df = df[df["commit_sha"] == args.commit_sha]

    all_results = []
    for row in df.itertuples(index=False):
        row_results = classify_commit_pair(
            row.commit_sha,
            row.main_sha,
            verbose=True
        )
        all_results.extend(row_results)
        pd.DataFrame(row_results).to_csv(
            args.output,
            mode="a",
            header=not os.path.exists(args.output),
            index=False
        )

    print(f"Results saved to {args.output}")