from enum import IntEnum


class ExitCode(IntEnum):
    """Stable process exit codes for machine consumers."""

    SUCCESS = 0
    USAGE = 2
    CONFIGURATION = 3
    POLICY = 4
    OPERATION = 5
    VERIFICATION = 6
    INTERNAL = 70
