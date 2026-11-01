"""One bounded data job. Stdout is strictly JSON; vendor progress stays captured."""
import contextlib
import io
import json
import sys

from core.data import worker_fetch

if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = worker_fetch(sys.argv[1], json.loads(sys.argv[2]))
    except Exception as ex:
        result = {"error": type(ex).__name__ + ": " + str(ex)[:250]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
