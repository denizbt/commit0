import os
import sys
import yaml
import multiprocessing
from tqdm import tqdm
from datasets import load_dataset
from git import Repo
from agent.commit0_utils import (
    args2string,
    create_branch,
    get_message,
    get_target_edit_files,
    get_lint_cmd,
)
from agent.agents import AiderAgents
from typing import Optional, Type
from types import TracebackType
from agent.class_types import AgentConfig
from commit0.harness.constants import SPLIT
from commit0.harness.get_pytest_ids import main as get_tests
from commit0.harness.constants import RUN_AIDER_LOG_DIR, RepoInstance
from commit0.cli import read_commit0_dot_file


class DirContext:
    def __init__(self, d: str):
        self.dir = d
        self.cwd = os.getcwd()

    def __enter__(self):
        os.chdir(self.dir)

    def __exit__(
        self,
        exctype: Optional[Type[BaseException]],
        excinst: Optional[BaseException],
        exctb: Optional[TracebackType],
    ) -> None:
        os.chdir(self.cwd)


def read_yaml_config(config_file: str) -> dict:
    """Read the yaml config from the file."""
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"The config file '{config_file}' does not exist.")
    with open(config_file, "r") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def run_agent_for_repo(
    repo_base_dir: str,
    agent_config: AgentConfig,
    example: RepoInstance,
) -> None:
    """Run Aider for a given repository."""
    # get repo info
    _, repo_name = example["repo"].split("/")

    repo_name = repo_name.lower()
    repo_name = repo_name.replace(".", "-")

    # Call the commit0 get-tests command to retrieve test files
    test_files_str = get_tests(repo_name, verbose=0)
    test_files = sorted(list(set([i.split(":")[0] for i in test_files_str])))

    repo_path = os.path.join(repo_base_dir, repo_name)
    repo_path = os.path.abspath(repo_path)
    try:
        local_repo = Repo(repo_path)
    except Exception:
        raise Exception(
            f"{repo_path} is not a git repo. Check if base_dir is correctly specified."
        )

    if agent_config.agent_name == "aider":
        agent = AiderAgents(agent_config.max_iteration, agent_config.model_name)
    else:
        raise NotImplementedError(
            f"{agent_config.agent_name} is not implemented; please add your implementations in baselines/agents.py."
        )

    run_id = args2string(agent_config)
    print(f"Agent is coding on branch: {run_id}", file=sys.stderr)
    create_branch(local_repo, run_id, example["base_commit"])
    latest_commit = local_repo.commit(run_id)
    # in cases where the latest commit of branch is not commit 0
    # set it back to commit 0
    # TODO: ask user for permission
    if latest_commit.hexsha != example["base_commit"]:
        local_repo.git.reset("--hard", example["base_commit"])
    target_edit_files = get_target_edit_files(repo_path)
    with DirContext(repo_path):
        if agent_config is None:
            raise ValueError("Invalid input")

        if agent_config.run_tests:
            # when unit test feedback is available, iterate over test files
            for test_file in test_files:
                test_cmd = (
                    f"python -m commit0 test {repo_path} {test_file} --branch {run_id}"
                )
                test_file_name = test_file.replace(".py", "").replace("/", "__")
                log_dir = RUN_AIDER_LOG_DIR / "with_tests" / test_file_name
                lint_cmd = get_lint_cmd(local_repo, agent_config.use_lint_info)
                message = get_message(agent_config, repo_path, test_file=test_file)
                agent.run(
                    message,
                    test_cmd,
                    lint_cmd,
                    target_edit_files,
                    log_dir,
                )
        else:
            # when unit test feedback is not available, iterate over target files to edit
            message = get_message(
                agent_config, repo_path, test_dir=example["test"]["test_dir"]
            )
            agent_config_log_file = os.path.abspath(
                RUN_AIDER_LOG_DIR / "no_tests" / ".agent.yaml"
            )
            os.makedirs(os.path.dirname(agent_config_log_file), exist_ok=True)
            # write agent_config to .agent.yaml
            with open(agent_config_log_file, "w") as agent_config_file:
                yaml.dump(agent_config, agent_config_file)

            for f in target_edit_files:
                file_name = f.replace(".py", "").replace("/", "__")
                log_dir = RUN_AIDER_LOG_DIR / "no_tests" / file_name
                lint_cmd = get_lint_cmd(local_repo, agent_config.use_lint_info)
                agent.run(message, "", lint_cmd, [f], log_dir)


def run_agent(agent_config_file: str) -> None:
    """Main function to run Aider for a given repository.

    Will run in parallel for each repo.
    """
    config = read_yaml_config(agent_config_file)

    agent_config = AgentConfig(**config)

    commit0_config = read_commit0_dot_file(".commit0.yaml")

    dataset = load_dataset(
        commit0_config["dataset_name"], split=commit0_config["dataset_split"]
    )
    filtered_dataset = [
        example
        for example in dataset
        if commit0_config["repo_split"] == "all"
        or (
            isinstance(example, dict)
            and "repo" in example
            and isinstance(example["repo"], str)
            and example["repo"].split("/")[-1]
            in SPLIT.get(commit0_config["repo_split"], [])
        )
    ]
    assert len(filtered_dataset) > 0, "No examples available"

    if len(filtered_dataset) > 1:
        sys.stdout = open(os.devnull, "w")

    with tqdm(
        total=len(filtered_dataset), smoothing=0, desc="Running Aider for repos"
    ) as pbar:
        with multiprocessing.Pool(processes=10) as pool:
            results = []

            # Use apply_async to submit jobs and add progress bar updates
            for example in filtered_dataset:
                result = pool.apply_async(
                    run_agent_for_repo,
                    args=(commit0_config["base_dir"], agent_config, example),
                    callback=lambda _: pbar.update(
                        1
                    ),  # Update progress bar on task completion
                )
                results.append(result)

            for result in results:
                result.wait()
