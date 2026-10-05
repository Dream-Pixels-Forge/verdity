"""
Verdity CLI - Command line interface for Verdity.

This package deliberately re-exports nothing.

It used to do `from .enforce import enforce` / `from .review import review`,
which meant `python -m verdity.cli.review` imported verdity.cli.review twice:
once via this package, then again via runpy. runpy warns about that double
execution and it can genuinely produce two distinct module objects.

It is tempting to replace those eager imports with a lazy module-level
__getattr__, but that is worse: Python rebinds `verdity.cli.enforce` to the
*module* as soon as anything imports the submodule, so the same attribute
would resolve to a Click Group or a module depending on import order.

The console-script entry points target verdity.cli.<module>:<group>
directly (see [project.scripts]), and nothing in the codebase imports these
names from the package, so the clean fix is to not export them at all.
"""
