"""Synthetic WP2 tests: never open the real SAP sources."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.data.business_selection import (
    apply_missing_categories, exclusion_reasons, number, quality_holds, run, validate_config,
)
from src.data.harmonize_workbooks import ROOT, SYSTEM_COLUMNS, sha256_file


def config_fixture():
    return json.loads((ROOT / "configs/business-selection.v1.json").read_text(encoding="utf-8"))


class BusinessSelectionTests(unittest.TestCase):
    def setUp(self):
        self.config = config_fixture()
        self.row = {"document_currency": "USD", "sales_group": "TEST", "country_key": "NA",
                    "net_price": "1", "order_quantity_item": "2", "net_value_item": "2", "kp_date": "2025-01-06"}

    def test_union_normalization_and_exact_decimal_zero(self):
        row = dict(self.row, document_currency=" idr ", sales_group=" peb ", country_key=" id ", net_price="0.00")
        self.assertEqual(exclusion_reasons(row, self.config), ["currency_idr", "buyer_excluded", "country_id", "net_price_zero"])
        for value in ["0", "0.00", "-0", "0e8"]:
            self.assertEqual(exclusion_reasons(dict(self.row, net_price=value), self.config), ["net_price_zero"])
        for value in ["", "bad", "NaN", "Infinity", "0.00000000000000000001"]:
            self.assertEqual(exclusion_reasons(dict(self.row, net_price=value), self.config), [])
        for code in ["PEB", "KWK", "LNS", "CPC"]:
            self.assertEqual(exclusion_reasons(dict(self.row, sales_group=code.lower()), self.config), ["buyer_excluded"])

    def test_invalid_numeric_is_hold_not_zero_and_zero_target_retained(self):
        self.assertIsNone(number("NaN"))
        row = dict(self.row, net_price="bad", kp_date="", sales_group=" ", order_quantity_item="0")
        holds = quality_holds(row, self.config["quality_policy"])
        self.assertEqual(set(holds), {"primary_date_missing_or_invalid", "sales_group_missing", "net_price_missing_or_invalid"})
        self.assertEqual(exclusion_reasons(row, self.config), [])
        self.assertIn("order_quantity_item_negative", quality_holds(dict(self.row, order_quantity_item="-1"), self.config["quality_policy"]))

    def test_contract_covers_all_columns_and_rejects_unsafe_rules(self):
        contract = json.loads((ROOT / self.config["feature_contract"]).read_text())
        schema = json.loads((ROOT / self.config["harmonization_config"]).read_text())
        validate_config(self.config, contract, schema)
        self.assertEqual(contract["buyer_mapping"]["canonical"], "sales_group")
        crd = next(c for c in contract["columns"] if c["field"] == "crd_date")
        self.assertEqual(crd["class"], "forbidden")
        broken = copy.deepcopy(contract)
        broken["columns"].pop()
        with self.assertRaises(ValueError):
            validate_config(self.config, broken, schema)
        broken_config = copy.deepcopy(self.config)
        broken_config["filters"][0]["operator"] = "unsupported"
        with self.assertRaises(ValueError):
            validate_config(broken_config, contract, schema)

    def make_workspace(self, root):
        (root / "configs").mkdir()
        (root / "dataset").mkdir()
        artifact = root / "artifacts/input"
        artifact.mkdir(parents=True)
        config = copy.deepcopy(self.config)
        for name in [config["harmonization_config"], config["feature_contract"]]:
            (root / name).write_bytes((ROOT / name).read_bytes())
        schema = json.loads((root / config["harmonization_config"]).read_text())
        rows = []
        updates = [{}, {"document_currency": " idr ", "sales_group": "peb", "country_key": "ID", "net_price": "0.00"},
                   {"sales_group": "kwk"}, {"net_price": "0"}, {"kp_date": "", "net_price": "bad"},
                   {"document_currency": "EUR", "order_quantity_item": "0"}]
        for i, update in enumerate(updates):
            row = {d["canonical_name"]: "" for d in schema["column_definitions"]}
            row.update(self.row)
            row.update(update)
            row.update(source_year=str(2023 + i % 3), source_file=f"{2023 + i % 3}.XLSX",
                       source_sheet="synthetic", source_row_number=str(i + 2), quality_status="valid", quality_flags="")
            rows.append(row)
        columns = SYSTEM_COLUMNS + [d["canonical_name"] for d in schema["column_definitions"]]
        pd.DataFrame(rows, columns=columns).to_csv(artifact / "canonical_transactions.csv.gz", index=False)
        sources = []
        paths = []
        for year in config["source_years"]:
            source = root / f"dataset/{year}.XLSX"
            source.write_bytes(b"synthetic fixture bytes only")
            paths.append(f"dataset/{year}.XLSX")
            sources.append({"source_file": source.name, "source_year": year, "source_sha256": sha256_file(source)})
        cleaning = artifact / "cleaning_manifest.json"
        cleaning.write_text(json.dumps({"config": {"sources": paths}}))
        manifest = {"manifest_id": "synthetic-wp1", "canonical_schema_version": schema["canonical_schema_version"],
                    "sources": sources, "row_count": len(rows), "input_cleaning_manifest": "artifacts/input/cleaning_manifest.json",
                    "input_cleaning_manifest_sha256": sha256_file(cleaning),
                    "outputs": {"canonical_transactions": "canonical_transactions.csv.gz",
                                "canonical_transactions_sha256": sha256_file(artifact / "canonical_transactions.csv.gz")}}
        (artifact / "data_manifest.json").write_text(json.dumps(manifest))
        (root / "configs/current-data.json").write_text(json.dumps({"active_data_manifest": "artifacts/input/data_manifest.json"}))
        config_path = root / "configs/business-selection.v1.json"
        config_path.write_text(json.dumps(config))
        return config_path

    def test_end_to_end_private_lineage_union_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = self.make_workspace(root)
            original_hash = sha256_file(root / "dataset/2023.XLSX")
            first = run(config_path, root=root)
            second = run(config_path, root=root)
            self.assertNotEqual(first, second)
            report = json.loads((first / "selection_report.json").read_text())
            self.assertEqual(report["all"]["before"], 6)
            self.assertEqual(report["all"]["excluded_unique"], 3)
            self.assertEqual(report["all"]["after"], 3)
            self.assertEqual(report["all"]["rows_matching_multiple_rules"], 1)
            self.assertEqual(report["all"]["exclusive_reason_combinations"]["currency_idr|buyer_excluded|country_id|net_price_zero"], 1)
            self.assertEqual(sum(s["after"] for s in report["by_source_year"].values()), 3)
            selected = pd.read_csv(first / "selected_transactions.csv.gz", dtype=str, keep_default_na=False)
            self.assertEqual(selected.country_key.tolist(), ["NA"] * 3)
            self.assertIn("0", selected.order_quantity_item.tolist())
            self.assertTrue(selected.wp2_quality_holds.str.contains("net_price_missing_or_invalid").any())
            self.assertEqual(sha256_file(root / "dataset/2023.XLSX"), original_hash)
            for name in ["selected_transactions.csv.gz", "selection_audit.csv.gz", "selection_report.json", "target_eda.json"]:
                self.assertEqual(sha256_file(first / name), sha256_file(second / name))
            manifest = json.loads((first / "data_manifest.json").read_text())
            self.assertFalse(manifest["ready_for_panel"])
            self.assertTrue(manifest["verification"]["roundtrip_exact"])
            self.assertEqual(len(manifest["inputs_sha256"]), 10)

    def test_checksum_tampering_and_public_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = self.make_workspace(root)
            (root / "dataset/2024.XLSX").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "Raw source checksum"):
                run(config_path, root=root)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = self.make_workspace(root)
            config = json.loads(config_path.read_text())
            config["output_root"] = "reports/unsafe"
            config_path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "private artifacts"):
                run(config_path, root=root)

    def test_v2_pcs_unknown_country_and_no_rounding_end_to_end(self):
        self.config = json.loads((ROOT / "configs/business-selection.v2.json").read_text())
        self.row.update(country_key=" ", order_quantity_item="1.5")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = self.make_workspace(root)
            output = run(config_path, root=root)
            selected = pd.read_csv(output / "selected_transactions.csv.gz", dtype=str, keep_default_na=False)
            self.assertEqual(selected.country_key.tolist(), ["__UNKNOWN_COUNTRY__"] * 3)
            self.assertEqual(selected.wp2_country_was_missing.tolist(), ["true"] * 3)
            self.assertEqual(selected.order_quantity_item.tolist(), ["1.5", "1.5", "0"])
            self.assertFalse(selected.wp2_quality_holds.str.contains("country_key_missing").any())
            report = json.loads((output / "selection_report.json").read_text())
            self.assertEqual(report["all"]["after"], 3)
            self.assertEqual(report["categorical_missing_handling"]["country_key"]["rows"], 3)
            self.assertEqual(report["buyer_missingness"]["before"], 0)
            manifest = json.loads((output / "data_manifest.json").read_text())
            self.assertNotIn("quantity_unit_requires_business_confirmation", manifest["blockers"])
            contract = json.loads((output / "feature_contract.json").read_text())
            self.assertEqual(contract["target"]["unit"], "pcs")
            self.assertEqual(contract["target"]["unit_status"], "business_confirmed")

    def test_reserved_country_token_keeps_valid_countries_and_rejects_collision(self):
        config = json.loads((ROOT / "configs/business-selection.v2.json").read_text())
        frame = pd.DataFrame({"country_key": ["", " ", "NA", "US"], "source_year": ["2023"] * 4})
        result, report = apply_missing_categories(frame, config)
        self.assertEqual(result.country_key.tolist(), ["__UNKNOWN_COUNTRY__", "__UNKNOWN_COUNTRY__", "NA", "US"])
        self.assertEqual(result.wp2_country_was_missing.tolist(), ["true", "true", "false", "false"])
        self.assertEqual(report["country_key"]["rows"], 2)
        self.assertEqual(frame.country_key.tolist(), ["", " ", "NA", "US"])
        frame.loc[3, "country_key"] = "__UNKNOWN_COUNTRY__"
        with self.assertRaisesRegex(ValueError, "collides"):
            apply_missing_categories(frame, config)


if __name__ == "__main__":
    unittest.main()
