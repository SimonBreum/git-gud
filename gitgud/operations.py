import csv
import shutil
import datetime as dt
import email.utils
import json

from git import Repo

from gitgud import actor

from pathlib import Path


class Operator():
    def __init__(self, path, initialize_repo=False):
        self.path = Path(path)
        self.git_path = self.path / '.git'
        self.hooks_path = self.git_path / 'hooks'
        self.gg_path = self.git_path / 'gud'
        self.last_commit_path = self.gg_path / 'last_commit.txt'
        self.commits_path = self.gg_path / 'commits.csv'
        self.level_path = self.gg_path / 'current_level.txt'
        self.progress_path = self.gg_path / 'progress.json'
        self.repo = None

        if initialize_repo:
            self.repo = Repo.init(self.path)

    def add_file_to_index(self, filename):
        self.setup_repo()
        open(self.path / filename, 'w+').close()
        self.repo.index.add([filename])

    def add_and_commit(self, name):
        self.add_file_to_index(name)
        commit = self.repo.index.commit(
            name,
            author=actor,
            committer=actor,
            skip_hooks=True
        )

        return commit

    def clear_tree_and_index(self):
        self.setup_repo()
        for path in self.path.glob('*'):
            if path.is_file():
                path.unlink()

        # Remove all directories except .git
        for path in self.path.iterdir():
            if path != self.git_path:
                shutil.rmtree(path)

        # Easiest way to clear the index is to commit an empty directory
        self.repo.git.add(update=True)
        self.repo.index.commit("Clearing index", skip_hooks=True)

    def shutoff_pager(self):
        self.repo.config_writer().set_value("core", "pager", '').release()

    def destroy_repo(self):
        # Clear all in installation directory
        if self.repo_exists():
            self.clear_tree_and_index()
        # Clear all in .git/ directory except .git/gud
        for path in self.git_path.iterdir():
            if path.is_file():
                path.unlink()
            elif path != self.gg_path:
                shutil.rmtree(path)
        self.repo = None

    def repo_exists(self):
        head_exists = (self.git_path / 'HEAD').exists()
        if head_exists and self.repo is None:
            self.repo = Repo(self.git_path)
        return head_exists

    def setup_repo(self):
        if not self.repo_exists():
            self.repo = Repo.init(self.path)
        return self.repo

    def create_tree(self, commits, head):
        self.setup_repo()

        self.clear_tree_and_index()
        self.repo.git.checkout(self.repo.head.commit)

        branches = self.repo.branches
        for branch in branches:
            self.repo.delete_head(branch, force=True)
        self.repo.delete_tag(*self.repo.tags)

        commit_objects = {}
        counter = len(commits)
        for name, parents, branches, tags in commits:
            committime = dt.datetime.now(dt.timezone.utc).astimezone() \
                    .replace(microsecond=0)
            committime_offset = dt.timedelta(seconds=counter) + \
                committime.utcoffset()
            committime_rfc = email.utils.format_datetime(
                    committime - committime_offset)
            # commit = (name, parents, branches, tags)
            parents = [commit_objects[parent] for parent in parents]
            if parents:
                # TODO GitPython detach head
                self.repo.git.checkout(parents[0])
            if len(parents) < 2:
                # Not a merge
                self.add_file_to_index(name)
                commit_obj = self.repo.index.commit(
                        name,
                        author=actor,
                        committer=actor,
                        author_date=committime_rfc,
                        commit_date=committime_rfc,
                        parent_commits=parents,
                        skip_hooks=True)
            else:
                assert name[0] == 'M'
                int(name[1:])  # Fails if not a number

                # For octopus merges, merge branches one by one
                for parent in parents[1:]:
                    merge_base = self.repo.merge_base(parents[0], parent)
                    self.repo.index.merge_tree(parent, base=merge_base)
                commit_obj = self.repo.index.commit(
                        name,
                        author=actor,
                        committer=actor,
                        author_date=committime_rfc,
                        commit_date=committime_rfc,
                        parent_commits=parents,
                        skip_hooks=True)

            commit_objects[name] = commit_obj
            self.track_commit(name, commit_obj.hexsha)

            for branch in branches:
                self.repo.create_head(branch, self.repo.head.commit)

            for tag in tags:
                self.repo.create_tag(tag, self.repo.head.commit)
            counter = counter - 1

        head_is_commit = True
        for branch in self.repo.branches:
            if branch.name == head:
                branch.checkout()
                head_is_commit = False

        if head_is_commit:
            self.repo.git.checkout(commit_objects[head])

    # Parses commit msg for keywords (e.g. Revert)
    @staticmethod
    def parse_name(commit_msg):
        if "Revert" in commit_msg:
            commit_msg = commit_msg[8:-64]
            commit_msg += '-'
        return commit_msg

    def get_current_tree(self):
        self.setup_repo()
        # Return a json object with the same structure as in level_json

        repo = self.repo

        tree = {
            'branches': {},
            # Ex: 'branch_name': {'target': 'commit_id', 'id': 'branch_name'}
            'tags': {},
            # Ex: 'tag_name': {'target': 'commit_id', 'id': 'tag_name'}
            'commits': {},
            # Ex: '2': {'parents': ['1'], 'id': '1'}
            'HEAD': {}
            # 'target': 'branch_name', 'id': 'HEAD'
        }

        commits = set()
        visited = set()

        for branch in repo.branches:
            commits.add(branch.commit)
            commit_name = branch.commit.hexsha
            tree['branches'][branch.name] = {
                "target": commit_name,
                "id": branch.name
            }

        for tag in repo.tags:
            commit_name = tag.commit.hexsha
            tree['tags'][tag.name] = {
                'target': commit_name,
                'id': tag.name
            }

        while len(commits) > 0:
            cur_commit = commits.pop()
            if cur_commit not in visited:
                for parent in cur_commit.parents:
                    commits.add(parent)
            visited.add(cur_commit)

        while len(visited) > 0:
            cur_commit = visited.pop()
            commit_name = cur_commit.hexsha
            # If revert detected, modifies commit_name; o/w nothing happens
            commit_name = self.parse_name(commit_name)

            parents = []
            for parent in cur_commit.parents:
                parents.append(self.parse_name(parent.hexsha))

            tree['commits'][commit_name] = {
                'parents': parents,
                'id': commit_name
            }

        if repo.head.is_detached:
            target = repo.commit('HEAD').hexsha
        else:
            target = repo.head.ref.name

        tree['HEAD'] = {
            'target': target,
            'id': 'HEAD'
        }
        return tree

    def initialize_progress_file(self, all_skills):
        with open(self.progress_path, 'w') as progress_file:
            progress_data = {}
            for skill in all_skills:
                progress_data.update(
                    {skill.name: {level.name: 'unvisited' for level in skill}}
                )
            json.dump(progress_data, progress_file)

    def read_progress_file(self):
        with open(self.progress_path) as progress_file:
            return json.load(progress_file)

    def update_progress_file(self, data):
        progress_data = self.read_progress_file()
        with open(self.progress_path, 'w') as progress_file:
            progress_data.update(data)
            json.dump(progress_data, progress_file)

    def get_level_progress(self, level):
        progress_data = self.read_progress_file()
        return progress_data[level.skill.name][level.name]

    def mark_level_complete(self, level):
        progress_data = self.read_progress_file()
        if (progress_data[level.skill.name][level.name] != "complete"):
            progress_data[level.skill.name].update(
                {level.name: "complete"}
            )
            self.update_progress_file(progress_data)

    def mark_level_incomplete(self, level):
        progress_data = self.read_progress_file()
        progress_data[level.skill.name].update(
            {level.name: "incomplete"}
        )
        self.update_progress_file(progress_data)

    def read_level_file(self):
        with open(self.level_path) as level_file:
            return level_file.read()

    def write_level(self, level):
        with open(self.level_path, 'w') as skill_file:
            skill_file.write(' '.join([level.skill.name, level.name]))

    def get_last_commit(self):
        with open(self.last_commit_path) as last_commit_file:
            return last_commit_file.read()

    def write_last_commit(self, name):
        with open(self.last_commit_path, 'w+') as last_commit_file:
            last_commit_file.write(name)

    def clear_tracked_commits(self):
        with open(self.commits_path, 'w'):
            pass

    def track_rebase(self, original_hash, rebase_hash):
        rebase_name = None
        with open(self.commits_path, 'r') as commit_file:
            reader = csv.reader(commit_file)
            for name, commit_hash in reader:
                if commit_hash == original_hash:
                    rebase_name = name + "'"
                    break
        if rebase_name is not None:
            self.track_commit(rebase_name, rebase_hash)
        else:
            raise KeyError('Original hash not found')

    def track_commit(self, name, commit_hash):
        with open(self.commits_path, 'a') as commit_file:
            commit_file.write(','.join([name, commit_hash]))
            commit_file.write('\n')

    def get_known_commits(self):
        known_commits = {}
        with open(self.commits_path, 'r') as commit_file:
            reader = csv.reader(commit_file)
            for name, commit_hash in reader:
                known_commits[commit_hash] = name
        return known_commits

    def get_diffs(self, known_commits):
        self.setup_repo()
        diffs = {}
        for commit_hash, commit_name in known_commits.items():
            if commit_name == '1':
                diff = self.repo.git.diff(
                        '4b825dc642cb6eb9a060e54bf8d69288fbee4904',
                        commit_hash)
                anti_diff = self.repo.git.diff(
                        commit_hash,
                        '4b825dc642cb6eb9a060e54bf8d69288fbee4904')
            else:
                diff = self.repo.git.diff(commit_hash + '~', commit_hash)
                anti_diff = self.repo.git.diff(commit_hash, commit_hash + '~')
            diffs[diff] = commit_name + "'"
            diffs[anti_diff] = commit_name + '-'
        return diffs

    def get_copy_mapping(self, non_merges, known_commits):
        diffs = self.get_diffs(known_commits)
        mapping = {}
        for commit_hash in non_merges:
            if commit_hash in known_commits:
                continue
            diff = self.repo.git.diff(commit_hash + '~', commit_hash)
            if diff in diffs:
                mapping[commit_hash] = diffs[diff]

        return mapping


def get_operator(operator_path=None, initialize_repo=False):
    if operator_path:
        return Operator(operator_path, initialize_repo=initialize_repo)
    else:
        for path in (Path.cwd() / "_").parents:
            gg_path = path / '.git' / 'gud'
            if gg_path.is_dir():
                return Operator(path, initialize_repo=initialize_repo)
