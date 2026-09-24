"""Failures of the machine, as distinct from failures of the material.

A note blocked because the SDK was missing, the network was down, Obsidian was
closed or acli could not be reached is not a note Tiro could not do. It is a
note Tiro could not *reach*. Blocking both the same way — with a hash, so the
note waits for the user to edit it — meant a night with no network left every
tagged note needing a hand edit before anything would retry (ITERATION-2 M4).

So a failure that is the machine's derives from ``MachineFailure``, and the
runner blocks such a note *without* recording its hash. The note then retries
by itself once the machine is back, bounded by the per-note daily attempts cap
that already stops a loop.
"""

from __future__ import annotations


class MachineFailure(Exception):
    """The machine failed, not the material. The note retries by itself."""


#: Exceptions from the standard library that are always the machine: a socket
#: that would not connect, a timeout, a file the OS would not hand over.
MACHINE_BUILTINS = (ConnectionError, TimeoutError, OSError)


def is_machine(exc: BaseException) -> bool:
    return isinstance(exc, (MachineFailure, *MACHINE_BUILTINS))
