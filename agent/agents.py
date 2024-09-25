import sys
import os
from abc import ABC, abstractmethod
from pathlib import Path
import logging

from aider.coders import Coder
from aider.models import Model
from aider.io import InputOutput
from tenacity import retry, wait_exponential


class Agents(ABC):
    def __init__(self, max_iteration: int):
        self.max_iteration = max_iteration

    @abstractmethod
    def run(self) -> None:
        """Start agent"""
        raise NotImplementedError


class AiderAgents(Agents):
    def __init__(self, max_iteration: int, model_name: str):
        super().__init__(max_iteration)
        self.model = Model(model_name)

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=10),
    )
    def run(
        self,
        message: str,
        test_cmd: str,
        lint_cmd: str,
        fnames: list[str],
        log_dir: Path,
    ) -> None:
        """Start aider agent"""
        if test_cmd:
            auto_test = True
        else:
            auto_test = False
        if lint_cmd:
            auto_lint = True
        else:
            auto_lint = False
        log_dir = log_dir.resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        input_history_file = log_dir / ".aider.input.history"
        chat_history_file = log_dir / ".aider.chat.history.md"

        print(
            f"check {os.path.abspath(chat_history_file)} for prompts and lm generations",
            file=sys.stderr,
        )
        # Set up logging
        log_file = log_dir / "aider.log"
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

        # Redirect print statements to the log file
        sys.stdout = open(log_file, "a")
        sys.stderr = open(log_file, "a")

        # Configure httpx logging
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.INFO)
        httpx_logger.propagate = False  # Prevent propagation to root logger
        httpx_handler = logging.FileHandler(log_file)
        httpx_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        httpx_logger.addHandler(httpx_handler)

        io = InputOutput(
            yes=True,
            input_history_file=input_history_file,
            chat_history_file=chat_history_file,
        )
        coder = Coder.create(
            main_model=self.model,
            fnames=fnames,
            auto_lint=auto_lint,
            auto_test=auto_test,
            lint_cmds={"python": lint_cmd},
            test_cmd=test_cmd,
            io=io,
        )
        coder.max_reflection = self.max_iteration
        coder.stream = False

        # Run the agent
        coder.run(message)

        # Close redirected stdout and stderr
        sys.stdout.close()
        sys.stderr.close()
        # Restore original stdout and stderr
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__