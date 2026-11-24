"""Bundle plotting modules from a fixed commit with a driver into one script that Arnor's Python reads from stdin.

    python make_bundle.py <commit> <driver.py> [module or module=repo/path.py ...] > bundle.py

Nothing is copied to Arnor: the module sources travel inside the bundle and are executed in memory, in the order
given. fink_lsst.analytics.catalog is stubbed (the figure code imports it but these drivers never open the catalogue).
"""
import base64
import subprocess
import sys

commit, driver, *mods = sys.argv[1:]
mods = mods or ['alert_colour_diagrams', 'alert_science_suite', 'paper_figures_pub', 'edp2_figures']
pairs = [m.split('=', 1) if '=' in m else (m, f'scripts/{m}.py') for m in mods]
src = {n: base64.b64encode(subprocess.check_output(['git', 'show', f'{commit}:{p}'])).decode() for n, p in pairs}
names = [n for n, _ in pairs]
head = f'''import base64, sys, types
_SRC = {src!r}
for _n in ("fink_lsst", "fink_lsst.analytics", "fink_lsst.analytics.catalog"):
    sys.modules[_n] = types.ModuleType(_n)
sys.modules["fink_lsst.analytics.catalog"].open_catalog = None
for _n in {names!r}:
    _m = types.ModuleType(_n)
    _m.__file__ = "/bundled/{commit}/scripts/" + _n + ".py"
    sys.modules[_n] = _m
    exec(compile(base64.b64decode(_SRC[_n]).decode(), _m.__file__, "exec"), _m.__dict__)
'''
sys.stdout.write(head + '\n# ---- driver\n' + open(driver).read())
