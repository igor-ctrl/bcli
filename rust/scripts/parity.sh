#!/usr/bin/env bash
# Regenerate the golden outputs from the Python bcli, then diff the Rust
# binary against them.
#
#   PY_BCLI=/path/to/python/bcli rust/scripts/parity.sh
#
# PY_BCLI defaults to `bcli` on PATH (the Python install). The Rust binary is
# built from this workspace. Exit status is non-zero on any difference.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixtures="$here/crates/bcli-cli/tests/fixtures"
golden="$fixtures/golden"
py="${PY_BCLI:-bcli}"

cargo build --quiet --manifest-path "$here/Cargo.toml" -p bcli-cli
rs="$here/target/debug/bcli"

run() {  # run <binary> <args...> -> writes $out.{out,err,code}
    local bin="$1" out="$2"; shift 2
    set +e
    env -i PATH="$PATH" HOME="$fixtures/home" "$bin" "$@" >"$out.out" 2>"$out.err"
    echo $? >"$out.code"
    set -e
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
status=0
while IFS='|' read -r name args; do
    name="$(echo "$name" | xargs)"
    [[ -z "$name" || "$name" == \#* ]] && continue
    read -r -a argv <<<"$args"
    run "$py" "$golden/$name" "${argv[@]}"
    run "$rs" "$tmp/$name" "${argv[@]}"
    for ext in out err code; do
        if ! cmp -s "$golden/$name.$ext" "$tmp/$name.$ext"; then
            echo "DIFF $name.$ext"
            diff "$golden/$name.$ext" "$tmp/$name.$ext" | head -20 || true
            status=1
        fi
    done
done <"$golden/cases.txt"

# Refresh the CLI-surface fixture used by tests/surface_parity.rs.
py_python="$(dirname "$(command -v "$py")")/python"
if [[ -x "$py_python" ]]; then
    env -i PATH="$PATH" HOME="$tmp" "$py_python" "$here/scripts/dump_python_surface.py" \
        >"$fixtures/python-cli-surface.json"
fi

[[ $status -eq 0 ]] && echo "parity: all cases match"
exit $status
