"""Isolated Telegram topic settings shared by config-dependent tests."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from importlib import import_module
from unittest.mock import patch

TEST_CHAT_ID = -1009000000101
DND_THREAD_ID = 11
MINI_THREAD_ID = 22
TOPIC_SETTINGS_MODULES = (
    "app.topics",
    "app.context",
    "app.topic_guard",
    "app.handlers.admin",
    "app.mini.worlds",
)


def topic_settings():
    return {
        TEST_CHAT_ID: {
            DND_THREAD_ID: {"admin": "@testadmin", "characters": {"@someone": "Test hero"}},
            MINI_THREAD_ID: {"admin": "@testadmin", "characters": {},
                             "mini": True, "mini_name": "Test Mini"},
        },
    }


@contextmanager
def isolated_topics(settings=None):
    """Replace every imported settings reference; never mutate live config."""
    isolated = deepcopy(topic_settings() if settings is None else settings)
    modules = [import_module(name) for name in TOPIC_SETTINGS_MODULES]
    with ExitStack() as stack:
        for module in modules:
            stack.enter_context(patch.object(module, "TOPIC_SETTINGS", isolated))
        yield isolated
