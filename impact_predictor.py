
import argparse
import csv
from pathlib import Path
from collections import defaultdict


def normalize_path(path):
    """Normalize repository paths for reliable comparison."""
    path = path.strip().replace("\\", "/").strip("/")
    if path.startswith("src/"):
        path = path[4:]
    return path.lower()


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Required report not found: {path}")

    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_historical_evidence(report_dir):
    rows = read_csv(report_dir / "cochange_report.csv")
    evidence = defaultdict(list)

    for row in rows:
        file_a = normalize_path(row["file_a"])
        file_b = normalize_path(row["file_b"])

        shared = int(row["shared_commits"])
        jaccard = float(row["jaccard_similarity"])
        confidence_ab = float(row["confidence_a_to_b"])
        confidence_ba = float(row["confidence_b_to_a"])

        # Evidence when A is changed and B may be affected.
        evidence[file_a].append({
            "candidate": file_b,
            "confidence": confidence_ab,
            "jaccard": jaccard,
            "shared_commits": shared
        })

        # Evidence when B is changed and A may be affected.
        evidence[file_b].append({
            "candidate": file_a,
            "confidence": confidence_ba,
            "jaccard": jaccard,
            "shared_commits": shared
        })

    return evidence


def load_structural_evidence(report_dir):
    rows = read_csv(report_dir / "structural_dependencies.csv")
    evidence = defaultdict(list)

    for row in rows:
        source = normalize_path(row["source_file"])
        target = normalize_path(row["target_file"])

        dependency_type = row["dependency_type"]

        # If target changes, source may be affected.
        evidence[target].append({
            "candidate": source,
            "dependency_type": dependency_type,
            "details": row["details"],
            "line": row["line"]
        })

    return evidence



def calculate_predictions(changed_files, historical, structural):

    # Identify all files in the current structural dataset.
    current_files = set(structural.keys())

    for items in structural.values():
        for item in items:
            current_files.add(item["candidate"])

    predictions = defaultdict(
        lambda: {
            "historical": 0.0,
            "structural": 0.0,
            "shared_commits": 0,
            "structural_count": 0,
            "reasons": []
        }
    )

    changed_files = {
        normalize_path(path) for path in changed_files
    }

    # Validate changed files.
    for changed in changed_files:
        if changed not in current_files:
            raise ValueError(
                f"Changed file not found in structural dataset: {changed}"
            )

    for changed in changed_files:

        # Historical evidence
        for item in historical.get(changed, []):

            candidate = item["candidate"]

            if candidate in changed_files:
                continue

            # Ignore files absent from current source structure.
            if candidate not in current_files:
                continue

            support = min(item["shared_commits"] / 5.0, 1.0)

            score = (
                0.5 * item["confidence"]
                + 0.5 * item["jaccard"]
            ) * support

            result = predictions[candidate]

            result["historical"] = max(
                result["historical"], score
            )

            result["shared_commits"] = max(
                result["shared_commits"],
                item["shared_commits"]
            )

            result["reasons"].append(
                f"Historically changed with {changed}"
            )

        # Structural evidence
        for item in structural.get(changed, []):

            candidate = item["candidate"]

            if candidate in changed_files:
                continue

            if candidate not in current_files:
                continue

            weights = {
                "function_call": 1.0,
                "from_import": 0.9,
                "submodule_import": 0.9
            }

            weight = weights.get(
                item["dependency_type"], 0.7
            )

            result = predictions[candidate]

            result["structural"] = min(
                1.0,
                result["structural"] + weight
            )

            result["structural_count"] += 1

            result["reasons"].append(
                f"{item['dependency_type']} dependency "
                f"from {candidate} to {changed} "
                f"(line {item['line']})"
            )

    results = []

    for candidate, evidence in predictions.items():

        historical_score = evidence["historical"]
        structural_score = evidence["structural"]

        combined_score = (
            1 - (1 - historical_score)
            * (1 - structural_score)
        )

        if combined_score >= 0.70:
            risk_level = "High"
        elif combined_score >= 0.35:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        results.append({
            "affected_file": candidate,
            "historical_score": round(historical_score, 4),
            "structural_score": round(structural_score, 4),
            "combined_score": round(combined_score, 4),
            "risk_level": risk_level,
            "shared_commits": evidence["shared_commits"],
            "structural_relationships": evidence["structural_count"],
            "evidence": " | ".join(
                sorted(set(evidence["reasons"]))
            )
        })

    return sorted(
        results,
        key=lambda item: item["combined_score"],
        reverse=True
    )

def main():
    parser = argparse.ArgumentParser(
        description="IMPACTLENS Impact Prediction Engine"
    )

    parser.add_argument(
        "--root",
        required=True,
        help="Path to analyzed repository"
    )

    parser.add_argument(
        "--changed",
        nargs="+",
        required=True,
        help="Changed file paths relative to repository source"
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    report_dir = root / "impactlens_reports"

    print("\n====================================")
    print("          IMPACTLENS")
    print("       IMPACT PREDICTION ENGINE")
    print("====================================")

    historical = load_historical_evidence(report_dir)
    structural = load_structural_evidence(report_dir)

    print("[INFO] Historical evidence loaded.")
    print("[INFO] Structural evidence loaded.")

    predictions = calculate_predictions(
        args.changed,
        historical,
        structural
    )

    output_file = report_dir / "impact_predictions.csv"

    fieldnames = [
        "affected_file",
        "historical_score",
        "structural_score",
        "combined_score",
        "risk_level",
        "shared_commits",
        "structural_relationships",
        "evidence"
    ]

    with open(
        output_file, "w", encoding="utf-8", newline=""
    ) as file:
        writer = csv.DictWriter(
            file, fieldnames=fieldnames
        )
        writer.writeheader()
        writer.writerows(predictions)

    print("\n========== PREDICTION RESULTS ==========")
    print("Changed files:", len(args.changed))
    print("Potentially affected files:", len(predictions))

    for index, item in enumerate(predictions[:15], 1):
        print(
            f"\n{index}. {item['affected_file']}"
            f"\n   Combined score: {item['combined_score']}"
            f"\n   Risk level: {item['risk_level']}"
            f"\n   Historical: {item['historical_score']}"
            f"\n   Structural: {item['structural_score']}"
        )

    print("\n[SUCCESS] Impact prediction completed!")
    print("[REPORT]", output_file)
    print("========================================")


if __name__ == "__main__":
    main()
