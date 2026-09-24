#!/usr/bin/env python3
"""
run_tests.py - 98 labelled backport test cases for the Type I-V classifier.

SETUP
  Put this file in the SAME folder as final_analysis.py (with paser.py, matcher.py,
  is_location_change.py, Is_namespace_change.py, type_5_classification.py).
  Requirements:  pip install pandas tree-sitter tree-sitter-c
                 git on PATH, Java on PATH, and a GumTree install.

RUN
  python run_tests.py
  python run_tests.py --gumtree /path/to/gumtree/bin/gumtree      # if not in the default place
  python run_tests.py --checkout backport                         # working tree = backport commit
  python run_tests.py --limit 10                                  # quick smoke run
  python run_tests.py --only "Type III"                           # one expected type
  python run_tests.py --verbose                                   # print every case

Default GumTree location (same as final_analysis.py):
  <this folder>/gumtree/dist/build/install/gumtree/bin/gumtree
Env var GUMTREE_BIN also works.

NOTE: test sources are plain C (.c) because stock GumTree only parses .c/.h for the C family.
Use --ext .cc only if YOUR GumTree build can parse .cc.
"""
import os, re, sys, io, shutil, stat, subprocess, tempfile, contextlib, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def die(msg):
    print("\n[setup error] " + msg)
    sys.exit(2)


try:
    import pandas as pd
except ImportError:
    die("pandas missing:  pip install pandas")
try:
    import final_analysis as fa
except ImportError as e:
    die("Cannot import final_analysis.py (%s).\nPut run_tests.py in the same folder as final_analysis.py, "
        "and run: pip install pandas tree-sitter tree-sitter-c" % e)

HDR = "#include <stdlib.h>\n\n"

# ---------------------------------------------------------------- shapes
# each: sig template, body, guard line(s), rename map for identifiers
SHAPES = {
    "div":    dict(sig="int {N}(int a, int b)", body=["  int q = a / b;", "  return q;"],
                   guard=["  if (b == 0) return -1;"], braced=["  if (b == 0) {", "    return -1;", "  }"],
                   ren=[{"b": "divisor"}, {"b": "den"}]),
    "copy":   dict(sig="int {N}(char* dst, const char* src, int n)",
                   body=["  for (int i = 0; i < n; ++i) {", "    dst[i] = src[i];", "  }", "  return n;"],
                   guard=["  if (n < 0) return -1;"], braced=["  if (n < 0) {", "    return -1;", "  }"],
                   ren=[{"n": "count"}, {"n": "size"}]),
    "sum":    dict(sig="int {N}(const int* v, int n)",
                   body=["  int total = 0;", "  for (int i = 0; i < n; ++i) total += v[i];", "  return total;"],
                   guard=["  if (n < 0) return 0;"], braced=["  if (n < 0) {", "    return 0;", "  }"],
                   ren=[{"n": "len"}, {"n": "num"}]),
    "alloc":  dict(sig="int* {N}(int size)",
                   body=["  int* p = (int*)malloc(size * 4);", "  p[0] = 0;", "  return p;"],
                   guard=["  if (size <= 0) return NULL;"], braced=["  if (size <= 0) {", "    return NULL;", "  }"],
                   ren=[{"size": "nbytes"}]),
    "resize": dict(sig="int {N}(int* shape, int dim)",
                   body=["  shape[0] = dim;", "  shape[1] = dim * 4;", "  return 0;"],
                   guard=["  if (dim < 0) return -1;"], braced=["  if (dim < 0) {", "    return -1;", "  }"],
                   ren=[{"dim": "rank"}]),
    "idx":    dict(sig="int {N}(const int* table, int idx)",
                   body=["  int v = table[idx];", "  return v * 2;"],
                   guard=["  if (idx < 0 || idx >= 16) return -1;"], braced=["  if (idx < 0 || idx >= 16) {", "    return -1;", "  }"],
                   ren=[{"idx": "index"}, {"idx": "pos"}]),
}
SHAPE_LIST = list(SHAPES)


def rn(lines, m):
    out = []
    for l in lines:
        for a, b in m.items():
            l = re.sub(r"\b%s\b" % re.escape(a), b, l)
        out.append(l)
    return out


def decoys(tag):
    return [
        ("int Decoy%sA(int x)" % tag, ["  return x + 1;"]),
        ("int Decoy%sB(int x, int y)" % tag, ["  int z = x * y;", "  return z;"]),
    ]


def build(funcs, prefix_lines=()):
    s = HDR + "".join(l + "\n" for l in prefix_lines)
    for sig, body in funcs:
        s += sig + " {\n" + "".join(l + "\n" for l in body) + "}\n\n"
    return s


def apply(body, ops):
    body = list(body)
    for op in ops:
        if op[0] == "ins":
            body[op[1]:op[1]] = op[2]
        elif op[0] == "rep":
            for i, l in enumerate(body):
                if op[1] in l:
                    body[i] = l.replace(op[1], op[2], 1)
                    break
            else:
                raise ValueError("rep target not found %r" % (op[1],))
        elif op[0] == "del":
            del body[op[1]]
    return body


CALL_SIG = "int {N}(int x)"
def call_body(h):
    return ["  int r = %s(x);" % h, "  return r;"]
API_SIG = "int {N}(int x)"
API_BODY = ["  int s = 0;", "  return s;"]

CASES = []

def case(expected, sub, main_t, back_t, main_ops, back_ops, path="ops/target.c",
         back_path=None, back_tail=None, back_prefix=(), back_swap=False):
    """main_t / back_t = (sig, body) of the patched function."""
    CASES.append(dict(expected=expected, sub=sub, main_t=main_t, back_t=back_t,
                      main_ops=main_ops, back_ops=back_ops, path=path,
                      back_path=back_path or path, back_tail=back_tail,
                      back_prefix=back_prefix, back_swap=back_swap))

def S(shape, name, ren=None):
    sh = SHAPES[shape]
    sig = sh["sig"].format(N=name)
    body = list(sh["body"])
    if ren:
        sig = rn([sig], ren)[0]; body = rn(body, ren)
    return sig, body

G = lambda s: SHAPES[s]["guard"]
B = lambda s: SHAPES[s]["braced"]

# ---------------------------------------------------------------- Type I
for s in SHAPE_LIST:
    case("Type I", "guard", S(s, "Fn"), S(s, "Fn"), [("ins", 0, G(s))], [("ins", 0, G(s))])
for s in SHAPE_LIST:
    case("Type I", "braced-guard", S(s, "Fn"), S(s, "Fn"), [("ins", 0, B(s))], [("ins", 0, B(s))])
case("Type I", "const-update", S("alloc", "Fn"), S("alloc", "Fn"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 8")])
case("Type I", "const-update", S("resize", "Fn"), S("resize", "Fn"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 8")])
case("Type I", "delete-line", S("resize", "Fn"), S("resize", "Fn"), [("del", 1)], [("del", 1)])
case("Type I", "delete-line", S("alloc", "Fn"), S("alloc", "Fn"), [("del", 1)], [("del", 1)])
for h, h2 in [("Helper", "HelperSafe"), ("Load", "LoadChecked")]:
    case("Type I", "update-identical", (CALL_SIG.format(N="Fn"), call_body(h)), (CALL_SIG.format(N="Fn"), call_body(h)),
         [("rep", h + "(", h2 + "(")], [("rep", h + "(", h2 + "(")])
case("Type I", "guard+tail-noise", S("div", "Fn"), S("div", "Fn"), [("ins", 0, G("div"))], [("ins", 0, G("div"))],
     back_tail=[("int Extra1(int x)", ["  return x - 1;"])])
case("Type I", "guard+tail-noise", S("sum", "Fn"), S("sum", "Fn"), [("ins", 0, G("sum"))], [("ins", 0, G("sum"))],
     back_tail=[("int Extra2(int x)", ["  return x - 2;"])])

# line-number-only shift: NOT a location change (line numbers alone prove nothing)
case("Type I", "line-shift-only", S("div", "Fn"), S("div", "Fn"), [("ins", 0, G("div"))], [("ins", 0, G("div"))],
     back_prefix=["// legacy comment 1", "// legacy comment 2", "static int kLegacy = 3;", ""])
case("Type I", "line-shift-only", S("copy", "Fn"), S("copy", "Fn"), [("ins", 0, G("copy"))], [("ins", 0, G("copy"))],
     back_prefix=["// legacy comment 1", "static int kLegacy = 7;", ""])

# ---------------------------------------------------------------- Type II
for s in SHAPE_LIST:
    case("Type II", "func-change/guard", S(s, "Fn"), S(s, "FnLegacy"), [("ins", 0, G(s))], [("ins", 0, G(s))])
for s in ["div", "copy", "sum", "idx"]:
    case("Type II", "func-change/braced", S(s, "Fn"), S(s, "FnLegacy"), [("ins", 0, B(s))], [("ins", 0, B(s))], back_swap=True)
case("Type II", "func-change/const", S("alloc", "Fn"), S("alloc", "FnOld"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 8")])
case("Type II", "func-change/const", S("resize", "Fn"), S("resize", "FnOld"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 8")])
case("Type II", "func-change/delete", S("resize", "Fn"), S("resize", "FnOld"), [("del", 1)], [("del", 1)])
case("Type II", "func-change/delete", S("alloc", "Fn"), S("alloc", "FnOld"), [("del", 1)], [("del", 1)])
for h, h2 in [("Helper", "HelperSafe"), ("Load", "LoadChecked")]:
    case("Type II", "func-change/update", (CALL_SIG.format(N="Fn"), call_body(h)), (CALL_SIG.format(N="FnOld"), call_body(h)),
         [("rep", h + "(", h2 + "(")], [("rep", h + "(", h2 + "(")])

# ---------------------------------------------------------------- Type III
# insert-style: backport branch uses a different variable name
ren_pairs = [("div", 0), ("copy", 0), ("sum", 0), ("alloc", 0), ("resize", 0), ("idx", 0),
             ("div", 1), ("copy", 1), ("sum", 1), ("idx", 1)]
for s, k in ren_pairs:
    m = SHAPES[s]["ren"][k]
    case("Type III", "insert/renamed-var", S(s, "Fn"), S(s, "Fn", m), [("ins", 0, G(s))], [("ins", 0, rn(G(s), m))])
# namespace/API qualified name
for i, api in enumerate(["Validate", "CheckShape", "VerifyInput", "SanityCheck"]):
    case("Type III", "insert/namespace-api", (API_SIG.format(N="Fn"), API_BODY), (API_SIG.format(N="Fn"), API_BODY),
         [("ins", 1, ["  s = tf_%s(x);" % api])], [("ins", 1, ["  s = %s(x);" % api])])
# update-style: same old identifier, different new identifier
upd = [("Helper", "HelperSafe", "HelperChecked"), ("Load", "LoadV2", "LoadNew"), ("Read", "ReadSafe", "ReadBounded"),
       ("Parse", "ParseStrict", "ParseSafe"), ("Copy", "CopyN", "CopyBytes"), ("Fetch", "FetchOrDie", "FetchChecked")]
for h, a, b in upd:
    case("Type III", "update/different-new-name", (CALL_SIG.format(N="Fn"), call_body(h)), (CALL_SIG.format(N="Fn"), call_body(h)),
         [("rep", h + "(", a + "(")], [("rep", h + "(", b + "(")])

# ---------------------------------------------------------------- Type IV
for s, k in ren_pairs:
    m = SHAPES[s]["ren"][k]
    case("Type IV", "insert/renamed-var+func", S(s, "Fn"), S(s, "FnLegacy", m), [("ins", 0, G(s))], [("ins", 0, rn(G(s), m))])
for i, api in enumerate(["Validate", "CheckShape", "VerifyInput", "SanityCheck"]):
    case("Type IV", "insert/namespace-api+func", (API_SIG.format(N="Fn"), API_BODY), (API_SIG.format(N="FnOld"), API_BODY),
         [("ins", 1, ["  s = tf_%s(x);" % api])], [("ins", 1, ["  s = %s(x);" % api])])
for h, a, b in upd:
    case("Type IV", "update+func", (CALL_SIG.format(N="Fn"), call_body(h)), (CALL_SIG.format(N="FnOld"), call_body(h)),
         [("rep", h + "(", a + "(")], [("rep", h + "(", b + "(")])

# ---------------------------------------------------------------- Type V
# extra statement in backport
for s in ["div", "copy", "sum", "idx"]:
    case("Type V", "backport-extra-stmt", S(s, "Fn"), S(s, "Fn"), [("ins", 0, G(s))],
         [("ins", 0, G(s) + ["  LogWarning(1);"])])
# backport misses second statement
for s in ["div", "sum", "alloc"]:
    case("Type V", "backport-missing-stmt", S(s, "Fn"), S(s, "Fn"), [("ins", 0, G(s) + ["  LogWarning(1);"])], [("ins", 0, G(s))])
# different guard condition
case("Type V", "different-condition", S("div", "Fn"), S("div", "Fn"), [("ins", 0, ["  if (b == 0) return -1;"])], [("ins", 0, ["  if (b <= 0) return -1;"])])
case("Type V", "different-condition", S("sum", "Fn"), S("sum", "Fn"), [("ins", 0, ["  if (n < 0) return 0;"])], [("ins", 0, ["  if (n <= 0) return 0;"])])
case("Type V", "different-condition", S("idx", "Fn"), S("idx", "Fn"), [("ins", 0, ["  if (idx < 0 || idx >= 16) return -1;"])], [("ins", 0, ["  if (idx < 0) return -1;"])])
# constant differs
case("Type V", "const-update-differs", S("alloc", "Fn"), S("alloc", "Fn"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 16")])
case("Type V", "const-update-differs", S("resize", "Fn"), S("resize", "Fn"), [("rep", "* 4", "* 8")], [("rep", "* 4", "* 2")])
# operator differs
case("Type V", "operator-update-differs", S("sum", "Fn"), S("sum", "Fn"), [("rep", "i < n", "i <= n")], [("rep", "i < n", "i != n")])
case("Type V", "operator-update-differs", S("copy", "Fn"), S("copy", "Fn"), [("rep", "i < n", "i <= n")], [("rep", "i < n", "i > n")])
# different return value
case("Type V", "different-return", S("div", "Fn"), S("div", "Fn"), [("ins", 0, ["  if (b == 0) return -1;"])], [("ins", 0, ["  if (b == 0) return 0;"])])
case("Type V", "different-return", S("copy", "Fn"), S("copy", "Fn"), [("ins", 0, ["  if (n < 0) return -1;"])], [("ins", 0, ["  if (n < 0) return n;"])])
# backport deletes extra
case("Type V", "backport-extra-delete", S("resize", "Fn"), S("resize", "Fn"), [("del", 1)], [("del", 1), ("del", 0)])
case("Type V", "backport-extra-delete", S("alloc", "Fn"), S("alloc", "Fn"), [("del", 1)], [("del", 1), ("del", 0)])
# entirely different fix shape
case("Type V", "different-fix", S("div", "Fn"), S("div", "Fn"), [("ins", 0, ["  if (b == 0) return -1;"])],
     [("ins", 0, ["  while (b == 0) {", "    b = 1;", "  }"])])
case("Type V", "different-fix", S("sum", "Fn"), S("sum", "Fn"), [("ins", 0, ["  if (n < 0) return 0;"])],
     [("ins", 0, ["  n = n < 0 ? 0 : n;"])])

assert len(CASES) == 98, len(CASES)


# ---------------------------------------------------------------- run
def _rmtree(path):
    def onerr(func, p, exc):           # Windows: read-only .git files
        try:
            os.chmod(p, stat.S_IWRITE); func(p)
        except Exception:
            pass
    shutil.rmtree(path, onerr=onerr) if sys.version_info < (3, 12) else shutil.rmtree(path, onexc=lambda f, p, e: onerr(f, p, e))


def sh(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def commit(repo, relpath, text, msg):
    fp = os.path.join(repo, *relpath.split("/"))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", newline="\n", encoding="utf-8") as f:   # LF only: keeps byte offsets stable on Windows
        f.write(text)
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "--allow-empty", "-m", msg], repo)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def run_case(i, c, checkout, ext, keep=False):
    repo = tempfile.mkdtemp(prefix="bpcase%03d_" % i)
    try:
        sh(["git", "init", "-q"], repo)
        for k, v in [("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false")]:
            sh(["git", "config", k, v], repo)
        dm = decoys("M"); db = decoys("M")
        if c["back_swap"]:
            db = db[::-1]
        mpath = c["path"][:-2] + ext if ext != ".c" else c["path"]
        bpath = c["back_path"][:-2] + ext if ext != ".c" else c["back_path"]
        main_before = [dm[0], c["main_t"], dm[1]]
        main_after = [dm[0], (c["main_t"][0], apply(c["main_t"][1], c["main_ops"])), dm[1]]
        tail = c["back_tail"] or []
        back_after_body = apply(c["back_t"][1], c["back_ops"])
        if c["back_swap"]:
            back_before = [c["back_t"], db[0], db[1]]
            back_after = [(c["back_t"][0], back_after_body), db[0], db[1]]
        else:
            back_before = [db[0], c["back_t"], db[1]]
            back_after = [db[0], (c["back_t"][0], back_after_body), db[1]]
        back_before += tail; back_after += tail
        pre = c["back_prefix"]
        commit(repo, bpath, build(back_before, pre), "backport-before")
        b1 = commit(repo, bpath, build(back_after, pre), "backport-after")
        commit(repo, mpath, build(main_before), "main-before")
        m1 = commit(repo, mpath, build(main_after), "main-after")
        if checkout == "backport":
            sh(["git", "checkout", "-q", b1], repo)
        wd = os.path.join(repo, "_gt"); os.makedirs(wd)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                res = fa.classify_commit_pair(b1, m1, repo_dir=repo, workdir=wd, verbose=False)
            except Exception as e:
                res = [dict(classification="", status="crash", error=str(e))]
        return res
    finally:
        if not keep:
            _rmtree(repo)


def find_gumtree(cli):
    cands = [cli, os.environ.get("GUMTREE_BIN"), getattr(fa, "GUMTREE", None)]
    for c_ in cands:
        if not c_:
            continue
        for p in (c_, c_ + ".bat", c_ + ".exe"):
            if os.path.isfile(p):
                return p
    return None


def preflight(gumtree, ext):
    if shutil.which("git") is None:
        die("git not found on PATH.")
    if shutil.which("java") is None:
        die("java not found on PATH (GumTree needs Java 11+).")
    try:
        import tree_sitter, tree_sitter_c  # noqa
    except ImportError:
        die("tree-sitter missing:  pip install tree-sitter tree-sitter-c")
    if not gumtree:
        die("GumTree not found. Pass --gumtree PATH or set GUMTREE_BIN.\n"
            "Default expected: " + getattr(fa, "GUMTREE", "<unknown>"))
    td = tempfile.mkdtemp()
    try:
        a, b = os.path.join(td, "a" + ext), os.path.join(td, "b" + ext)
        open(a, "w").write("int f(int a){return a+1;}\n")
        open(b, "w").write("int f(int b){return b+1;}\n")
        r = subprocess.run([gumtree, "textdiff", a, b], capture_output=True, text=True)
        if "update-node" not in r.stdout:
            die("GumTree self-check failed for extension '%s' (it prints nothing for unsupported types).\n"
                "stderr: %s\nTry the default '.c'." % (ext, r.stderr[:300]))
    finally:
        _rmtree(td)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gumtree", help="path to gumtree launcher")
    ap.add_argument("--checkout", choices=["main", "backport"], default="main",
                    help="which commit is checked out in the repo while classifying (default main)")
    ap.add_argument("--ext", default=".c", help="source extension for test files (default .c)")
    ap.add_argument("--limit", type=int, help="run only the first N cases")
    ap.add_argument("--only", help="run only cases whose expected label equals this, e.g. 'Type III'")
    ap.add_argument("--output", default=os.path.join(HERE, "test_results.csv"))
    ap.add_argument("--keep", action="store_true", help="keep temp git repos for inspection")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    gumtree = find_gumtree(args.gumtree)
    preflight(gumtree, args.ext)
    fa.GUMTREE = gumtree
    print("GumTree :", gumtree)
    print("Checkout:", args.checkout, "| ext:", args.ext)

    # GumTree exits 0 even when it fails; treat empty output as an error instead of a silent Type I
    _orig = fa.run_gumtree_textdiff
    def _checked(*a, **k):
        r = _orig(*a, **k)
        if not r.stdout.strip():
            raise RuntimeError("EMPTY GumTree output: " + r.stderr[:100])
        return r
    fa.run_gumtree_textdiff = _checked

    todo = list(enumerate(CASES, 1))
    if args.only:
        todo = [(i, c) for i, c in todo if c["expected"] == args.only]
    if args.limit:
        todo = todo[:args.limit]

    rows = []
    for n, (i, c) in enumerate(todo, 1):
        res = run_case(i, c, args.checkout, args.ext, args.keep)
        r = res[0]
        row = dict(id=i, expected=c["expected"], sub=c["sub"], predicted=r.get("classification", ""),
                   status=r.get("status", ""), error=(r.get("error", "") or "")[:150])
        row["correct"] = row["expected"] == row["predicted"]
        rows.append(row)
        if args.verbose or n % 10 == 0:
            print("[%3d/%d] #%03d %-8s %-28s -> %-8s %s" % (n, len(todo), i, row["expected"], row["sub"],
                  row["predicted"] or row["status"], "OK" if row["correct"] else "WRONG"))

    df = pd.DataFrame(rows)
    df.to_csv(args.output, index=False)
    pd.set_option("display.width", 200)
    print("\n=== Accuracy: %d/%d (%.0f%%) ===" % (df.correct.sum(), len(df), 100 * df.correct.mean()))
    print("\nConfusion matrix (rows = expected, cols = predicted)")
    print(pd.crosstab(df.expected, df.predicted.replace("", "ERROR")).to_string())
    print("\nPer sub-category")
    print(df.groupby(["expected", "sub"]).agg(n=("id", "count"), correct=("correct", "sum")).to_string())
    bad = df[df.status.isin(["failed", "crash"])]
    if len(bad):
        print("\nErrors:"); print(bad[["id", "sub", "status", "error"]].to_string(index=False))
    print("\nSaved:", args.output)


if __name__ == "__main__":
    main()