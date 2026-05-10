from app.services.workqueue.providers import all_providers
from app.services.workqueue.providers.base import WorkqueueProvider


def test_registry_iterable():
    providers = list(all_providers())
    assert all(isinstance(p, WorkqueueProvider) or hasattr(p, "fetch") for p in providers)
