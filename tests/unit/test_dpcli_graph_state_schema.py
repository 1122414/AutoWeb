from __future__ import annotations

import unittest

from core.state_v2 import AgentState


class TestDpcliGraphStateSchema(unittest.TestCase):
    def test_executor_success_update_fields_are_registered(self) -> None:
        annotations = AgentState.__annotations__

        for field in (
            "dpcli_action_kind",
            "dpcli_verification_contract",
        ):
            with self.subTest(field=field):
                self.assertIn(field, annotations)


if __name__ == "__main__":
    unittest.main()
