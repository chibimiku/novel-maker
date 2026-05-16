import unittest

from modules.proofread.cli import _build_parser


class CliParserTests(unittest.TestCase):
    def test_parse_find(self):
        parser = _build_parser()
        args = parser.parse_args(
            ["--workspace", "d:/ws", "--config", "d:/cfg.json", "--mode", "find", "--resume"]
        )
        self.assertEqual(args.mode, "find")
        self.assertTrue(args.resume)

    def test_parse_solve_filters(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "--workspace",
                "d:/ws",
                "--config",
                "d:/cfg.json",
                "--mode",
                "solve",
                "--categories",
                "L1,L2,W5",
                "--chapters",
                "第一章,第二章",
            ]
        )
        self.assertEqual(args.mode, "solve")
        self.assertEqual(args.categories, "L1,L2,W5")
        self.assertEqual(args.chapters, "第一章,第二章")


if __name__ == "__main__":
    unittest.main()

