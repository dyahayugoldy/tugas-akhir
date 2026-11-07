"""Bounded MOEBS adaptation (Wei 2025 eqs. 6-16) and matched random search.

Only the caller's train/validation evaluator is accepted; no dataset/test API.
Mixed parameter decoding, Pareto ranking of pairs, and zero-MP fallback are
explicit SAP adaptations, detailed in docs/wp5-models.md.
"""

import copy
import time

import numpy as np


def dominates(a, b):
    return bool(np.all(np.asarray(a) <= b) and np.any(np.asarray(a) < b))


def grid_cells(archive, bins):
    values = np.asarray([entry["objectives"] for entry in archive], dtype=float)
    lower, upper = values.min(0), values.max(0)
    span = np.where(upper > lower, upper - lower, 1.0)
    cells = np.minimum(((values - lower) / span * bins).astype(int), bins - 1)
    return [tuple(cell) for cell in cells]


def update_archive(archive, entry, capacity, bins, rng):
    if any(dominates(old["objectives"], entry["objectives"]) for old in archive):
        return archive
    result = [old for old in archive if not dominates(entry["objectives"], old["objectives"])] + [entry]
    while len(result) > capacity:
        cells = grid_cells(result, bins)
        counts = {cell: cells.count(cell) for cell in cells}
        crowded = [i for i, cell in enumerate(cells) if counts[cell] == max(counts.values())]
        result.pop(int(rng.choice(crowded)))
    return result


def choose_leader(archive, bins, rng):
    cells = grid_cells(archive, bins)
    # P(cell) proportional to 1/occupancy. Sample members with 1/count^2.
    weights = np.asarray([1 / cells.count(cell) ** 2 for cell in cells])
    return archive[int(rng.choice(len(archive), p=weights / weights.sum()))]


def decode(position, space):
    values = {}
    for unit, (key, spec) in zip(position, space.items()):
        if spec["type"] == "choice":
            values[key] = spec["values"][min(int(unit * len(spec["values"])), len(spec["values"]) - 1)]
        elif spec["type"] == "log_float":
            values[key] = float(np.exp(np.log(spec["low"]) + unit * np.log(spec["high"] / spec["low"])))
        else:
            raise ValueError("Unsupported search-space type")
    return values


def validate_search(config):
    if config["objective_names"] != ["validation_rmse", "validation_mae", "parameter_count"]:
        raise ValueError("Search objectives must use validation errors and parameter cost only")
    if config["selection"] != "lexicographic_validation_rmse_mae_parameter_count_trial_id":
        raise ValueError("Unsupported candidate selection rule")
    if set(config["space"]) != {"d_model", "n_heads", "learning_rate", "weight_decay"}:
        raise ValueError("Search space may only alter declared architecture/optimizer fields")
    for key, spec in config["space"].items():
        if spec["type"] == "choice":
            if not spec["values"] or any(type(v) is not int or v <= 0 for v in spec["values"]):
                raise ValueError("Search choices must be positive integers")
        elif spec["type"] != "log_float" or not 0 < spec["low"] < spec["high"] or not np.isfinite(spec["high"]):
            raise ValueError("Invalid bounded log search space")
    if any(d % h or d % 2 for d in config["space"]["d_model"]["values"] for h in config["space"]["n_heads"]["values"]):
        raise ValueError("All search candidates must have valid attention dimensions")
    moebs = config["moebs"]
    for key in ("population_size", "max_iterations", "archive_size", "grid_bins"):
        if type(moebs[key]) is not int or moebs[key] < (2 if key in ("population_size", "grid_bins") else 1):
            raise ValueError("Invalid MOEBS population/archive/grid/iteration budget")
    if moebs["epsilon"] <= 0 or moebs["beta_values"] != [0, 2]:
        raise ValueError("Invalid MOEBS numerical controls")
    if type(config["max_evaluations"]) is not int or config["max_evaluations"] < moebs["population_size"] or config["max_seconds"] <= 0:
        raise ValueError("Invalid optimization budget")
    if config["other_models"] != "random_search_same_evaluations_epochs_and_wall_clock_caps":
        raise ValueError("Matched random search policy is required")


def search(config, *, method, seed, evaluate, on_record=None):
    """evaluate(parameters, trial_id, remaining_seconds) -> objective dict.

    Objective dict must contain exactly config.objective_names. Every attempt
    counts toward NFE, including timeout/numerical failures. The evaluator must
    honour the remaining cooperative wall-clock budget.
    """
    validate_search(config)
    if method not in ("moebs", "random"):
        raise ValueError("Unknown optimization method")
    rng, started = np.random.default_rng(seed), time.perf_counter()
    settings, records, archive = config["moebs"], [], []
    dimensions = len(config["space"])

    def allowed():
        return len(records) < config["max_evaluations"] and time.perf_counter() - started < config["max_seconds"]

    def trial(position, maneuver):
        nonlocal archive
        if not allowed():
            return None
        position = np.clip(position, 0.0, 1.0)
        number = len(records)
        parameters = decode(position, config["space"])
        entry = {"trial_id": number, "position": position.tolist(), "parameters": parameters,
                 "maneuver": maneuver, "seed": seed, "objective_split": "validation", "test_used": False}
        trial_started = time.perf_counter()
        try:
            objectives = evaluate(copy.deepcopy(parameters), number, config["max_seconds"] - (trial_started - started))
            if set(objectives) != set(config["objective_names"]):
                raise ValueError("Evaluator returned unsupported objectives")
            values = [float(objectives[key]) for key in config["objective_names"]]
            if not np.isfinite(values).all():
                raise ValueError("Nonfinite objective")
            entry.update(status="completed", objectives=values)
        except (TimeoutError, FloatingPointError) as error:
            entry.update(status="failed", error_type=type(error).__name__, objectives=None)
        entry["elapsed_seconds"] = time.perf_counter() - trial_started
        records.append(entry)
        if entry["status"] == "completed":
            archive = update_archive(archive, entry, settings["archive_size"], settings["grid_bins"], rng)
        if on_record:
            on_record(copy.deepcopy(entry))
        return entry

    if method == "random":
        while allowed():
            trial(rng.random(dimensions), "random")
    else:
        population = [trial(rng.random(dimensions), "initial") for _ in range(settings["population_size"]) if allowed()]
        population = [entry for entry in population if entry and entry["status"] == "completed"]
        velocities = {entry["trial_id"]: np.zeros(dimensions) for entry in population}
        for _ in range(settings["max_iterations"]):
            if len(population) < 2 or not allowed():
                break
            for i in range(len(population)):
                if not allowed():
                    break
                j = int(rng.choice([k for k in range(len(population)) if k != i]))
                # Non-domination count supplies a scalar body cost for eq. 7.
                costs = np.asarray([sum(dominates(b["objectives"], a["objectives"]) for b in population) for a in population], float)
                if costs[i] < costs[j] or (costs[i] == costs[j] and rng.random() < 0.5):
                    ab, eb = i, j
                else:
                    ab, eb = j, i
                body = (costs.max() - costs) / (np.ptp(costs) + settings["epsilon"])
                # Equal ranks have no scalar preference. Give equal body factors.
                if np.ptp(costs) == 0:
                    body[:] = 1.0
                beta = int(rng.choice(settings["beta_values"]))
                powers = [body[k] * float(np.linalg.norm(velocities[population[k]["trial_id"]])) ** beta for k in (ab, eb)]
                er = powers[1] / sum(powers) if sum(powers) > settings["epsilon"] else 0.5
                cr = 1.0 - er
                xab, xeb = (np.asarray(population[k]["position"]) for k in (ab, eb))
                leader = np.asarray(choose_leader(archive, settings["grid_bins"], rng)["position"])
                predator = xab + rng.random() * cr * (xeb - xab) + rng.random() * cr * (leader - xeb)
                prey = rng.random(dimensions) if er < 1 / len(population) else xeb + rng.random() * er * (1 - xab - xeb)
                for k, proposal, move in ((ab, predator, "predator"), (eb, prey, "horizontal" if er < 1 / len(population) else "vertical")):
                    candidate = trial(proposal, move)
                    if candidate is None or candidate["status"] != "completed":
                        continue
                    old = population[k]
                    if dominates(candidate["objectives"], old["objectives"]) or (not dominates(old["objectives"], candidate["objectives"]) and rng.random() < 0.5):
                        velocities[candidate["trial_id"]] = np.asarray(candidate["position"]) - np.asarray(old["position"])
                        population[k] = candidate
    if not archive:
        raise RuntimeError("Search produced no completed candidate within resource caps")
    # Final selection across all non-dominated evaluated solutions, not just a
    # capacity-pruned archive (which can remove the primary RMSE optimum).
    completed = [entry for entry in records if entry["status"] == "completed"]
    pareto = [a for a in completed if not any(dominates(b["objectives"], a["objectives"]) for b in completed)]
    selected = min(pareto, key=lambda entry: (*entry["objectives"], entry["trial_id"]))
    reason = "max_evaluations" if len(records) >= config["max_evaluations"] else "max_seconds" if not allowed() else "max_iterations"
    return {"method": method, "config": copy.deepcopy(config), "seed": seed, "trials": records,
            "archive_trial_ids": [a["trial_id"] for a in archive], "pareto_trial_ids": [a["trial_id"] for a in pareto],
            "selected_trial_id": selected["trial_id"], "selected_parameters": selected["parameters"],
            "evaluations": len(records), "elapsed_seconds": time.perf_counter() - started,
            "stop_reason": reason, "test_used": False}
