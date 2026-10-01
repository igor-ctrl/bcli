"""Dump the Python bcli Typer/click surface as JSON (fixture for the Rust parity test)."""
import json, sys
import typer
from bcli_cli.app import app

def walk(cmd, path):
    out = []
    if hasattr(cmd, 'commands'):
        if path:
            out.append({"path": path, "group": True, "options": [], "positionals": []})
        for name, sub in sorted(cmd.commands.items()):
            out.extend(walk(sub, path + [name]))
        if not path:
            out.insert(0, {"path": [], "group": True, "options": opts(cmd), "positionals": []})
        return out
    out.append({"path": path, "group": False, "options": opts(cmd), "positionals": pos(cmd)})
    return out

def opts(cmd):
    r = []
    for p in cmd.params:
        if p.param_type_name == 'option':
            if p.name == "help":
                continue
            r.append({"long": sorted(o for o in p.opts if o.startswith("--")),
                      "short": sorted(o for o in p.opts if not o.startswith("--")),
                      "flag": bool(p.is_flag), "hidden": bool(p.hidden),
                      "multiple": bool(p.multiple), "required": bool(p.required)})
    return r

def pos(cmd):
    return [{"name": p.name, "required": bool(p.required), "nargs": p.nargs}
            for p in cmd.params if p.param_type_name == 'argument']

root = typer.main.get_command(app)
json.dump(walk(root, []), sys.stdout, indent=1)
