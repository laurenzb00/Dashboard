"""Unit tests for core.normalize – data normalization functions."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.normalize import normalize_fronius, normalize_bmk
from core.schema import (
    PV_POWER_KW, GRID_POWER_KW, LOAD_POWER_KW,
    BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C
)


class TestNormalizeFronius(unittest.TestCase):
    """Tests for Fronius inverter data normalization."""

    def test_full_record(self):
        raw = {
            "P_PV": 5500.0,     # Watts
            "P_Grid": -2000.0, # Watts
            "P_Load": 3000.0,  # Watts
        }
        result = normalize_fronius(raw)

        self.assertAlmostEqual(result[PV_POWER_KW], 5.5)    # kW
        self.assertAlmostEqual(result[GRID_POWER_KW], -2.0) # kW
        self.assertAlmostEqual(result[LOAD_POWER_KW], 3.0)  # kW

    def test_partial_record(self):
        raw = {
            "P_PV": 3000.0,
        }
        result = normalize_fronius(raw)

        self.assertAlmostEqual(result[PV_POWER_KW], 3.0)
        self.assertEqual(result[GRID_POWER_KW], 0.0)
        self.assertEqual(result[LOAD_POWER_KW], 0.0)

    def test_string_values_converted(self):
        raw = {
            "P_PV": "4200.0",
            "P_Grid": "-500.0",
        }
        result = normalize_fronius(raw)

        self.assertAlmostEqual(result[PV_POWER_KW], 4.2)
        self.assertAlmostEqual(result[GRID_POWER_KW], -0.5)

    def test_invalid_values_default_zero(self):
        raw = {
            "P_PV": "invalid",
            "P_Grid": None,
        }
        result = normalize_fronius(raw)

        self.assertEqual(result[PV_POWER_KW], 0.0)
        self.assertEqual(result[GRID_POWER_KW], 0.0)


class TestNormalizeBmk(unittest.TestCase):
    """Tests for BMK heating data normalization."""

    def test_full_record(self):
        raw = {
            "Kesseltemperatur": 75.0,
            "Warmwasser": 50.0,
            "Pufferspeicher Oben": 65.0,
            "Pufferspeicher Mitte": 55.0,
            "Pufferspeicher Unten": 45.0,
        }
        result = normalize_bmk(raw)

        self.assertAlmostEqual(result[BMK_KESSEL_C], 75.0)
        self.assertAlmostEqual(result[BMK_WARMWASSER_C], 50.0)
        self.assertAlmostEqual(result[BUF_TOP_C], 65.0)
        self.assertAlmostEqual(result[BUF_MID_C], 55.0)
        self.assertAlmostEqual(result[BUF_BOTTOM_C], 45.0)

    def test_partial_record(self):
        raw = {
            "Kesseltemperatur": 80.0,
        }
        result = normalize_bmk(raw)

        self.assertAlmostEqual(result[BMK_KESSEL_C], 80.0)
        self.assertEqual(result[BUF_TOP_C], 0.0)

    def test_string_values_converted(self):
        raw = {
            "Kesseltemperatur": "82.5",
            "Warmwasser": "55.0",
        }
        result = normalize_bmk(raw)

        self.assertAlmostEqual(result[BMK_KESSEL_C], 82.5)
        self.assertAlmostEqual(result[BMK_WARMWASSER_C], 55.0)

    def test_alternate_key_names(self):
        """Test with alternate field names (Kessel vs Kesseltemperatur)."""
        raw = {
            "Kessel": 70.0,
            "Warmwassertemperatur": 48.0,
            "Puffer_Oben": 60.0,
        }
        result = normalize_bmk(raw)

        self.assertAlmostEqual(result[BMK_KESSEL_C], 70.0)
        self.assertAlmostEqual(result[BMK_WARMWASSER_C], 48.0)
        self.assertAlmostEqual(result[BUF_TOP_C], 60.0)


if __name__ == "__main__":
    unittest.main()
