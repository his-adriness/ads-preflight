"""Run the tests of a test file against a server.

`run_suite` connects once, checks that the allowed tools are the kind the file says they
are, then makes each call and checks the result. Only the tools in the file's allowlist can
be called. If the server stops answering partway, the run stops and the remaining tests are
marked "not run": a dead server would fail each of them for the same reason.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from typing import Literal

from ads_preflight.assertions import Failure, evaluate
from ads_preflight.client import HttpTarget, ServerError, ServerSession, connect, connect_http
from ads_preflight.models import CallRecord, ServerInfo
from ads_preflight.testfile import CaseSpec, SuiteSpec, check_access

Status = Literal["passed", "failed", "not_run"]

# Opens a connection to the target that may call only the given tools. Tests replace it.
OpenSession = Callable[
    [Sequence[str] | HttpTarget, Sequence[str], float, dict[str, str] | None],
    AbstractAsyncContextManager[ServerSession],
]


class RunSetupError(Exception):
    """The run cannot start: the allowed tools do not match the server. The message is ready."""


@dataclass(frozen=True)
class CaseResult:
    """What happened to one test."""

    name: str
    tool: str
    status: Status
    failures: tuple[Failure, ...] = ()
    record: CallRecord | None = None  # The call that was made. None if the test was not run.


@dataclass(frozen=True)
class RunResult:
    """What happened in a whole run."""

    server: ServerInfo
    cases: tuple[CaseResult, ...]
    stopped: str | None = None  # Why the run ended early, if it did.

    def count(self, status: Status) -> int:
        return sum(1 for case in self.cases if case.status == status)

    @property
    def ok(self) -> bool:
        """True if the run finished and every test passed."""
        return self.stopped is None and self.count("passed") == len(self.cases)


def _open_session(
    target: Sequence[str] | HttpTarget,
    allowed: Sequence[str],
    timeout: float,
    env: dict[str, str] | None,
) -> AbstractAsyncContextManager[ServerSession]:
    if isinstance(target, HttpTarget):
        return connect_http(
            target.url,
            allowed_tools=allowed,
            connect_timeout=timeout,
            call_timeout=timeout,
            headers=target.headers,
        )
    return connect(
        target, allowed_tools=allowed, connect_timeout=timeout, call_timeout=timeout, env=env
    )


async def run_suite(
    suite: SuiteSpec,
    target: Sequence[str] | HttpTarget,
    *,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    open_session: OpenSession = _open_session,
) -> RunResult:
    """Run every test of `suite` against the server and return the results.

    `timeout` limits the connection and each call, in seconds. Raises ServerError if the
    server cannot be used at all, and RunSetupError if its tools do not match the file.
    A server that stops partway is not an exception: the result says where the run stopped.
    """
    allowed = suite.callable_tools
    result: RunResult | None = None
    try:
        async with open_session(target, allowed, timeout, env) as session:
            problems = check_access(suite, await session.list_tools())
            if problems:
                raise RunSetupError(
                    "the tools in allow_tools do not match the server, so nothing was called:\n"
                    + "\n".join(f"  {problem}" for problem in problems)
                )
            results: list[CaseResult] = []
            stopped: str | None = None
            for case in suite.tests:
                if stopped is not None:
                    results.append(CaseResult(case.name, case.call, "not_run"))
                    continue
                try:
                    results.append(await _run_case(session, case))
                except ServerError as error:
                    stopped = f"stopped at the test {case.name!r}: {error}"
                    results.append(CaseResult(case.name, case.call, "not_run"))
            result = RunResult(server=session.info, cases=tuple(results), stopped=stopped)
    except ServerError as error:
        if result is None:
            raise
        # The tests finished but stopping the server failed. Keep the results.
        result = replace(result, stopped=result.stopped or f"after the last test: {error}")
    return result


async def _run_case(session: ServerSession, case: CaseSpec) -> CaseResult:
    record = await session.call_tool(case.call, case.arguments)
    failures = tuple(evaluate(case.expect, record))
    return CaseResult(case.name, case.call, "failed" if failures else "passed", failures, record)
