
import argparse
import csv
import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path


class ChangeAnalyzer:

    def __init__(self, repository_path):
        self.root = Path(repository_path).resolve()
        self.file_commit_counts = Counter()
        self.cochange_counts = Counter()
        self.total_commits = 0

    def get_git_history(self):
        """Read commit history and files changed in each commit."""

        command = [
            "git",
            "-C", str(self.root),
            "log",
            "--format=%x1e%H",
            "--name-only",
            "--no-renames"
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True
            )

        except FileNotFoundError:
            raise RuntimeError("Git is not installed or not in PATH.")

        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"Git history could not be read: {error.stderr}"
            )

        commits = []

        for block in result.stdout.split("\x1e"):

            lines = block.strip().splitlines()

            if not lines:
                continue

            commit_hash = lines[0].strip()

            changed_files = {
                line.strip().replace("\\", "/")
                for line in lines[1:]
                if line.strip()
            }

            if changed_files:
                commits.append({
                    "commit": commit_hash,
                    "files": changed_files
                })

        return commits

    def analyze(self):
        """Calculate historical file relationships."""

        commits = self.get_git_history()

        self.total_commits = len(commits)

        if not commits:
            print("[WARNING] No commits with changed files found.")
            return

        for commit in commits:

            files = sorted(commit["files"])

            # Count commits affecting each file.
            for file in files:
                self.file_commit_counts[file] += 1

            # Count files appearing together in a commit.
            for file_a, file_b in combinations(files, 2):
                self.cochange_counts[
                    (file_a, file_b)
                ] += 1

        print(f"[INFO] Analyzed {self.total_commits} commits.")
        print(f"[INFO] Found {len(self.file_commit_counts)} files.")
        print(f"[INFO] Found {len(self.cochange_counts)} file pairs.")

    def generate_report(self):
        """Generate a CSV report with relationship metrics."""

        output_dir = self.root / "impactlens_reports"
        output_dir.mkdir(parents=True, exist_ok=True)

        report_path = output_dir / "cochange_report.csv"

        rows = []

        for (file_a, file_b), shared in self.cochange_counts.items():

            count_a = self.file_commit_counts[file_a]
            count_b = self.file_commit_counts[file_b]

            confidence_a = shared / count_a if count_a else 0
            confidence_b = shared / count_b if count_b else 0

            union = count_a + count_b - shared

            jaccard = shared / union if union else 0

            rows.append({
                "file_a": file_a,
                "file_b": file_b,
                "commits_file_a": count_a,
                "commits_file_b": count_b,
                "shared_commits": shared,
                "confidence_a_to_b": round(confidence_a, 4),
                "confidence_b_to_a": round(confidence_b, 4),
                "jaccard_similarity": round(jaccard, 4)
            })

        rows.sort(
            key=lambda row: (
                row["jaccard_similarity"],
                row["shared_commits"]
            ),
            reverse=True
        )

        headers = [
            "file_a",
            "file_b",
            "commits_file_a",
            "commits_file_b",
            "shared_commits",
            "confidence_a_to_b",
            "confidence_b_to_a",
            "jaccard_similarity"
        ]

        with open(
            report_path,
            "w",
            newline="",
            encoding="utf-8-sig"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=headers
            )

            writer.writeheader()
            writer.writerows(rows)

        print("\n[SUCCESS] Historical analysis completed!")
        print(f"[REPORT] {report_path}")
        print(f"[RESULT] Total file pairs: {len(rows)}")

        if rows:
            print("\nTop 5 historical relationships:")

            for row in rows[:5]:
                print(
                    f"{row['file_a']} <-> {row['file_b']} "
                    f"| Shared: {row['shared_commits']} "
                    f"| Jaccard: {row['jaccard_similarity']}"
                )

    def run(self):

        print("\n====================================")
        print("       IMPACTLENS")
        print("   HISTORICAL CHANGE ANALYZER")
        print("====================================\n")

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"Repository not found: {self.root}"
            )

        # Verify that the directory is a Git repository.
        result = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                "The selected directory is not a Git repository."
            )

        self.analyze()
        self.generate_report()


def main():

    parser = argparse.ArgumentParser(
        description="IMPACTLENS Historical Change Analyzer"
    )

    parser.add_argument(
        "--root",
        default=".",
        help="Path to the Git repository"
    )

    args = parser.parse_args()

    analyzer = ChangeAnalyzer(args.root)
    analyzer.run()


if __name__ == "__main__":
    main()
