from unittest.mock import Mock

from skills.vector_base import VectorCacheBase


class _Cache(VectorCacheBase):
    @property
    def _collection_name(self):
        return "unit"

    @property
    def _collection_description(self):
        return "unit"

    def _schema_fields(self, _dim):
        return []

    def _vector_field_names(self):
        return []


def test_shutdown_is_idempotent():
    cache = object.__new__(_Cache)
    cache._tag = "unit"
    cache._executor = Mock()
    cache._shutdown_complete = False

    cache._shutdown()
    cache._shutdown()

    cache._executor.shutdown.assert_called_once_with(wait=True)
