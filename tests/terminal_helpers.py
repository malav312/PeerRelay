"""PTY automation deliberately demonstrates that a TTY is not human authentication."""

import os
import pty
import subprocess


def decision(command, action, digest):
    master, slave = pty.openpty()
    try:
        os.write(master, (action + " " + digest + "\n").encode())
        return subprocess.run(
            command, stdin=slave, stderr=slave, stdout=subprocess.PIPE, text=True, timeout=10
        )
    finally:
        os.close(master)
        os.close(slave)
