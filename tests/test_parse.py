import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from parse_request import RequestError, parse  # noqa: E402

FORM = """### Runner label

{label}

### Concurrent runs

{runs}

### Command

{command}

### Job timeout (minutes)

{timeout}

### Queue timeout (minutes)

{queue}
"""


def body(label="self-hosted", runs="5", command="_No response_", timeout="_No response_", queue="_No response_"):
    return FORM.format(label=label, runs=runs, command=command, timeout=timeout, queue=queue)


class ParseTest(unittest.TestCase):
    def test_defaults(self):
        req = parse(body())
        self.assertEqual(req, {"labels": ["self-hosted"], "count": 5, "timeout": 30, "queue_timeout": 60, "command": ""})

    def test_multiple_labels_and_command_fence(self):
        req = parse(body(label="self-hosted, linux ,x64", command="```bash\nmake test\necho done\n```", timeout="5", queue="0"))
        self.assertEqual(req["labels"], ["self-hosted", "linux", "x64"])
        self.assertEqual(req["command"], "make test\necho done")
        self.assertEqual((req["timeout"], req["queue_timeout"]), (5, 0))

    def test_crlf(self):
        self.assertEqual(parse(body(runs="3").replace("\n", "\r\n"))["count"], 3)

    def test_invalid(self):
        for kwargs in [{"label": "_No response_"}, {"runs": "0"}, {"runs": "257"}, {"runs": "ten"},
                       {"label": "bad label; rm -rf"}, {"timeout": "999"}]:
            with self.subTest(**kwargs), self.assertRaises(RequestError):
                parse(body(**kwargs))


if __name__ == "__main__":
    unittest.main()
