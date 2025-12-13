#!/usr/bin/env python3
"""
Database Sync Script for SGLang CI Dashboard

Syncs the local database with GitHub repository (log branch).
Supports both push (upload) and pull (download) operations.

USAGE:
    # Pull database from GitHub
    python sync_database.py pull

    # Push database to GitHub
    python sync_database.py push

    # Force push (overwrite remote)
    python sync_database.py push --force

    # Pull with backup of local database
    python sync_database.py pull --backup
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime


class DatabaseSyncer:
    """Sync dashboard database with GitHub"""

    def __init__(
        self,
        db_path: str,
        github_repo: str = "ROCm/sglang-ci",
        github_token: str = None,
        branch: str = None,
    ):
        """
        Initialize database syncer

        Args:
            db_path: Path to local database file
            github_repo: GitHub repository in owner/repo format
            github_token: GitHub personal access token
            branch: Git branch to use (default: from env GITHUB_DATA_BRANCH or 'log')
        """
        self.db_path = db_path
        self.github_repo = github_repo
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN")

        # Branch for database storage (default: 'log' for backward compatibility)
        # Can be changed to 'data' to separate database from logs
        self.branch = branch or os.environ.get("GITHUB_DATA_BRANCH", "log")

        # Working directory for git operations
        self.work_dir = "/mnt/raid/michael/sglang-ci-data"

        # Database path in repository
        self.repo_db_path = "database/ci_dashboard.db"

    def ensure_repo_clone(self):
        """Ensure we have a clone of the data repository"""
        if not os.path.exists(os.path.join(self.work_dir, ".git")):
            print(f"Cloning repository {self.github_repo}...")

            if self.github_token:
                clone_url = (
                    f"https://{self.github_token}@github.com/{self.github_repo}.git"
                )
            else:
                clone_url = f"https://github.com/{self.github_repo}.git"

            subprocess.run(
                ["git", "clone", clone_url, self.work_dir],
                check=True,
            )
        else:
            print(f"Repository already cloned at {self.work_dir}")

    def pull_database(self, backup: bool = False):
        """
        Pull database from GitHub to local

        Args:
            backup: Create backup of local database before overwriting
        """
        print("Pulling database from GitHub...")

        # Ensure we have the repository
        self.ensure_repo_clone()

        # Change to work directory
        os.chdir(self.work_dir)

        # Checkout configured branch (log or data)
        subprocess.run(["git", "fetch", "origin", self.branch], check=True)
        subprocess.run(["git", "checkout", "-q", self.branch], check=True)

        # Pull latest changes with automatic conflict resolution
        result = subprocess.run(
            ["git", "pull", "--rebase", "--quiet"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print("⚠️  Rebase conflict detected – auto-resolving...")

            # Check if in rebase state
            rebase_merge = os.path.join(self.work_dir, ".git", "rebase-merge")
            rebase_apply = os.path.join(self.work_dir, ".git", "rebase-apply")

            if os.path.exists(rebase_merge) or os.path.exists(rebase_apply):
                # Accept remote version (newer data)
                db_in_repo = os.path.join(self.work_dir, self.repo_db_path)
                if os.path.exists(db_in_repo):
                    subprocess.run(
                        ["git", "checkout", "--theirs", self.repo_db_path],
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "add", self.repo_db_path], capture_output=True
                    )

                # Continue rebase
                subprocess.run(
                    ["git", "-c", "core.editor=true", "rebase", "--continue"],
                    capture_output=True,
                )
                print("✅ Conflict auto-resolved (accepted remote database)")

        # Check if database exists in repo
        repo_db_file = os.path.join(self.work_dir, self.repo_db_path)

        if not os.path.exists(repo_db_file):
            print(f"⚠️  Database not found in repository at {self.repo_db_path}")
            print("    Run 'python sync_database.py push' to upload the database.")
            return

        # Check if local db_path is a symlink pointing to the repo database
        is_symlink_to_repo = False
        if os.path.islink(self.db_path):
            link_target = os.path.realpath(self.db_path)
            repo_real = os.path.realpath(repo_db_file)
            if link_target == repo_real:
                is_symlink_to_repo = True
                print(
                    f"✅ Local database is symlink to repo – already synced via git pull"
                )
                return

        # Backup local database if requested
        if backup and os.path.exists(self.db_path):
            backup_path = (
                f"{self.db_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            print(f"Creating backup: {backup_path}")
            shutil.copy2(self.db_path, backup_path)

        # Copy database from repo to local
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        shutil.copy2(repo_db_file, self.db_path)

        print(f"✅ Database pulled from GitHub to {self.db_path}")

    def push_database(self, force: bool = False):
        """
        Push database from local to GitHub

        Args:
            force: Force push even if remote is newer
        """
        print("Pushing database to GitHub...")

        # Check if local database exists
        if not os.path.exists(self.db_path):
            print(f"❌ Local database not found at {self.db_path}")
            print("   Run data ingestion first: python ingest_data.py")
            sys.exit(1)

        # Ensure we have the repository
        self.ensure_repo_clone()

        # Change to work directory
        os.chdir(self.work_dir)

        # Checkout configured branch (log or data)
        subprocess.run(["git", "fetch", "origin", self.branch], check=True)
        subprocess.run(["git", "checkout", "-q", self.branch], check=True)

        # Pull latest changes (unless force) with conflict resolution
        if not force:
            result = subprocess.run(
                ["git", "pull", "--rebase", "--quiet"],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                print("⚠️  Rebase conflict detected during push – auto-resolving...")

                # Check if in rebase state
                rebase_merge = os.path.join(self.work_dir, ".git", "rebase-merge")
                rebase_apply = os.path.join(self.work_dir, ".git", "rebase-apply")

                if os.path.exists(rebase_merge) or os.path.exists(rebase_apply):
                    # Accept remote version first
                    db_in_repo = os.path.join(self.work_dir, self.repo_db_path)
                    if os.path.exists(db_in_repo):
                        subprocess.run(
                            ["git", "checkout", "--theirs", self.repo_db_path],
                            capture_output=True,
                        )
                        subprocess.run(
                            ["git", "add", self.repo_db_path], capture_output=True
                        )

                    # Continue rebase
                    subprocess.run(
                        ["git", "-c", "core.editor=true", "rebase", "--continue"],
                        capture_output=True,
                    )
                    print("✅ Conflict auto-resolved – will re-apply local changes")

        # Copy database from local to repo (skip if symlink points to repo)
        repo_db_file = os.path.join(self.work_dir, self.repo_db_path)
        os.makedirs(os.path.dirname(repo_db_file), exist_ok=True)

        # Check if local db_path is a symlink pointing to the repo database
        is_symlink_to_repo = False
        if os.path.islink(self.db_path):
            link_target = os.path.realpath(self.db_path)
            repo_real = os.path.realpath(repo_db_file)
            if link_target == repo_real:
                is_symlink_to_repo = True
                print("Local database is symlink to repo – no copy needed")

        if not is_symlink_to_repo:
            shutil.copy2(self.db_path, repo_db_file)

        # Stage the file
        subprocess.run(["git", "add", self.repo_db_path], check=True)

        # Check if there are changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
        )

        if result.returncode == 0:
            print("No changes to database - nothing to push")
            return

        # Commit
        commit_msg = (
            f"Update dashboard database ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
        )

        subprocess.run(
            [
                "git",
                "-c",
                "user.name=ci-bot",
                "-c",
                "user.email=ci-bot@example.com",
                "commit",
                "-m",
                commit_msg,
                "--quiet",
            ],
            check=True,
        )

        # Push
        print(f"Pushing to GitHub ({self.branch} branch)...")

        if self.github_token:
            push_url = f"https://{self.github_token}@github.com/{self.github_repo}.git"
            subprocess.run(
                ["git", "push", push_url, self.branch, "--quiet"],
                check=True,
            )
        else:
            subprocess.run(
                ["git", "push", "origin", self.branch, "--quiet"],
                check=True,
            )

        print(f"✅ Database pushed to GitHub ({self.github_repo} {self.branch} branch)")

    def get_remote_db_info(self):
        """Get information about remote database"""
        self.ensure_repo_clone()

        os.chdir(self.work_dir)

        # Checkout configured branch
        subprocess.run(
            ["git", "fetch", "origin", self.branch], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "checkout", "-q", self.branch], check=True, capture_output=True
        )

        # Get file info
        repo_db_file = os.path.join(self.work_dir, self.repo_db_path)

        if os.path.exists(repo_db_file):
            size = os.path.getsize(repo_db_file)
            mtime = datetime.fromtimestamp(os.path.getmtime(repo_db_file))

            print(f"Remote database:")
            print(f"  Path: {self.repo_db_path}")
            print(f"  Size: {size / 1024:.1f} KB")
            print(f"  Last modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"⚠️  Database not found in repository at {self.repo_db_path}")

    def get_local_db_info(self):
        """Get information about local database"""
        if os.path.exists(self.db_path):
            size = os.path.getsize(self.db_path)
            mtime = datetime.fromtimestamp(os.path.getmtime(self.db_path))

            print(f"Local database:")
            print(f"  Path: {self.db_path}")
            print(f"  Size: {size / 1024:.1f} KB")
            print(f"  Last modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"⚠️  Local database not found at {self.db_path}")


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Sync dashboard database with GitHub",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "action",
        choices=["pull", "push", "info"],
        help="Action to perform (pull: download from GitHub, push: upload to GitHub, info: show database info)",
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "ci_dashboard.db"),
        help="Path to local database file",
    )

    parser.add_argument(
        "--github-repo",
        type=str,
        default=os.environ.get("GITHUB_REPO", "ROCm/sglang-ci"),
        help="GitHub repository in owner/repo format",
    )

    parser.add_argument(
        "--branch",
        type=str,
        default=os.environ.get("GITHUB_DATA_BRANCH", "log"),
        help="Git branch to use for database storage (default: log)",
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create backup before pulling (pull only)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force push without pulling first (push only)",
    )

    args = parser.parse_args()

    # Initialize syncer
    syncer = DatabaseSyncer(
        db_path=args.db_path,
        github_repo=args.github_repo,
        branch=args.branch,
    )

    # Perform action
    if args.action == "pull":
        syncer.pull_database(backup=args.backup)
    elif args.action == "push":
        syncer.push_database(force=args.force)
    elif args.action == "info":
        syncer.get_local_db_info()
        print()
        syncer.get_remote_db_info()


if __name__ == "__main__":
    main()
