"""The run layout's names: the product's one run and the library's root of every run are two classes.

The library's storage module and :mod:`rcp.runs.layout` both defined ``RunLayout``, for two meanings, so a module
that imported both got one class silently. The library's class is ``RunsRoot``; this row keeps both meanings
distinct, and it lives in the product's tree because the library's suite cannot import a consumer.
"""

from __future__ import annotations

from pathlib import Path


def test_the_library_layout_and_the_product_layout_are_two_names(tmp_path: Path) -> None:
    """H7: ``configkit.storage`` and ``rcp.runs.layout`` both defined ``RunLayout``, for two meanings.

    A module that imported both got one class silently: the product's ``RunLayout`` is *one run's* directory,
    the library's is the directory every run lives under. The library's class is ``RunsRoot``, so both bind in
    one namespace and each keeps its own meaning.
    """
    from configkit.storage import RunsRoot

    from rcp.runs.layout import RunLayout

    assert RunsRoot is not RunLayout
    assert (RunsRoot.__name__, RunLayout.__name__) == ("RunsRoot", "RunLayout")
    assert (RunsRoot.__module__, RunLayout.__module__) == ("configkit.storage", "rcp.runs.layout")
    # The meanings the two names keep apart: the library roots the runs, the product roots one run.
    assert RunsRoot(tmp_path).run_dir("run-1") == tmp_path / "run-1"
    assert RunLayout.at(tmp_path).root == str(tmp_path)
