"""
The exit-code contract is ONE table, and nothing may drift from it.

Carried forward from two Manager reviews as "exit code 4 is still undocumented".
It is documented - in `--help` and in README.md, both since commit 8101878 - so
the note was stale by the time it was written twice. What was NEVER true is the
other half of the ask, "one table, one place": there are two copies of the
table and nothing tied them to each other or to the codes `main.py` actually
returns. A contract stated twice and checked nowhere is one edit away from
being a contract stated differently in two places.

So the codes are DISCOVERED FROM SOURCE - every `return N` in the CLI's own
entry points, parsed out of the AST - and both documents are checked against
that set. Adding a `return 5` to `main.py` and documenting it nowhere now fails
here, which is the property the reviews were actually asking for.
"""
import ast
import re
from pathlib import Path

from src.main import build_parser

ROOT = Path(__file__).parent.parent
MAIN = ROOT / "src" / "main.py"
README = ROOT / "README.md"

# The functions that produce a process exit status. `main` dispatches to the
# other three and returns their value, so between them they are every code the
# tool can exit with.
EXIT_FUNCTIONS = {"main", "run_fixture", "run_search", "run_new_trip"}


def _codes_returned_by_the_cli() -> set:
    """Every integer literal returned by the CLI's entry points, from the AST."""
    tree = ast.parse(MAIN.read_text())
    codes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in EXIT_FUNCTIONS:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Return)
                and isinstance(inner.value, ast.Constant)
                and isinstance(inner.value.value, int)
                and not isinstance(inner.value.value, bool)
            ):
                codes.add(inner.value.value)
    return codes


def _codes_in_help() -> set:
    epilog = build_parser().epilog
    block = epilog.split("EXIT CODES")[1]
    return {int(m) for m in re.findall(r"^\s{2}(\d)\s{2}", block, re.MULTILINE)}


def _codes_in_readme() -> set:
    body = README.read_text().split("### Exit codes")[1].split("###")[0]
    return {int(m) for m in re.findall(r"^\|\s*`(\d)`\s*\|", body, re.MULTILINE)}


def test_every_exit_code_the_cli_returns_is_in_the_help_table():
    """
    A code the tool can exit with and nobody wrote down is the bug this project
    keeps making: a behaviour that exists, is correct, and is documented
    nowhere the reader looks.
    """
    returned = _codes_returned_by_the_cli()
    documented = _codes_in_help()
    assert returned - documented == set(), (
        f"src/main.py can exit with {sorted(returned - documented)}, which the "
        f"--help table does not mention."
    )


def test_the_help_table_documents_no_code_the_cli_cannot_return():
    """A documented code nothing returns is a promise about behaviour that does not exist."""
    returned = _codes_returned_by_the_cli() | {0}
    documented = _codes_in_help()
    assert documented - returned == set(), (
        f"--help documents {sorted(documented - returned)}, which src/main.py "
        f"never returns."
    )


def test_the_readme_table_and_the_help_table_are_the_same_contract():
    """
    Two copies of one contract, held to each other.

    `--help` calls itself "the single authoritative list; README.md quotes this
    one", which is only true if something checks the quotation.
    """
    assert _codes_in_readme() == _codes_in_help()


def test_both_tables_carry_the_headline_meaning_of_each_code():
    """
    The codes agreeing is not enough - the WORDS must agree too.

    3 and 4 are the pair that matters: "the number is not quotable" and "the
    plan cannot be executed" are different failures, and a wrapping script that
    confused them would quote a withheld margin.
    """
    help_text = build_parser().epilog.split("EXIT CODES")[1]
    readme = README.read_text().split("### Exit codes")[1].split("###")[0]
    for phrase in ("WALLET ERROR", "WITHHELD", "NOT EXECUTABLE"):
        assert phrase in help_text, f"--help lost {phrase!r}"
        assert phrase in readme, f"README lost {phrase!r}"
