"""Close WP6 from pinned runs, with an additive audit of historical resumes.

No training, test prediction, or rewriting of historical manifests occurs here.
The evidence bundle is private, exclusive-created, and independently verifiable.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import uuid

import numpy as np
import pandas as pd

from src.data.harmonize_workbooks import ROOT, load_json, relative_path, sha256_file
from src.evaluation.metrics import point_metrics
from src.utils.manifest import write_json
from .adapter import ForecastAdapter, ModelData
from .acceptance import MODELS, consumer_digest
from .experiment import PURPOSE, validate_config, verify_experiment
from .search import search


def require(condition, message):
    if not condition:
        raise ValueError(message)


def private_path(root, name):
    path = relative_path(root, name, description="WP6 audit evidence")
    require(path.is_relative_to(root / "artifacts/runs"), "Evidence must stay inside private artifacts/runs")
    return path


def verify_member(root, entry):
    path = private_path(root, entry["manifest_path"])
    require(sha256_file(path) == entry["sha256"], "Pinned member manifest checksum mismatch")
    manifest = verify_experiment(path, root=root)
    require(manifest["model_name"] == entry["model_name"], "Member model label mismatch")
    if "seed" in entry:
        require(manifest["seed"] == entry["seed"], "Member seed label mismatch")
    return path, manifest


def audit_fit(fit, config, train_count, validation_count):
    """Check the entire stopping sequence, not just its final epoch difference."""
    training = fit["training_config"]
    expected = config["training"]
    require(set(training) == set(expected), "Training fields differ from shared protocol")
    for key in expected.keys() - {"learning_rate", "weight_decay", "max_seconds"}:
        require(training[key] == expected[key], "Training rule differs from shared protocol")
    require(0 < training["max_seconds"] <= expected["max_seconds"], "Trial time cap exceeds protocol")
    require(0 <= fit["training_seconds"] <= training["max_seconds"], "Fit exceeded time cap")
    require(fit["test_used"] is False, "Test used in training")
    history = fit["history"]
    require(0 < len(history) == fit["epochs_completed"] <= expected["max_epochs"], "Invalid epoch count")
    best, best_epoch, stale = float("inf"), None, 0
    for i, epoch in enumerate(history, 1):
        require(stale < expected["patience"], "Training continued after patience was exhausted")
        require(epoch["epoch"] == i, "Epoch sequence changed")
        require(epoch["train_samples"] == train_count and epoch["validation_samples"] == validation_count,
                "An epoch lost shared samples")
        rmse = epoch["validation_rmse"]
        require(np.isfinite(rmse) and np.isfinite(epoch["train_mse_scaled"]), "Nonfinite training history")
        if rmse < best - expected["min_delta"]:
            best, best_epoch, stale = rmse, i, 0
        else:
            stale += 1
    require(len(history) == expected["max_epochs"] or stale == expected["patience"], "Fit stopped prematurely")
    require(fit["best_epoch"] == best_epoch and fit["best_validation_rmse"] == best,
            "Best checkpoint does not match epoch selection")
    for field, history_field in (("training_update_seconds", "training_update_seconds"),
                                  ("validation_selection_seconds", "validation_selection_seconds")):
        np.testing.assert_allclose(fit[field], sum(e[history_field] for e in history), rtol=1e-10)
    return {"epochs_completed": len(history), "best_epoch": best_epoch,
            "fit_seconds": fit["training_seconds"]}


def audit_predictions(base, name, seed, architecture, fit, metrics, train, validation, config, *, trial=False):
    restored = ForecastAdapter.load(base / "checkpoint.pt", expected_contract=train.contract, runtime=config["runtime"])
    require(restored.name == name and restored.seed == seed, "Checkpoint identity differs from run")
    require(restored.architecture == architecture and restored.fit_record == fit, "Checkpoint metadata/history mismatch")
    result = restored.predict(validation, batch_size=config["training"]["batch_size"])
    if trial:
        with np.load(base / "validation_predictions.npz", allow_pickle=False) as saved:
            np.testing.assert_array_equal(result["prediction"], saved["prediction"])
            np.testing.assert_array_equal(result["sample_id"], saved["sample_id"])
    else:
        saved = pd.read_csv(base / "predictions.csv", float_precision="round_trip")
        require(len(saved) == len(validation) * train.contract["horizon_weeks"], "Prediction row count mismatch")
        expected_ids = np.repeat(result["sample_id"], train.contract["horizon_weeks"])
        np.testing.assert_array_equal(saved["sample_id"].to_numpy(), expected_ids)
        np.testing.assert_array_equal(saved["prediction"].to_numpy(), result["prediction"].reshape(-1))
        np.testing.assert_array_equal(saved["actual"].to_numpy(), validation.arrays["y"].astype(float).reshape(-1))
        require(set(saved["split"]) == {"validation"}, "Non-validation prediction rows in WP6")
        require(set(saved["model_name"]) == {name} and set(saved["seed"]) == {seed}, "Prediction identity mismatch")
    actual = point_metrics(validation.arrays["y"], result["prediction"], smape_epsilon=config["metrics"]["smape_epsilon"])
    require(set(actual) == set(metrics), "Metric field contract mismatch")
    for key, value in actual.items():
        if isinstance(value, float):
            np.testing.assert_allclose(value, metrics[key], rtol=1e-12, atol=1e-9)
        else:
            require(value == metrics[key], "Metric coverage or definition mismatch")
    np.testing.assert_allclose(actual["rmse"], fit["best_validation_rmse"], rtol=1e-12, atol=1e-9)
    return restored.metadata()["parameter_count"]


def replay_search(config, name, records, recorded):
    """Recreate candidate order and selection without fitting any network."""
    def evaluate(parameters, trial_id, remaining):
        prior = recorded["trials"][trial_id]
        require(parameters == prior["parameters"], "Search candidate parameters/order changed")
        record = records[trial_id]
        return {"validation_rmse": record["metrics"]["rmse"], "validation_mae": record["metrics"]["mae"],
                "parameter_count": record["metadata"]["parameter_count"]}
    replay = search(config["search"], method="moebs" if name == "moebs_transformer" else "random",
                    seed=config["tuning"]["seed"], evaluate=evaluate)
    for key in ("evaluations", "selected_trial_id", "selected_parameters", "pareto_trial_ids"):
        require(replay[key] == recorded[key], "Search replay selection differs from recorded outcome")
    for a, b in zip(replay["trials"], recorded["trials"]):
        for key in ("trial_id", "parameters", "position", "objectives", "status", "objective_split", "test_used"):
            require(a[key] == b[key], "Search trial replay differs from recorded trial")
    return replay["selected_trial_id"]


def audit_resume(original, resumed, config, train, validation, spec):
    """Reconcile byte-copied trials and independently recomputed repeats."""
    require(spec["copied_trial_ids"] == [0, 1, 2] and spec["recomputed_trial_ids"] == [3],
            "Unexpected historical resume mapping")
    original_search, resumed_search = load_json(original / "search.json"), load_json(resumed / "search.json")
    require(load_json(original / "source/configs/models.wp6.v1.json") == config,
            "Original resume source used a different protocol")
    evidence, rows = {}, []
    # Pin every donor file; do not relabel the resumed snapshot as original provenance.
    for file in sorted(original.rglob("*")):
        if file.is_file():
            evidence[file.relative_to(original).as_posix()] = sha256_file(file)
    for i in spec["copied_trial_ids"] + spec["recomputed_trial_ids"]:
        old, new = original / "trials" / f"trial-{i:03d}", resumed / "trials" / f"trial-{i:03d}"
        a, b = load_json(old / "training.json"), load_json(new / "training.json")
        for key in ("architecture", "training", "metadata", "metrics"):
            require(a[key] == b[key], "Resume donor configuration or objective changed")
        audit_fit(a["fit"], config, len(train), len(validation))
        audit_predictions(old, "tft", config["tuning"]["seed"], a["architecture"], a["fit"], a["metrics"],
                          train, validation, config, trial=True)
        with np.load(old / "validation_predictions.npz", allow_pickle=False) as x, np.load(new / "validation_predictions.npz", allow_pickle=False) as y:
            np.testing.assert_array_equal(x["prediction"], y["prediction"])
            np.testing.assert_array_equal(x["sample_id"], y["sample_id"])
        if i in spec["copied_trial_ids"]:
            for f in ("checkpoint.pt", "checkpoint.pt.sha256", "training.json", "validation_predictions.npz"):
                require(sha256_file(old / f) == sha256_file(new / f), "Copied trial bytes differ from donor")
        rows.append({"trial_id": i, "kind": "copied" if i in spec["copied_trial_ids"] else "recomputed_repeat",
                     "original_evaluation_seconds": original_search["trials"][i]["elapsed_seconds"],
                     "resumed_evaluation_seconds": resumed_search["trials"][i]["elapsed_seconds"],
                     "original_fit_seconds": a["fit"]["training_seconds"], "resumed_fit_seconds": b["fit"]["training_seconds"],
                     "predictions_exact": True})
    require(original_search["selected_trial_id"] == resumed_search["selected_trial_id"], "Resume changed selected candidate")
    effective = resumed_search["elapsed_seconds"] + sum(rows[i]["original_evaluation_seconds"] for i in spec["copied_trial_ids"])
    physical = original_search["elapsed_seconds"] + resumed_search["elapsed_seconds"]
    require(effective <= config["search"]["max_seconds"], "Reconciled search exceeds cap")
    return {"status": "passed_with_disclosed_repeat", "original_files_sha256": evidence, "trial_lineage": rows,
            "completed_unique_candidates": len(rows), "completed_physical_fits": len(rows) + len(spec["recomputed_trial_ids"]),
            "resumed_session_search_seconds": resumed_search["elapsed_seconds"],
            "reconciled_selected_search_seconds": effective,
            "original_plus_resumed_search_seconds": physical,
            "recomputed_extra_fit_seconds": sum(rows[i]["resumed_fit_seconds"] for i in spec["recomputed_trial_ids"]),
            "selected_trial_id": resumed_search["selected_trial_id"],
            "old_search_seconds_preserved": True}


def audit_plan(plan, *, root=ROOT):
    root = Path(root).resolve()
    config_path = relative_path(root, plan["training_config"], description="WP6 training config")
    require(sha256_file(config_path) == plan["training_config_sha256"], "Pinned training config changed")
    config = load_json(config_path)
    validate_config(config)
    require(len(plan["tuning_runs"]) == 3 and len(plan["final_runs"]) == 9, "Incomplete cohort grid")
    members = [verify_member(root, e) for e in plan["tuning_runs"] + plan["final_runs"]]
    tuning = {m["model_name"]: (p, m) for p, m in members[:3]}
    require(set(tuning) == set(MODELS) and all(m["wp6"]["phase"] == "tuning" for _, m in members[:3]), "Invalid tuning membership")
    require({(m["model_name"], m["seed"]) for _, m in members[3:]} == {(n, s) for n in MODELS for s in config["final_seeds"]}, "Invalid final model/seed membership")
    bindings, environments, core_versions, data, summaries = set(), set(), {}, {}, []
    for path, m in members:
        w, cfg = m["wp6"], m["effective_config"]
        require(all(cfg.get(k) == v for k, v in config.items()), "Run protocol differs from cohort config")
        require(m["run_id"] == path.parent.name, "Run directory identity mismatch")
        bindings.add((m["split_manifest_id"], w["split_manifest_sha256"], json.dumps(w["data_digests"], sort_keys=True)))
        environments.add(json.dumps(m["environment"], sort_keys=True))
        for name, digest in w["source_code_sha256"].items():
            require(sha256_file(path.parent / "source" / name) == digest, "Source snapshot checksum mismatch")
            if name.startswith("src/") and name != "src/models/experiment.py":
                require(name not in core_versions or core_versions[name] == digest, "Model/data/scoring source differs across cohort")
                core_versions[name] = digest
        name = m["model_name"]
        if name not in data:
            split = relative_path(root, cfg["split_manifest_path"], description="WP6 split")
            data[name] = tuple(ModelData(split, name, part) for part in ("train", "validation"))
        train, validation = data[name]
        require(cfg["data_contract"] == train.contract, "Model data contract mismatch")
        for part, dataset in (("train", train), ("validation", validation)):
            require(consumer_digest(dataset, config["training"]["batch_size"]) == w["data_digests"][part], "Actual shared tensors differ from digest")
        summary = audit_fit(w["training"], config, len(train), len(validation))
        metrics = load_json(path.parent / "metrics.json")
        require(metrics["split"] == "validation", "WP6 metrics must be validation-only")
        parameters = audit_predictions(path.parent, name, m["seed"], cfg["selected_architecture"], w["training"], metrics["global"], train, validation, config)
        summary.update(model_name=name, seed=m["seed"], phase=w["phase"], parameter_count=parameters,
                       inference_seconds=m["timing"]["inference_seconds"])
        summaries.append(summary)
        if w["phase"] == "tuning":
            require(m["seed"] == config["tuning"]["seed"], "Tuning seed differs across models")
            outcome = load_json(path.parent / "search.json")
            n = config["search"]["max_evaluations"]
            require(outcome == w["search"] and outcome["evaluations"] == n and len(outcome["trials"]) == n,
                    "Search record or NFE differs")
            records = []
            for i, trial in enumerate(outcome["trials"]):
                require(trial["trial_id"] == i and trial["status"] == "completed" and not trial["test_used"], "Incomplete/unsafe trial")
                record = load_json(path.parent / "trials" / f"trial-{i:03d}" / "training.json")
                arch, training = dict(config["models"][name]), dict(config["training"])
                for key, value in trial["parameters"].items():
                    (arch if key in arch else training)[key] = value
                require(record["architecture"] == arch, "Trial architecture differs from search parameters")
                require(all(record["training"][k] == v for k, v in training.items() if k != "max_seconds"), "Trial optimizer differs from search")
                require(record["metadata"]["seed"] == m["seed"] and record["metadata"]["runtime"] == config["runtime"], "Trial initialization/runtime mismatch")
                audit_fit(record["fit"], config, len(train), len(validation))
                audit_predictions(path.parent / "trials" / f"trial-{i:03d}", name, m["seed"], arch, record["fit"], record["metrics"], train, validation, config, trial=True)
                records.append(record)
            selected = replay_search(config, name, records, outcome)
            require(w["selected_trial_id"] == selected and cfg["selected_architecture"] == records[selected]["architecture"], "Wrong selected architecture")
            require(cfg["selected_training"] == records[selected]["training"], "Wrong selected optimizer")
            np.testing.assert_allclose(w["total_trial_training_seconds"], sum(r["fit"]["training_seconds"] for r in records), rtol=1e-12)
            require(outcome["elapsed_seconds"] <= config["search"]["max_seconds"], "Search exceeded time cap")
        else:
            require(w["phase"] == "final_training", "Unexpected cohort phase")
            tp, tm = tuning[name]
            require(cfg["tuning_manifest_path"] == tp.relative_to(root).as_posix() and cfg["tuning_manifest_sha256"] == sha256_file(tp), "Final run chose another tuning lineage")
            require(cfg["selected_architecture"] == tm["effective_config"]["selected_architecture"], "Final architecture was not frozen")
            selected_training = dict(config["training"])
            for k in ("learning_rate", "weight_decay"):
                selected_training[k] = tm["effective_config"]["selected_training"][k]
            require(w["training"]["training_config"] == cfg["selected_training"] == selected_training, "Final training parameters were not frozen")
            benchmark = w["inference_benchmark"]
            require(benchmark["sample_count"] == len(validation) and benchmark["repetitions"] == 5 and benchmark["warmup_runs"] == 1, "Inference benchmark cohort mismatch")
            seconds = benchmark["seconds"]
            require(len(seconds) == 5 and all(np.isfinite(s) and s >= 0 for s in seconds), "Invalid inference repetitions")
            require(benchmark["split"] == "validation" and benchmark["include_adapter_conversion"] is True,
                    "Inference measurement scope differs")
            np.testing.assert_allclose([benchmark["median_seconds"], m["timing"]["inference_seconds"]], statistics.median(seconds), rtol=1e-12)
    require(len(bindings) == 1 and len(environments) == 1, "Different data or environment across cohort")
    resume = plan["resume"]
    require(resume["model_name"] == "tft", "Historical resume model mismatch")
    donor = private_path(root, resume["original_directory"])
    resumed = tuning["tft"][0].parent
    for name, digest in core_versions.items():
        require(sha256_file(donor / "source" / name) == digest, "Resume donor model/data source differs")
    resume_audit = audit_resume(donor, resumed, config, *data["tft"], resume)
    excluded, all_fit_seconds = [], {name: 0.0 for name in MODELS}
    physical_directories = [p.parent for p, _ in members[:3]] + [donor]
    for directory in plan["excluded_directories"]:
        base = private_path(root, directory)
        hashes = {p.relative_to(base).as_posix(): sha256_file(p) for p in sorted(base.rglob("*")) if p.is_file()}
        name = next(n for n in MODELS if base.name.startswith(n + "_"))
        selected_base = tuning[name][0].parent
        chosen_records = [p for p in (selected_base / "trials").glob("trial-*/training.json")]
        for p in (base / "trials").glob("trial-*/training.json"):
            record = load_json(p)
            matches = [candidate for candidate in chosen_records
                       if all(load_json(candidate)[k] == record[k] for k in ("architecture", "training", "metadata", "metrics"))]
            require(len(matches) >= 1, "Excluded retry introduced another candidate or outcome")
            with np.load(p.parent / "validation_predictions.npz", allow_pickle=False) as a, np.load(matches[0].parent / "validation_predictions.npz", allow_pickle=False) as b:
                np.testing.assert_array_equal(a["prediction"], b["prediction"])
                np.testing.assert_array_equal(a["sample_id"], b["sample_id"])
        excluded.append({"directory": directory, "files_sha256": hashes,
                         "completed_trial_records": len(list((base / "trials").glob("trial-*/training.json"))),
                         "all_completed_trials_duplicate_cohort_candidates_and_predictions": True})
        physical_directories.append(base)
    # Copied donor trials are storage copies, not additional physical fits.
    physical_counts = {name: 0 for name in MODELS}
    for base in physical_directories:
        name = next(n for n in MODELS if base.name.startswith(n + "_"))
        for p in (base / "trials").glob("trial-*/training.json"):
            i = int(p.parent.name.removeprefix("trial-"))
            if base == resumed and i in resume["copied_trial_ids"]:
                continue
            all_fit_seconds[name] += load_json(p)["fit"]["training_seconds"]
            physical_counts[name] += 1
    resources = {}
    for name, (path, manifest) in tuning.items():
        w = manifest["wp6"]
        resources[name] = {"cohort_completed_candidates": config["search"]["max_evaluations"],
                           "cohort_trial_fit_seconds": w["total_trial_training_seconds"],
                           "cohort_search_seconds": resume_audit["reconciled_selected_search_seconds"] if name == "tft" else w["search_seconds"],
                           "all_recorded_physical_tuning_fits": physical_counts[name],
                           "all_recorded_completed_tuning_fit_seconds": all_fit_seconds[name],
                           "final_fit_seconds": sum(r["fit_seconds"] for r in summaries if r["model_name"] == name and r["phase"] == "final_training")}
    return {"status": "complete_with_disclosed_resource_limitations", "members_verified": len(members),
            "tuning_runs_verified": 3, "final_runs_verified": 9, "trial_checkpoints_verified": 12,
            "test_predictions": 0, "split_manifest_id": members[0][1]["split_manifest_id"],
            "split_manifest_sha256": members[0][1]["wp6"]["split_manifest_sha256"],
            "source_core_sha256": core_versions, "resume_audit": resume_audit,
            "excluded_runs": excluded, "resource_accounting": resources, "runs": summaries,
            "limitations": ["Historical retries have unequal physical compute; equal candidate-selection budgets do not imply equal actual total compute.",
                            "Only recorded completed fits and sessions can be reconciled exactly; unrecorded interrupted work/startup overhead cannot be reconstructed.",
                            "Early failure notes wrongly inferred host termination; later artifacts prove an excluded MoEBS and the original TFT search completed.",
                            "Original TFT runtime/core/data provenance is checked, but the donor session lacks its own completed environment manifest.",
                            "CPU peak memory unavailable; hardware contention/thermal/load state was not continuously measured.",
                            "Validation comprises nine calendar weeks; tuning and final selection reuse validation, and final seed 2026 shares the tuning seed.",
                            "Source annual coverage and retrospective target availability remain WP3/WP4 assumptions."]}


def close(plan_path, *, root=ROOT):
    root, plan_path = Path(root).resolve(), Path(plan_path).resolve()
    plan = load_json(plan_path)
    audit = audit_plan(plan, root=root)
    base = private_path(root, plan["output_root"] + f"/wp6-closure_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}")
    base.mkdir(parents=True, exist_ok=False)
    with (base / "verifier_source.py").open("xb") as snapshot:
        snapshot.write(Path(__file__).read_bytes())
    write_json(base / "plan.snapshot.json", plan)
    write_json(base / "audit.json", audit)
    write_json(base / "cohort.json", {"stage": "WP6_certified_cohort", "purpose": PURPOSE,
               "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "status": audit["status"], "plan_path": (base / "plan.snapshot.json").relative_to(root).as_posix(),
               "plan_sha256": sha256_file(base / "plan.snapshot.json"),
               "audit_path": (base / "audit.json").relative_to(root).as_posix(), "audit_sha256": sha256_file(base / "audit.json"),
               "verifier_sha256": sha256_file(Path(__file__)),
               "verifier_snapshot_path": (base / "verifier_source.py").relative_to(root).as_posix(),
               "tuning_runs": plan["tuning_runs"], "final_runs": plan["final_runs"],
               "test_predictions": 0, "next_stage": "WP7_test_evaluation"})
    print(f"WP6 closure verified: {(base / 'cohort.json').relative_to(root).as_posix()}", flush=True)
    return base / "cohort.json"


def verify(path, *, root=ROOT):
    root, path = Path(root).resolve(), Path(path).resolve()
    require(path.is_relative_to(root / "artifacts/runs"), "Cohort must stay private")
    receipt = load_json(path)
    require(receipt["stage"] == "WP6_certified_cohort" and receipt["purpose"] == PURPOSE and receipt["test_predictions"] == 0, "Invalid closure receipt")
    require(sha256_file(Path(__file__)) == receipt["verifier_sha256"], "Use the pinned closure verifier version")
    require(sha256_file(private_path(root, receipt["verifier_snapshot_path"])) == receipt["verifier_sha256"],
            "Closure verifier snapshot changed")
    pp, ap = private_path(root, receipt["plan_path"]), private_path(root, receipt["audit_path"])
    require(sha256_file(pp) == receipt["plan_sha256"] and sha256_file(ap) == receipt["audit_sha256"], "Closure evidence checksum mismatch")
    plan = load_json(pp)
    require(receipt["tuning_runs"] == plan["tuning_runs"] and receipt["final_runs"] == plan["final_runs"], "Receipt membership differs from plan")
    regenerated = audit_plan(plan, root=root)
    require(regenerated == load_json(ap) and receipt["status"] == regenerated["status"], "Independent audit differs from sealed evidence")
    print("WP6 certified cohort, resume provenance, resource accounting and all checkpoints verified.", flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.plan:
        close(args.plan)
    else:
        verify(args.verify)


if __name__ == "__main__":
    main()
