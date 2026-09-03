from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import _bootstrap  # noqa: F401

_USER_IDENTITY = (
    Path(__file__).resolve().parents[2]
    / 'api'
    / 'src'
    / 'core'
    / 'integrations'
    / 'transports'
    / 'user_identity.py'
)


def _load_user_identity():
    spec = importlib.util.spec_from_file_location('bridge_user_identity', _USER_IDENTITY)
    if spec is None or spec.loader is None:
        raise RuntimeError('user_identity.py not found')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_user_identity = _load_user_identity()
apply_user_ids = _user_identity.apply_user_ids
is_user_like = _user_identity.is_user_like


class _FakeUser:
    def __init__(self, pk: int = 7, public_id: str = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'):
        self.pk = pk
        self.public_id = UUID(public_id)
        self.is_authenticated = True


class BridgeUserIdentityTests(unittest.TestCase):
    def test_apply_user_ids_replaces_orm_user(self) -> None:
        user = _FakeUser()
        result = apply_user_ids({'user': user, 'extra': 'ok'})
        self.assertEqual(result['user_id'], 7)
        self.assertEqual(result['user_public_id'], 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.assertEqual(result['extra'], 'ok')
        self.assertNotIn('user', result)

    def test_explicit_ids_win_over_orm(self) -> None:
        user = _FakeUser()
        result = apply_user_ids({
            'user': user,
            'user_id': 42,
            'user_public_id': '11111111-2222-3333-4444-555555555555',
        })
        self.assertEqual(result['user_id'], 42)
        self.assertEqual(result['user_public_id'], '11111111-2222-3333-4444-555555555555')
        self.assertNotIn('user', result)

    def test_primitive_user_is_dropped(self) -> None:
        result = apply_user_ids({'user': 9, 'user_id': 9})
        self.assertEqual(result['user_id'], 9)
        self.assertNotIn('user', result)

    def test_namespace_without_auth_is_not_user_like(self) -> None:
        value = SimpleNamespace(pk=1, public_id='x')
        self.assertFalse(is_user_like(value))


if __name__ == '__main__':
    unittest.main()
