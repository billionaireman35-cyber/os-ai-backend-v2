import unittest
from app.security.risk import RiskSignals, calculate_risk


class RiskTests(unittest.TestCase):
    def test_fresh_step_up_satisfies_untrusted_device_assurance(self):
        score, reasons = calculate_risk(RiskSignals(
            device_trusted=False,
            sensitive_action=True,
            authentication_strength=100,
        ))
        self.assertEqual(score, 10)
        self.assertEqual(reasons, ("sensitive_action",))

    def test_untrusted_device_requires_step_up_before_fresh_auth(self):
        for auth in (70, 80):
            score, reasons = calculate_risk(RiskSignals(
                device_trusted=False,
                sensitive_action=True,
                authentication_strength=auth,
            ))
            self.assertEqual(score, 35)
            self.assertEqual(
                reasons,
                ("untrusted_device", "sensitive_action"),
            )


if __name__ == "__main__":
    unittest.main()
