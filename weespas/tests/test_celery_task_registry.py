"""Contract tests for the Celery task REGISTRY — names, routing, and module identity.

These guard a class of bug that no test of a task *body* can catch, because the task
body is perfectly correct: the failure is that the worker and the publisher disagree
about what the task is *called*.

The bug this file was written for (observed in the dev Celery log, 2026-07-28):

    Received unregistered task of type
    'PE.weespas.services.personalization_tasks.invalidate_user_feed'.

`invalidate_user_feed` was the one task declared without an explicit `name=`, so
Celery derived its name from `func.__module__`. Meanwhile `celery_app.include` listed
modules by their bare "services.*" path while every real import used the fully
qualified "PE.weespas.services.*" path. Python therefore loaded those files TWICE as
two distinct module objects, and the derived task name depended on which identity got
there first — worker registered the short name, API published the long one.

It failed silently, which is the worst part: `apply_async()` does NOT raise when no
worker knows the name (the broker happily accepts the message), so the caller's
`except Exception:` inline fallback never fired. Feed invalidation was dropped on the
floor and the chained `feeds.prewarm_user_feed` died with it — users kept seeing a
stale personalized feed after favoriting or dismissing a property.
"""
from __future__ import annotations

import sys

from PE.weespas.core.celery_app import celery_app


def _app_task_names() -> list[str]:
    """Registered task names, excluding Celery's own built-ins (celery.chord, ...)."""
    return [n for n in celery_app.tasks if not n.startswith("celery.")]


def test_no_task_name_is_derived_from_a_module_path():
    """Every task must declare an explicit `name=`.

    A module-path-derived name silently rebinds itself when a module moves or gets
    imported under a second identity. An explicit name is a stable wire contract, so
    a rename can never desynchronize the publisher from the worker.
    """
    celery_app.loader.import_default_modules()
    derived = [
        n for n in _app_task_names()
        if n.startswith(("services.", "PE.", "PE.weespas."))
    ]
    assert derived == [], (
        "These tasks take their name from their module path, so moving or "
        f"double-importing the module silently renames the task: {derived}"
    )


def test_include_list_matches_real_import_paths():
    """`celery_app.include` must use the same module paths the codebase imports.

    If it lists "services.foo" while the app imports "PE.weespas.services.foo",
    Python creates two module objects from one file: module-level state is duplicated
    (two separate `POPULAR_CITIES` lists, two Redis clients) and unnamed tasks
    register under whichever identity won the race.
    """
    include = celery_app.conf.include or []
    assert include, "include list is empty — tasks would not be discovered at all"
    bad = [m for m in include if not m.startswith("PE.weespas.")]
    assert bad == [], (
        "include entries must be fully qualified to match real imports; "
        f"these would double-import their module: {bad}"
    )


def test_importing_tasks_does_not_double_import_any_module():
    """The end state the two rules above exist to protect.

    Asserts on real `sys.modules` identity rather than on config strings, so it still
    bites if a *different* mechanism (a stray sys.path entry, a relative import)
    reintroduces the dual identity.
    """
    celery_app.loader.import_default_modules()
    doubled = [
        m for m in sys.modules
        if m.startswith("services.") and f"PE.weespas.{m}" in sys.modules
    ]
    assert doubled == [], (
        "these modules are loaded twice under two identities, so their "
        f"module-level state is duplicated: {sorted(doubled)}"
    )


def test_invalidate_user_feed_is_registered_and_routed_to_feeds():
    """The specific regression: the name the API publishes is the name a worker knows.

    Also pins the queue. `invalidate_user_feed` is chained with
    `feeds.prewarm_user_feed`; if the two land on different queues the pair can be
    served by different worker pools and the p99-warming intent of the chain is lost.
    """
    from PE.weespas.services.personalization_tasks import invalidate_user_feed

    assert invalidate_user_feed.name == "feeds.invalidate_user_feed"
    assert invalidate_user_feed.name in celery_app.tasks, (
        "the task the routers import is not in the registry the worker builds — "
        "this is exactly the 'Received unregistered task' failure"
    )

    route = celery_app.amqp.router.route({}, invalidate_user_feed.name)
    assert str(route["queue"].name) == "feeds"


def test_every_beat_scheduled_task_exists_in_the_registry():
    """Beat fires tasks by name string; a typo or rename is invisible until 3am.

    Nothing type-checks `beat_schedule`, so a scheduled name that matches no task
    just emits 'unregistered task' into the log forever.
    """
    celery_app.loader.import_default_modules()
    registered = set(_app_task_names())
    missing = sorted({
        entry["task"]
        for entry in celery_app.conf.beat_schedule.values()
        if entry["task"] not in registered
    })
    assert missing == [], f"beat_schedule references non-existent tasks: {missing}"
